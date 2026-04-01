# channels/odoo.py
"""Odoo 16 channel implementation using async XML-RPC."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from loguru import logger
from aioxmlrpc.client import ServerProxy
import markdown
from traceback import format_exc

from .base import Channel, InboundMessage
from .. import config


class OdooChannel(Channel):
    """Odoo 16 channel using async XML-RPC client."""

    def __init__(
        self,
        on_message,
        on_chat_metadata,
        name: str = "odoo",
        registered_groups=None,
        url: Optional[str] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        message_limit: int = 4096,
        poll_interval: int = 5000,  # milliseconds
        session_dir: Optional[Path] = None,
        max_retries: int = 3,
        retry_delay: int = 5  # seconds
    ):
        super().__init__(on_message, on_chat_metadata, name, registered_groups=registered_groups)
        
        # Connection settings
        self.url = url or config.ODOO_URL
        self.database = database or config.ODOO_DATABASE
        self.username = username or config.ODOO_USERNAME
        self.password = password or config.ODOO_PASSWORD
        self.message_limit = message_limit
        self.poll_interval = poll_interval / 1000  # Convert to seconds
        self.session_dir = Path(session_dir or config.ODOO_SESSION_DIR)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Async RPC clients
        self.common = None
        self.object = None
        self.models = None
        
        # User and session info
        self.user_id: Optional[int] = None
        self.uid: Optional[int] = None  # Alias for user_id
        self.session_id: Optional[str] = None
        self.user_context: Optional[Dict] = None
        self.partner_id: Optional[int] = None
        self.company_id: Optional[int] = None
        
        # Channel state
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._last_message_ids: Dict[str, int] = {}  # model -> last message ID
        self._known_channels: Dict[str, Dict[str, Any]] = {}  # jid -> channel info
        
        # Rate limiting
        self._request_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests
        self._last_request_time = 0
        self._min_request_interval = 0.5  # 500ms between requests

    async def connect(self) -> None:
        """Connect to Odoo via async XML-RPC."""
        retry_count = 0
        
        while retry_count < self.max_retries:
            try:
                logger.info(f"Connecting to Odoo at {self.url}...")
                
                # Create session directory
                self.session_dir.mkdir(parents=True, exist_ok=True)
                
                # Load saved session if exists
                await self._load_session()
                
                # Create async RPC clients
                self.common = ServerProxy(f'{self.url}/xmlrpc/2/common')
                self.object = ServerProxy(f'{self.url}/xmlrpc/2/object')
                
                # Authenticate
                await self._authenticate()
                
                # Initialize models proxy
                self.models = self._create_models_proxy()
                
                # Get user info
                await self._get_user_info()
                
                # Load last seen messages
                await self._load_last_message_ids()
                
                # Load initial channels
                await self._load_channels()
                
                self._connected = True
                self._running = True
                logger.info(f"Odoo connected successfully as user {self.username} (ID: {self.uid})")
                
                # Save session
                await self._save_session()
                
                # Start polling loop
                self._poll_task = asyncio.create_task(self._poll_loop())
                
                return
                
            except Exception as e:
                retry_count += 1
                logger.error(f"Failed to connect to Odoo (attempt {retry_count}/{self.max_retries}): {e}")
                
                if retry_count < self.max_retries:
                    logger.info(f"Retrying in {self.retry_delay} seconds...")
                    await asyncio.sleep(self.retry_delay)
                else:
                    logger.error("Max retries reached, giving up")
                    await self.disconnect()
                    raise

    async def disconnect(self) -> None:
        """Disconnect from Odoo."""
        self._running = False
        self._connected = False
        
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        
        # Close client sessions
        if self.common:
            await self.common.close()
        if self.object:
            await self.object.close()
        
        # Save state
        await self._save_last_message_ids()
        await self._save_session()
        
        logger.info("Odoo disconnected")

    def owns_jid(self, jid: str) -> bool:
        """Check if JID belongs to Odoo.
        
        JID format: odoo:{model}:{id}
        Examples:
            odoo:mail.channel:5
            odoo:res.users:2
            odoo:mail.box:inbox
        """
        return jid.startswith(f"{self.name}:") and ':' in jid[len(self.name) + 1:]

    async def send_message(self, jid: str, text: str) -> bool:
        """Send message to Odoo discussion."""
        if not self._connected or not self.models:
            logger.error("Odoo not connected")
            return False
        
        async with self._request_semaphore:
            await self._rate_limit()
            
            try:
                text = markdown.markdown(
                        text,
                        extensions=['fenced_code', 'tables', 'codehilite']
                        )
                # Parse JID: odoo:{model}:{id}
                parts = jid.split(':')
                if len(parts) != 3:
                    logger.error(f"Invalid Odoo JID format: {jid}")
                    return False
                
                _, model, model_id = parts
                
                if model == 'mail.channel':
                    return await self._send_channel_message(int(model_id), text)
                elif model == 'res.users' or model == 'res.partner':
                    return await self._send_private_message(int(model_id), text)
                else:
                    logger.error(f"Unsupported model for sending: {model}")
                    return False
                    
            except Exception as e:
                logger.error(f"Failed to send Odoo message: {e}")
                return False

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Set typing indicator in Odoo."""
        if not self._connected or not self.models:
            return
        
        async with self._request_semaphore:
            try:
                parts = jid.split(':')
                if len(parts) != 3:
                    return
                
                _, model, model_id = parts
                
                if model == 'mail.channel':
                    await self._send_typing_notification(int(model_id), is_typing)
                    
            except Exception as e:
                logger.debug(f"Failed to set typing indicator: {e}")

    async def mark_as_read(self, jid: str, message_id: str) -> None:
        """Mark message as read in Odoo."""
        if not self._connected or not self.models:
            return
        
        async with self._request_semaphore:
            try:
                parts = jid.split(':')
                if len(parts) != 3:
                    return
                
                _, model, model_id = parts
                
                if model == 'mail.channel':
                    # Mark channel message as read
                    await self.models.execute_kw(
                        self.database,
                        self.uid,
                        self.password,
                        'mail.channel',
                        'message_read',
                        [[int(message_id)], {'channel_id': int(model_id)}]
                    )
                    
            except Exception as e:
                logger.debug(f"Failed to mark message as read: {e}")

    async def _rate_limit(self) -> None:
        """Rate limiting to avoid overwhelming the server."""
        now = asyncio.get_event_loop().time()
        time_since_last = now - self._last_request_time
        if time_since_last < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - time_since_last)
        self._last_request_time = now

    async def _authenticate(self) -> None:
        """Authenticate with Odoo."""
        try:
            # Check version
            version = await self.common.version()
            logger.debug(f"Odoo version: {version.get('server_version')}")
            
            # Authenticate
            self.uid = await self.common.authenticate(
                self.database,
                self.username,
                self.password,
                {}
            )
            self.user_id = self.uid  # Set alias
            
            if not self.uid:
                raise Exception("Authentication failed")
            
            logger.info(f"Authenticated as user ID: {self.uid}")
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise

    def _create_models_proxy(self):
        """Create a wrapper for models.execute_kw."""
        class ModelsProxy:
            def __init__(self, parent):
                self.parent = parent
            
            async def execute_kw(self, db, uid, password, model, method, args, kwargs=None):
                if kwargs is None:
                    kwargs = {}
                return await self.parent.object.execute_kw(
                    db, uid, password, model, method, args, kwargs
                )
        
        return ModelsProxy(self)

    async def _get_user_info(self) -> None:
        """Get current user information."""
        try:
            # Get user context
            self.user_context = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'res.users',
                'context_get',
                []
            )
            
            # Get user details
            user_data = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'res.users',
                'read',
                [[self.uid], ['name', 'email', 'partner_id', 'company_id']]
            )
            
            if user_data:
                user_info = user_data[0]
                logger.info(f"Logged in as: {user_info.get('name')}")
                self.partner_id = user_info.get('partner_id', [0])[0] if user_info.get('partner_id') else None
                self.company_id = user_info.get('company_id', [0])[0] if user_info.get('company_id') else None
                
        except Exception as e:
            logger.warning(f"Failed to get user info: {e}")

    async def _load_channels(self) -> None:
        """Load initial channels."""
        try:
            # Get channels the user is a member of
            channels = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.channel',
                'search_read',
                [[['channel_type', 'in', ['channel', 'group']]],
                 ['id', 'name', 'description', 'channel_type']]
            )
            
            for channel in channels:
                channel_id = channel['id']
                channel_name = channel.get('name', f'Channel {channel_id}')
                channel_type = channel.get('channel_type', 'channel')
                
                jid = self.create_jid(f"mail.channel:{channel_id}")
                
                # Update metadata
                await self.on_chat_metadata(
                    jid,
                    datetime.now().isoformat(),
                    channel_name,
                    self.name,
                    True,  # is_group
                )
                
                self._known_channels[jid] = {
                    'id': channel_id,
                    'name': channel_name,
                    'type': channel_type
                }
            
            logger.info(f"Loaded {len(channels)} channels:{self._known_channels}")
            
        except Exception as e:
            logger.error(f"Failed to load channels: {e}")

    async def _poll_loop(self) -> None:
        """Main polling loop for new messages."""
        logger.info(f"Starting Odoo poll loop (interval: {self.poll_interval}s)")
        
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self._running:
            try:
                async with self._request_semaphore:
                    await self._rate_limit()
                    
                    # Check for new messages in channels
                    await self._check_channels()
                    
                    # Check for new private messages
                    await self._check_private_messages()
                    
                    # Check for mentions
                    await self._check_mentions()
                
                consecutive_errors = 0  # Reset on success
                await asyncio.sleep(self.poll_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in Odoo poll loop ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error("Too many consecutive errors, stopping poll loop")
                    break
                
                # Exponential backoff
                backoff = min(30, self.poll_interval * (2 ** consecutive_errors))
                await asyncio.sleep(backoff)

    async def _process_inbound_message(self, msg: InboundMessage) -> None:
        res = await super()._process_inbound_message(msg)
        await self._save_last_message_ids()

        return res

    async def _check_channels(self) -> None:
        """Check for new messages in channels."""
        try:
            # Get channels with recent messages
            channels = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.channel',
                'search_read',
                [[
                    ['channel_type', 'in', ['channel', 'group', 'chat']],
                    ['message_ids', '!=', False]
                ]],
                {
                    'fields': ['id', 'name', 'message_ids', 'channel_type'],
                    'limit': 50  # Limit to prevent overload
                }
            )
            
            for channel in channels:
                channel_id = channel['id']
                channel_name = channel.get('name', f'Channel {channel_id}')
                channel_type = channel['channel_type']
                
                # Get last message ID for this channel
                last_id = self._last_message_ids.get(f'mail.channel:{channel_id}', 0)
                
                # Get new messages
                if channel.get('message_ids'):
                    message_ids = channel['message_ids']
                    new_ids = [mid for mid in message_ids if mid > last_id]
                    
                    if new_ids:
                        # Limit to last 10 new messages to prevent overload
                        new_ids = new_ids[-10:]
                        await self._process_channel_messages(channel_id, channel_name, channel_type, new_ids)
                        
        except Exception as e:
            logger.error(f"Error checking channels: {e}")

    async def _check_private_messages(self) -> None:
        """Check for new private messages."""
        try:
            # Get recent private messages
            messages = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.message',
                'search_read',
                [[
                    ['message_type', '=', 'comment'],
                    ['model', '=', 'res.partner'],
                    ['res_id', '=', self.partner_id] if self.partner_id else [],
                    ['id', '>', self._last_message_ids.get('private', 0)]
                ]],
                {
                    'fields': ['id', 'body', 'author_id', 'date', 'subject'],
                    'limit': 50,
                    'order': 'id ASC'
                }
            )
            
            for msg in messages:
                msg_id = msg['id']
                body = msg.get('body', '')
                author = msg.get('author_id', [0, ''])[1] if msg.get('author_id') else 'Unknown'
                
                # Skip messages from self
                if author == self.username:
                    continue
                
                # Get partner info
                partner_id = msg.get('author_id', [0])[0] if msg.get('author_id') else 0
                
                if partner_id:
                    jid = self.create_jid(f"res.partner:{partner_id}")
                    chat_id = f"res.partner:{partner_id}"
                    
                    # Get partner name
                    partner_name = author
                    
                    message = InboundMessage(
                        id=f"mail.message:{msg_id}",
                        chat_id=jid,
                        chat_name=partner_name,
                        sender_id=str(partner_id),
                        sender_name=author,
                        content=body,
                        timestamp=datetime.fromisoformat(msg['date'].replace('Z', '+00:00')),
                        is_from_me=False,
                        is_group=False,
                        raw_data=msg
                    )
                    
                    await self._process_inbound_message(message)
            
            if messages:
                self._last_message_ids['private'] = max(m['id'] for m in messages)
                
        except Exception as e:
            logger.error(f"Error checking private messages: {e}")

    async def _check_mentions(self) -> None:
        """Check for mentions in messages."""
        try:
            # Search for messages mentioning the current user
            mentions = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.message',
                'search_read',
                [[
                    ['partner_ids', 'in', [self.partner_id]] if self.partner_id else [],
                    ['message_type', '=', 'comment'],
                    ['author_id', '!=', self.partner_id] if self.partner_id else [],
                    ['id', '>', self._last_message_ids.get('mentions', 0)],
                    ['model', '!=', 'mail.channel']
                ]],
                {
                    'fields': ['id', 'subject', 'body', 'author_id', 'model', 'res_id', 'date'],
                    'limit': 50
                }
            )
            
            for msg in mentions:
                msg_id = msg['id']
                subject = msg.get('subject', '')
                body = msg.get('body', '')
                author = msg.get('author_id', [0, ''])[1] if msg.get('author_id') else 'Unknown'
                model = msg.get('model', '')
                res_id = msg.get('res_id', 0)
                
                # Create JID based on context
                if model == 'mail.channel' and res_id:
                    jid = self.create_jid(f"mail.channel:{res_id}")
                    chat_id = f"mail.channel:{res_id}"
                    chat_name = f"Channel {res_id}"
                else:
                    jid = self.create_jid(f"mention:{msg_id}")
                    chat_id = f"mention:{msg_id}"
                    chat_name = f"Mention in {model}"
                
                # Create message
                message = InboundMessage(
                    id=f"mention:{msg_id}",
                    chat_id=jid,
                    chat_name=chat_name,
                    sender_id=str(author),
                    sender_name=author,
                    content=f"{subject}\n\n{body}" if subject else body,
                    timestamp=datetime.fromisoformat(msg['date'].replace('Z', '+00:00')),
                    is_from_me=False,
                    is_group=True,
                    raw_data=msg
                )
                
                await self._process_inbound_message(message)
                
            if mentions:
                self._last_message_ids['mentions'] = max(m['id'] for m in mentions)
                
        except Exception as e:
            logger.error(f"Error checking mentions: {e}")

    async def _process_channel_messages(self, channel_id: int, channel_name: str, channel_type: str, message_ids: List[int]) -> None:
        """Process messages from a channel."""
        try:
            # Get message details
            messages = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.message',
                'read',
                [message_ids, ['id', 'subject', 'body', 'author_id', 'date', 'message_type']]
            )
            
            jid = self.create_jid(f"mail.channel:{channel_id}")
            chat_id = f"mail.channel:{channel_id}"
            
            for msg in messages:
                msg_id = msg['id']
                body = msg.get('body', '')
                author_info = msg.get('author_id', [0, ''])
                author_id = author_info[0] if isinstance(author_info, list) else 0
                author_name = author_info[1] if isinstance(author_info, list) and len(author_info) > 1 else 'Unknown'
                
                # Skip system messages
                if msg.get('message_type') != 'comment':
                    continue
                
                is_from_me=(author_id == self.partner_id)
                if is_from_me:
                    continue
                
                # Create message
                message = InboundMessage(
                    id=f"mail.message:{msg_id}",
                    chat_id=jid,
                    chat_name=channel_name,
                    sender_id=str(author_id),
                    sender_name=author_name,
                    content=body,
                    timestamp=datetime.fromisoformat(msg['date'].replace('Z', '+00:00')),
                    is_from_me=is_from_me,
                    #is_bot_message=(author_id == self.partner_id),
                    is_group=False if channel_type == 'chat' else True,
                    raw_data=msg
                )
                
                await self._process_inbound_message(message)
                
            # Update last message ID
            if messages:
                self._last_message_ids[f'mail.channel:{channel_id}'] = max(m['id'] for m in messages)
                
        except Exception as e:
            logger.error(f"Error processing channel messages: {e}")

    async def _process_private_messages(self, partner_id: int, partner_name: str, message_ids: List[int]) -> None:
        """Process private messages from a partner."""
        try:
            # Get message details
            messages = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.message',
                'read',
                [message_ids, ['id', 'subject', 'body', 'author_id', 'date']]
            )
            
            jid = self.create_jid(f"res.partner:{partner_id}")
            chat_id = f"res.partner:{partner_id}"
            
            for msg in messages:
                msg_id = msg['id']
                body = msg.get('body', '')
                author_info = msg.get('author_id', [0, ''])
                author_id = author_info[0] if isinstance(author_info, list) else 0
                author_name = author_info[1] if isinstance(author_info, list) and len(author_info) > 1 else 'Unknown'
                 
                is_from_me=(author_id == self.partner_id)
                if is_from_me:
                    continue

                # Create message
                message = InboundMessage(
                    id=f"mail.message:{msg_id}",
                    chat_id=jid,
                    chat_name=partner_name,
                    sender_id=str(author_id),
                    sender_name=author_name,
                    content=body,
                    timestamp=datetime.fromisoformat(msg['date'].replace('Z', '+00:00')),
                    is_from_me=is_from_me,
                    #is_bot_message=(author_id == self.partner_id),
                    is_group=False,
                    raw_data=msg
                )
                
                await self._process_inbound_message(message)
                
            # Update last message ID
            if messages:
                self._last_message_ids[f'res.partner:{partner_id}'] = max(m['id'] for m in messages)
                
        except Exception as e:
            logger.error(f"Error processing private messages: {e}")

    async def _send_channel_message(self, channel_id: int, text: str) -> bool:
        """Send message to a channel."""
        try:
            # Post message to channel
            message_id = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'mail.channel',
                'message_post',
                [channel_id],
                {
                    'body': text,
                    'message_type': 'comment',
                    'subtype_xmlid': 'mail.mt_comment'
                }
            )
            
            logger.debug(f"Message sent to channel {channel_id}, ID: {message_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send channel message: {e}")
            return False

    async def _send_private_message(self, partner_id: int, text: str) -> bool:
        """Send private message to a partner."""
        try:
            # Create or get conversation
            #message_id = await self.models.execute_kw(
            #    self.database,
            #    self.uid,
            #    self.password,
            #    'mail.message',
            #    'create',
            #    [{
            #        'body': text,
            #        'partner_ids': [(4, partner_id)],
            #        'message_type': 'comment',
            #        #'subtype_xmlid': 'mail.mt_comment',
            #        'model': 'res.partner',
            #        'res_id': partner_id
            #    }]
            #)
            message_id = await self.models.execute_kw(
                self.database,
                self.uid,
                self.password,
                'res.partner',
                'message_post',
                [partner_id],
                {
                    'body': text,
                    'message_type': 'comment',
                    'subtype_xmlid': 'mail.mt_comment'
                }
            )
            
            logger.debug(f"Private message sent to partner {partner_id}, ID: {message_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send private message: {e}")
            return False

    async def _send_typing_notification(self, channel_id: int, is_typing: bool) -> None:
        """Send typing notification via bus."""
        return

    async def _save_session(self) -> None:
        """Save session information."""
        session_file = self.session_dir / "session.json"
        session_data = {
            'uid': self.uid,
            'user_id': self.user_id,
            'partner_id': self.partner_id,
            'company_id': self.company_id,
            'session_id': self.session_id,
            'user_context': self.user_context,
            'url': self.url,
            'database': self.database,
            'username': self.username
        }
        
        try:
            with open(session_file, 'w') as f:
                json.dump(session_data, f, indent=2, default=str)
            logger.debug(f"Session saved to {session_file}")
        except Exception as e:
            logger.warning(f"Failed to save session: {e}")

    async def _load_session(self) -> None:
        """Load saved session information."""
        session_file = self.session_dir / "session.json"
        
        try:
            if session_file.exists():
                with open(session_file, 'r') as f:
                    session_data = json.load(f)
                
                self.uid = session_data.get('uid')
                self.user_id = session_data.get('user_id')
                self.partner_id = session_data.get('partner_id')
                self.company_id = session_data.get('company_id')
                self.session_id = session_data.get('session_id')
                self.user_context = session_data.get('user_context')
                
                logger.info(f"Loaded saved session for user ID: {self.uid}")
                
        except Exception as e:
            logger.debug(f"No saved session found: {e}")

    async def _save_last_message_ids(self) -> None:
        """Save last seen message IDs."""
        ids_file = self.session_dir / "last_ids.json"
        
        try:
            with open(ids_file, 'w') as f:
                json.dump(self._last_message_ids, f, indent=2)
            logger.debug(f"Last message IDs saved to {ids_file}")
        except Exception as e:
            logger.warning(f"Failed to save last message IDs: {e}")

    async def _load_last_message_ids(self) -> None:
        """Load last seen message IDs."""
        ids_file = self.session_dir / "last_ids.json"
        
        try:
            if ids_file.exists():
                with open(ids_file, 'r') as f:
                    self._last_message_ids = json.load(f)
                logger.info(f"Loaded last message IDs for {len(self._last_message_ids)} conversations")
            else:
                logger.debug(f"No last message IDs file found: {ids_file}")
        except Exception as e:
            msg = format_exc()
            logger.debug(f"Load last message IDs fail: {msg}")
