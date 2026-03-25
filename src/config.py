# config.py
"""
Configuration management using environment variables only.
All settings can be set via environment variables for Docker deployment.
"""

import os
import re
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load .env file if exists (for development, but all values can be overridden by env)
load_dotenv()

# ============================================================================
# Helper Functions
# ============================================================================

def str_to_bool(value: Optional[str]) -> bool:
    """Convert string to boolean."""
    if value is None:
        return False
    return value.lower() in ('true', '1', 'yes', 'on', 'y')

def str_to_int(value: Optional[str], default: int) -> int:
    """Convert string to int with default."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default

def get_env_list(key: str, default: str = '') -> list:
    """Get comma-separated list from environment variable."""
    value = os.getenv(key, default)
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]

# ============================================================================
# Core Application Settings
# ============================================================================

# Assistant name (used for triggers)
ASSISTANT_NAME = os.getenv('ASSISTANT_NAME', 'Andy')

# Does the assistant have its own phone number?
# If false, messages will be prefixed with assistant name
ASSISTANT_HAS_OWN_NUMBER = str_to_bool(os.getenv('ASSISTANT_HAS_OWN_NUMBER'))

# Polling intervals (in milliseconds)
POLL_INTERVAL = str_to_int(os.getenv('POLL_INTERVAL'), 1000)
SCHEDULER_POLL_INTERVAL = str_to_int(os.getenv('SCHEDULER_POLL_INTERVAL'), 60000)
IPC_POLL_INTERVAL = str_to_int(os.getenv('IPC_POLL_INTERVAL'), 1000)

# Paths (all relative to /app in Docker, can be overridden)
DATA_DIR = Path(os.getenv('DATA_DIR', '/app/data'))
STORE_DIR = Path(os.getenv('STORE_DIR', '/app/store'))
GROUPS_DIR = Path(os.getenv('GROUPS_DIR', '/app/groups'))
HOST_DIR = Path(os.getenv('HOST_DIR', '/data/docker-data/nanoclaw'))

# Main group folder name
MAIN_GROUP_FOLDER = os.getenv('MAIN_GROUP_FOLDER', 'main')

# Timezone
TIMEZONE = os.getenv('TZ', 'UTC')

# Trigger pattern (automatically generated from ASSISTANT_NAME)
def escape_regex(s: str) -> str:
    """Escape regex special characters"""
    return re.escape(s)

TRIGGER_PATTERN = re.compile(f'.*@{escape_regex(ASSISTANT_NAME)}\\b', re.IGNORECASE)

# ============================================================================
# Container Runtime Settings
# ============================================================================

# Container image to use
CONTAINER_IMAGE = os.getenv('CONTAINER_IMAGE', 'nanoclaw-agent:latest')

# Container timeout in milliseconds
CONTAINER_TIMEOUT = str_to_int(os.getenv('CONTAINER_TIMEOUT'), 1800000)  # 30 minutes

# Maximum output size from container (in bytes)
CONTAINER_MAX_OUTPUT_SIZE = str_to_int(os.getenv('CONTAINER_MAX_OUTPUT_SIZE'), 10485760)  # 10MB

# Idle timeout - how long to keep container alive after last result
IDLE_TIMEOUT = str_to_int(os.getenv('IDLE_TIMEOUT'), 1800000)  # 30 minutes

# Maximum concurrent containers
MAX_CONCURRENT_CONTAINERS = max(1, str_to_int(os.getenv('MAX_CONCURRENT_CONTAINERS'), 5))

# Container runtime binary
CONTAINER_RUNTIME_BIN = os.getenv('CONTAINER_RUNTIME_BIN', 'docker')

# Mount allowlist path (for security)
MOUNT_ALLOWLIST_PATH = Path(os.getenv(
    'MOUNT_ALLOWLIST_PATH',
    '/root/.config/nanoclaw/mount-allowlist.json'
))

# ============================================================================
# Logging Settings
# ============================================================================

LOG_LEVEL = os.getenv('LOG_LEVEL', 'info').upper()
LOG_FORMAT = os.getenv('LOG_FORMAT', 'json')  # 'json' or 'pretty'
LOG_OUTPUT = os.getenv('LOG_OUTPUT', 'stdout')  # 'stdout', 'stderr', or file path

# ============================================================================
# Channel Enablement Flags
# ============================================================================

WHATSAPP_ENABLED = str_to_bool(os.getenv('WHATSAPP_ENABLED'))
TELEGRAM_ENABLED = str_to_bool(os.getenv('TELEGRAM_ENABLED'))
SIGNAL_ENABLED = str_to_bool(os.getenv('SIGNAL_ENABLED'))
DISCORD_ENABLED = str_to_bool(os.getenv('DISCORD_ENABLED'))
SLACK_ENABLED = str_to_bool(os.getenv('SLACK_ENABLED'))
ODOO_ENABLED = str_to_bool(os.getenv('ODOO_ENABLED'))

# ============================================================================
# Odoo 16 Configuration
# ============================================================================


# Odoo connection settings
ODOO_URL = os.getenv('ODOO_URL', 'http://localhost:8069')
ODOO_DATABASE = os.getenv('ODOO_DATABASE', '')
ODOO_USERNAME = os.getenv('ODOO_USERNAME', '')
ODOO_PASSWORD = os.getenv('ODOO_PASSWORD', '')

# Odoo session storage
ODOO_SESSION_DIR = Path(os.getenv(
    'ODOO_SESSION_DIR',
    str(STORE_DIR / 'odoo-session')
))

# Odoo polling interval (milliseconds)
ODOO_POLL_INTERVAL = str_to_int(os.getenv('ODOO_POLL_INTERVAL'), 1000)

# Odoo message limit
ODOO_MESSAGE_LIMIT = str_to_int(os.getenv('ODOO_MESSAGE_LIMIT'), 4096)

# Odoo models to monitor (comma-separated)
ODOO_MONITOR_MODELS = get_env_list(
    'ODOO_MONITOR_MODELS',
    'mail.channel,res.partner'
)

# Odoo channel types to monitor
ODOO_CHANNEL_TYPES = get_env_list(
    'ODOO_CHANNEL_TYPES',
    'channel,chat'
)

# Odoo include mentions
ODOO_INCLUDE_MENTIONS = str_to_bool(os.getenv('ODOO_INCLUDE_MENTIONS', 'true'))

# Odoo auto-mark read
ODOO_AUTO_MARK_READ = str_to_bool(os.getenv('ODOO_AUTO_MARK_READ', 'true'))

# Odoo SSL verification
ODOO_SSL_VERIFY = str_to_bool(os.getenv('ODOO_SSL_VERIFY', 'true'))
# Custom channels (comma-separated list of channel names)
CUSTOM_CHANNELS = get_env_list('CUSTOM_CHANNELS')

# ============================================================================
# WhatsApp Configuration
# ============================================================================

WHATSAPP_SESSION_DIR = Path(os.getenv(
    'WHATSAPP_SESSION_DIR',
    str(STORE_DIR / 'whatsapp-session')
))
WHATSAPP_HEADLESS = str_to_bool(os.getenv('WHATSAPP_HEADLESS', 'true'))  # Default to headless in Docker
WHATSAPP_MESSAGE_LIMIT = str_to_int(os.getenv('WHATSAPP_MESSAGE_LIMIT'), 4096)
WHATSAPP_RECONNECT_DELAY = str_to_int(os.getenv('WHATSAPP_RECONNECT_DELAY'), 5000)  # milliseconds

# ============================================================================
# Telegram Configuration
# ============================================================================

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_MESSAGE_LIMIT = str_to_int(os.getenv('TELEGRAM_MESSAGE_LIMIT'), 4096)
TELEGRAM_WEBHOOK_URL = os.getenv('TELEGRAM_WEBHOOK_URL', '')  # For webhook mode
TELEGRAM_WEBHOOK_PORT = str_to_int(os.getenv('TELEGRAM_WEBHOOK_PORT'), 8443)
TELEGRAM_WEBHOOK_HOST = os.getenv('TELEGRAM_WEBHOOK_HOST', '0.0.0.0')

# ============================================================================
# Signal Configuration
# ============================================================================

SIGNAL_PHONE_NUMBER = os.getenv('SIGNAL_PHONE_NUMBER', '')
SIGNAL_CLI_PATH = os.getenv('SIGNAL_CLI_PATH', 'signal-cli')
SIGNAL_MESSAGE_LIMIT = str_to_int(os.getenv('SIGNAL_MESSAGE_LIMIT'), 4096)
SIGNAL_DATA_DIR = Path(os.getenv('SIGNAL_DATA_DIR', str(DATA_DIR / 'signal')))

# ============================================================================
# Discord Configuration
# ============================================================================

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', '')
DISCORD_MESSAGE_LIMIT = str_to_int(os.getenv('DISCORD_MESSAGE_LIMIT'), 2000)
DISCORD_COMMAND_PREFIX = os.getenv('DISCORD_COMMAND_PREFIX', '!')

# ============================================================================
# Slack Configuration
# ============================================================================

SLACK_APP_TOKEN = os.getenv('SLACK_APP_TOKEN', '')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN', '')
SLACK_MESSAGE_LIMIT = str_to_int(os.getenv('SLACK_MESSAGE_LIMIT'), 40000)
SLACK_USER_TOKEN = os.getenv('SLACK_USER_TOKEN', '')  # Optional for user operations

# ============================================================================
# Database Configuration
# ============================================================================

DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'sqlite')  # 'sqlite' or 'postgres'

# SQLite
SQLITE_PATH = Path(os.getenv('SQLITE_PATH', str(STORE_DIR / 'nanoclaw.db')))

# PostgreSQL
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
POSTGRES_PORT = str_to_int(os.getenv('POSTGRES_PORT'), 5432)
POSTGRES_DATABASE = os.getenv('POSTGRES_DATABASE', 'nanoclaw')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'nanoclaw')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', '')
POSTGRES_POOL_SIZE = str_to_int(os.getenv('POSTGRES_POOL_SIZE'), 10)
POSTGRES_MAX_OVERFLOW = str_to_int(os.getenv('POSTGRES_MAX_OVERFLOW'), 20)

# ============================================================================
# Redis Configuration (for distributed mode)
# ============================================================================

REDIS_ENABLED = str_to_bool(os.getenv('REDIS_ENABLED'))
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = str_to_int(os.getenv('REDIS_PORT'), 6379)
REDIS_DB = str_to_int(os.getenv('REDIS_DB'), 0)
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
REDIS_SOCKET_TIMEOUT = str_to_int(os.getenv('REDIS_SOCKET_TIMEOUT'), 5)
REDIS_SOCKET_CONNECT_TIMEOUT = str_to_int(os.getenv('REDIS_SOCKET_CONNECT_TIMEOUT'), 5)

# ============================================================================
# Security Settings
# ============================================================================

# Secret key for internal communications
SECRET_KEY = os.getenv('SECRET_KEY', '')  # Should be set in production

# Allowed hosts for webhooks
ALLOWED_HOSTS = get_env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')

# CORS settings
CORS_ORIGINS = get_env_list('CORS_ORIGINS', '*')

# ============================================================================
# API Configuration
# ============================================================================

API_ENABLED = str_to_bool(os.getenv('API_ENABLED'))
API_HOST = os.getenv('API_HOST', '0.0.0.0')
API_PORT = str_to_int(os.getenv('API_PORT'), 8000)
API_WORKERS = str_to_int(os.getenv('API_WORKERS'), 4)
API_RELOAD = str_to_bool(os.getenv('API_RELOAD', 'false'))  # Development only

# API Authentication
API_KEY_REQUIRED = str_to_bool(os.getenv('API_KEY_REQUIRED', 'true'))
API_KEY = os.getenv('API_KEY', '')  # If empty, all keys are accepted
API_KEY_HEADER = os.getenv('API_KEY_HEADER', 'X-API-Key')

# ============================================================================
# External Services
# ============================================================================

# OpenAI API (for GPT)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
OPENAI_ORG_ID = os.getenv('OPENAI_ORG_ID', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', '')


# ============================================================================
# Docker-Specific Settings
# ============================================================================

# Container mode detection
IN_DOCKER = Path('/.dockerenv').exists() or os.getenv('IN_DOCKER', 'false').lower() == 'true'

# Docker health check settings
HEALTH_CHECK_ENABLED = str_to_bool(os.getenv('HEALTH_CHECK_ENABLED', 'true'))
HEALTH_CHECK_PORT = str_to_int(os.getenv('HEALTH_CHECK_PORT'), 8080)
HEALTH_CHECK_PATH = os.getenv('HEALTH_CHECK_PATH', '/health')

# Docker volumes
DOCKER_SOCKET_PATH = os.getenv('DOCKER_SOCKET_PATH', '/var/run/docker.sock')
DOCKER_NETWORK = os.getenv('DOCKER_NETWORK', 'nanoclaw-network')

# ============================================================================
# Configuration Validation
# ============================================================================

def validate_config() -> Dict[str, str]:
    """Validate configuration and return list of errors."""
    errors = {}
    
    # Check required settings based on enabled features
    if TELEGRAM_ENABLED and not TELEGRAM_BOT_TOKEN:
        errors['TELEGRAM_BOT_TOKEN'] = 'Telegram enabled but token not set'
    
    if SIGNAL_ENABLED and not SIGNAL_PHONE_NUMBER:
        errors['SIGNAL_PHONE_NUMBER'] = 'Signal enabled but phone number not set'
    
    if DISCORD_ENABLED and not DISCORD_BOT_TOKEN:
        errors['DISCORD_BOT_TOKEN'] = 'Discord enabled but token not set'
    
    if SLACK_ENABLED and (not SLACK_APP_TOKEN or not SLACK_BOT_TOKEN):
        errors['SLACK'] = 'Slack enabled but tokens not set (need both app_token and bot_token)'

    if ODOO_ENABLED:
        if not ODOO_URL:
            errors['ODOO_URL'] = 'Odoo enabled but URL not set'
        if not ODOO_DATABASE:
            errors['ODOO_DATABASE'] = 'Odoo enabled but database not set'
        if not ODOO_USERNAME:
            errors['ODOO_USERNAME'] = 'Odoo enabled but username not set'
        if not ODOO_PASSWORD:
            errors['ODOO_PASSWORD'] = 'Odoo enabled but password not set'
 

    if API_ENABLED and API_KEY_REQUIRED and not API_KEY:
        errors['API_KEY'] = 'API enabled with key required but no API_KEY set'
    
    return errors

# ============================================================================
# Docker Environment File Template
# ============================================================================

DOCKER_ENV_TEMPLATE = """
# NanoClaw Docker Environment Configuration
# Copy this to .env or pass directly to docker run --env-file

