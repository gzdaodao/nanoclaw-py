# channels/factory.py
"""Channel factory with environment variable support."""

import importlib
from typing import List, Type, Dict, Any, Optional, Callable
from loguru import logger
import os
import sys

from .base import Channel, InboundMessage
from .. import config

current_package = __package__

class ChannelFactory:
    """Factory for creating channels from configuration."""

    # 内置通道注册表
    _builtin_channels = {
        'whatsapp': ('.whatsapp', 'WhatsAppChannel'),
        'telegram': ('.telegram', 'TelegramChannel'),
        'signal': ('.signal', 'SignalChannel'),
        'discord': ('.discord', 'DiscordChannel'),
        'slack': ('.slack', 'SlackChannel'),
        'odoo': ('.odoo', 'OdooChannel'),
    }

    def __init__(self):
        self._custom_channels: Dict[str, Type[Channel]] = {}

    def register_channel(self, name: str, channel_class: Type[Channel]) -> None:
        """Register a custom channel implementation."""
        self._custom_channels[name] = channel_class
        logger.debug(f"Registered custom channel: {name}")

    def create_channels(
        self,
        on_message: Callable,
        on_chat_metadata: Callable,
        registered_groups: Optional[Callable] = None  # 添加可选参数
    ) -> List[Channel]:
        """Create channels based on configuration.
        
        Args:
            on_message: Callback for incoming messages
            on_chat_metadata: Callback for chat metadata updates
            registered_groups: Optional function to get registered groups
        """
        channels = []
        
        # 从环境变量加载通道配置
        channel_configs = self._load_channel_configs()
        
        for channel_name, enabled in channel_configs.items():
            if not enabled:
                continue
                
            try:
                channel = self._create_channel(
                    channel_name,
                    on_message,
                    on_chat_metadata,
                    registered_groups  # 传递 registered_groups
                )
                if channel:
                    channels.append(channel)
                    logger.info(f"Channel enabled: {channel_name}")
            except Exception as e:
                logger.error(f"Failed to create channel {channel_name}: {e}")
        
        if not channels:
            logger.warning(
                "No channels enabled! Set WHATSAPP_ENABLED=true, TELEGRAM_ENABLED=true, "
                "ODOO_ENABLED=true, or configure other channels in .env"
            )
        
        return channels

    def _load_channel_configs(self) -> Dict[str, bool]:
        """Load channel enablement from environment variables."""
        configs = {}
        
        # 内置通道
        for name in self._builtin_channels.keys():
            env_var = f"{name.upper()}_ENABLED"
            configs[name] = getattr(config, env_var, False)
        
        # 自定义通道（通过环境变量配置）
        custom_str = getattr(config, 'CUSTOM_CHANNELS', '')
        if custom_str:
            for name in custom_str.split(','):
                name = name.strip()
                if name:
                    configs[name] = True
        
        return configs

    def _create_channel(
        self,
        channel_name: str,
        on_message: Callable,
        on_chat_metadata: Callable,
        registered_groups: Optional[Callable] = None
    ) -> Optional[Channel]:
        """Create a single channel instance."""
        
        # 构建基础参数字典
        kwargs = {
            'on_message': on_message,
            'on_chat_metadata': on_chat_metadata,
            'name': channel_name,
        }
        
        # 如果提供了 registered_groups，添加到 kwargs
        if registered_groups:
            kwargs['registered_groups'] = registered_groups
        
        # 检查自定义注册
        if channel_name in self._custom_channels:
            channel_class = self._custom_channels[channel_name]
            return self._instantiate_channel(
                channel_class,
                channel_name,
                **kwargs
            )
        
        # 检查内置通道
        if channel_name in self._builtin_channels:
            module_path, class_name = self._builtin_channels[channel_name]
            try:
                module = importlib.import_module(module_path, package=current_package)
                channel_class = getattr(module, class_name)
                return self._instantiate_channel(
                    channel_class,
                    channel_name,
                    **kwargs
                )
            except (ImportError, AttributeError) as e:
                logger.debug(f"Builtin channel {channel_name} not available: {e}")
                return None
        
        logger.warning(f"Unknown channel type: {channel_name}")
        return None

    def _instantiate_channel(
        self,
        channel_class: Type[Channel],
        channel_name: str,
        **kwargs
    ) -> Channel:
        """Instantiate a channel with configuration."""
        
        # 添加通道特定的配置
        prefix = channel_name.upper()
        
        # 通用配置
        if hasattr(config, f'{prefix}_SESSION_DIR'):
            kwargs['session_dir'] = getattr(config, f'{prefix}_SESSION_DIR')
        
        if hasattr(config, f'{prefix}_HEADLESS'):
            kwargs['headless'] = getattr(config, f'{prefix}_HEADLESS')
        
        # Token/Bot配置
        token_attrs = ['TOKEN', 'BOT_TOKEN', 'API_TOKEN', 'API_KEY']
        for attr in token_attrs:
            env_var = f'{prefix}_{attr}'
            if hasattr(config, env_var):
                kwargs['token'] = getattr(config, env_var)
                break
        
        # 手机号配置（用于Signal等）
        if hasattr(config, f'{prefix}_PHONE_NUMBER'):
            kwargs['phone_number'] = getattr(config, f'{prefix}_PHONE_NUMBER')
        
        # 代理配置
        if hasattr(config, f'{prefix}_PROXY'):
            kwargs['proxy'] = getattr(config, f'{prefix}_PROXY')
        
        # 消息限制
        if hasattr(config, f'{prefix}_MESSAGE_LIMIT'):
            kwargs['message_limit'] = getattr(config, f'{prefix}_MESSAGE_LIMIT')
        else:
            kwargs['message_limit'] = getattr(config, 'CHANNEL_MESSAGE_LIMIT', 4096)
        
        # Odoo specific configuration
        if channel_name == 'odoo':
            if hasattr(config, 'ODOO_URL'):
                kwargs['url'] = config.ODOO_URL
            if hasattr(config, 'ODOO_DATABASE'):
                kwargs['database'] = config.ODOO_DATABASE
            if hasattr(config, 'ODOO_USERNAME'):
                kwargs['username'] = config.ODOO_USERNAME
            if hasattr(config, 'ODOO_PASSWORD'):
                kwargs['password'] = config.ODOO_PASSWORD
            if hasattr(config, 'ODOO_POLL_INTERVAL'):
                kwargs['poll_interval'] = config.ODOO_POLL_INTERVAL
        
        return channel_class(**kwargs)
