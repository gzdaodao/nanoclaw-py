# router.py - 面向对象版本
import re
from typing import List, Optional
import json

from .dtypes import NewMessage, Channel


class MessageFormatter:
    """Format messages for agent consumption"""
    
    @staticmethod
    def escape_xml(s: str) -> str:
        """Escape XML special characters"""
        if not s:
            return ''
        return (s.replace('&', '&amp;')
                  .replace('<', '&lt;')
                  .replace('>', '&gt;')
                  .replace('"', '&quot;'))
    
    #@classmethod
    #def format_messages(cls, messages: List[NewMessage]) -> str:
    #    """Format messages as XML"""
    #    lines = []
    #    for m in messages:
    #        lines.append(
    #            f'<message sender="{cls.escape_xml(m.sender_name)}" time="{m.timestamp}">'
    #            f'{cls.escape_xml(m.content)}</message>'
    #        )
    #    return '<messages>\n' + '\n'.join(lines) + '\n</messages>'

 
    @classmethod
    def format_messages(cls, messages: List[NewMessage]) -> str:
        lines = []
        for m in messages:
            lines.append({
                'sender': m.sender_name,
                'time': m.timestamp,
                'message': m.content,
                })

        return json.dumps(lines, ensure_ascii=False)


class InternalTagStripper:
    """Strip internal tags from text"""
    
    INTERNAL_TAG_PATTERN = re.compile(r'<internal>[\s\S]*?<\/internal>')
    
    @classmethod
    def strip(cls, text: str) -> str:
        """Strip <internal> tags from text"""
        return cls.INTERNAL_TAG_PATTERN.sub('', text).strip()


class OutboundFormatter:
    """Format outbound messages"""
    
    def __init__(self):
        self.stripper = InternalTagStripper()
    
    def format(self, raw_text: str) -> str:
        """Format outbound message by stripping internal tags"""
        return self.stripper.strip(raw_text)


class ChannelRouter:
    """Route messages to appropriate channels"""
    
    @staticmethod
    def find_channel(channels: List[Channel], jid: str) -> Optional[Channel]:
        """Find channel that owns a JID"""
        for c in channels:
            if c.owns_jid(jid):
                return c
        return None


# Maintain backward compatibility
escape_xml = MessageFormatter.escape_xml
format_messages = MessageFormatter.format_messages
strip_internal_tags = InternalTagStripper.strip
format_outbound = OutboundFormatter().format
find_channel = ChannelRouter.find_channel