# ============================================================================
# Core Settings
# ============================================================================
ASSISTANT_NAME=Andy
ASSISTANT_HAS_OWN_NUMBER=false
LOG_LEVEL=info
TIMEZONE=UTC

# ============================================================================
# Channel Enablement
# ============================================================================
ODOO_ENABLED=true
WHATSAPP_ENABLED=false
TELEGRAM_ENABLED=false
SIGNAL_ENABLED=false
DISCORD_ENABLED=false
SLACK_ENABLED=false

# ============================================================================
# Odoo 16
# ============================================================================
ODOO_URL=http://odoo-server:8069
ODOO_DATABASE=your_database
ODOO_USERNAME=your_username
ODOO_PASSWORD=your_password
ODOO_POLL_INTERVAL=5000
ODOO_MONITOR_MODELS=mail.channel,res.partner
ODOO_INCLUDE_MENTIONS=true
ODOO_AUTO_MARK_READ=true

# ============================================================================
# WhatsApp (always runs in headless mode in Docker)
# ============================================================================
WHATSAPP_SESSION_DIR=/app/store/whatsapp-session
WHATSAPP_HEADLESS=true

# ============================================================================
# Telegram
# ============================================================================
TELEGRAM_BOT_TOKEN=your_bot_token_here

# ============================================================================
# Signal
# ============================================================================
SIGNAL_PHONE_NUMBER=+1234567890
SIGNAL_CLI_PATH=/usr/local/bin/signal-cli

# ============================================================================
# Discord
# ============================================================================
DISCORD_BOT_TOKEN=your_discord_token

# ============================================================================
# Slack
# ============================================================================
SLACK_APP_TOKEN=xapp-your-app-token
SLACK_BOT_TOKEN=xoxb-your-bot-token

# ============================================================================
# Database
# ============================================================================
DATABASE_TYPE=sqlite
SQLITE_PATH=/app/store/nanoclaw.db

# For PostgreSQL (uncomment to use)
# DATABASE_TYPE=postgres
# POSTGRES_HOST=postgres
# POSTGRES_DATABASE=nanoclaw
# POSTGRES_USER=nanoclaw
# POSTGRES_PASSWORD=changeme

# ============================================================================
# Redis (for distributed mode)
# ============================================================================
# REDIS_ENABLED=true
# REDIS_HOST=redis
# REDIS_PASSWORD=

# ============================================================================
# API
# ============================================================================
API_ENABLED=false
API_KEY_REQUIRED=true
API_KEY=your-secret-api-key-here

# ============================================================================
# Container Settings
# ============================================================================
CONTAINER_IMAGE=nanoclaw-agent:latest
CONTAINER_TIMEOUT=1800000
MAX_CONCURRENT_CONTAINERS=5

# ============================================================================
# Security
# ============================================================================
# Set a strong secret key in production
SECRET_KEY=your-secret-key-here

# ============================================================================
# External API Keys
# ============================================================================
ANTHROPIC_API_KEY=your-anthropic-key
OPENAI_API_KEY=your-openai-key
"""

# ============================================================================
# Helper function to print configuration
# ============================================================================

def print_config() -> None:
    """Print current configuration (useful for debugging)."""
    import json
    from datetime import datetime
    
    config_dict = {}
    for key, value in globals().items():
        if key.isupper() and not key.startswith('_'):
            if isinstance(value, Path):
                config_dict[key] = str(value)
            elif isinstance(value, re.Pattern):
                config_dict[key] = value.pattern
            else:
                config_dict[key] = value
    
    print(f"\n=== NanoClaw Configuration ({datetime.now().isoformat()}) ===")
    print(json.dumps(config_dict, indent=2, default=str))
    
    # Validate and show errors
    errors = validate_config()
    if errors:
        print("\n=== Configuration Errors ===")
        for key, error in errors.items():
            print(f"  {key}: {error}")
