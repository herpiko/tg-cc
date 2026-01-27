"""Configuration management for ccc bot."""

import logging
import os
import yaml

logger = logging.getLogger(__name__)

# Global configuration - Shared
AUTHORIZED_USERS = []
PROJECTS = []
ASK_RULES = ""
FEAT_RULES = ""
FIX_RULES = ""
PLAN_RULES = ""
FEEDBACK_RULES = ""
GENERAL_RULES = ""

# Telegram-specific configuration
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_AUTHORIZED_GROUPS = []  # List of dicts: [{"group": "id", "sub": "thread_id"}, ...]

# Lark-specific configuration
LARK_APP_ID = ""
LARK_APP_SECRET = ""
LARK_VERIFICATION_TOKEN = ""
LARK_ENCRYPT_KEY = ""
LARK_WEBHOOK_PORT = 8080
LARK_AUTHORIZED_USERS = []  # List of Lark user open_ids
LARK_AUTHORIZED_CHATS = []  # List of Lark chat_ids


def load_config(config_path: str = None):
    """Load configuration from config.yaml"""
    global PROJECTS, AUTHORIZED_USERS, TELEGRAM_AUTHORIZED_GROUPS
    global ASK_RULES, FEAT_RULES, FIX_RULES, PLAN_RULES, FEEDBACK_RULES, GENERAL_RULES
    global TELEGRAM_BOT_TOKEN
    global LARK_APP_ID, LARK_APP_SECRET, LARK_VERIFICATION_TOKEN, LARK_ENCRYPT_KEY
    global LARK_WEBHOOK_PORT, LARK_AUTHORIZED_USERS, LARK_AUTHORIZED_CHATS

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        config_path = os.path.abspath(config_path)

    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)

            # Shared configuration
            PROJECTS = data.get('projects', [])
            AUTHORIZED_USERS = data.get('authorized_users', [])
            GENERAL_RULES = data.get('general_rules', '')
            ASK_RULES = data.get('ask_rules', '')
            FEAT_RULES = data.get('feat_rules', '')
            FIX_RULES = data.get('fix_rules', '')
            PLAN_RULES = data.get('plan_rules', '')
            FEEDBACK_RULES = data.get('feedback_rules', '')

            # Telegram configuration
            telegram_config = data.get('telegram', {})
            if telegram_config:
                TELEGRAM_BOT_TOKEN = telegram_config.get('bot_token', '')

                # Parse telegram authorized_groups - supports format with optional sub (thread_id)
                raw_groups = telegram_config.get('authorized_groups', [])
                TELEGRAM_AUTHORIZED_GROUPS = []
                for group in raw_groups:
                    if isinstance(group, dict):
                        # Format: {group: "id", sub: "thread_id"}
                        TELEGRAM_AUTHORIZED_GROUPS.append({
                            "group": str(group.get('group', '')),
                            "sub": str(group.get('sub', '')) if group.get('sub') else None
                        })
                    else:
                        # Simple format: just the group id as string
                        TELEGRAM_AUTHORIZED_GROUPS.append({"group": str(group), "sub": None})

            # Lark configuration
            lark_config = data.get('lark', {})
            if lark_config:
                LARK_APP_ID = lark_config.get('app_id', '')
                LARK_APP_SECRET = lark_config.get('app_secret', '')
                LARK_VERIFICATION_TOKEN = lark_config.get('verification_token', '')
                LARK_ENCRYPT_KEY = lark_config.get('encrypt_key', '')
                LARK_WEBHOOK_PORT = lark_config.get('webhook_port', 8080)
                LARK_AUTHORIZED_USERS = lark_config.get('authorized_users', [])
                LARK_AUTHORIZED_CHATS = lark_config.get('authorized_chats', [])

            # Logging
            logger.info(f"Loaded {len(PROJECTS)} projects from {config_path}")
            for project in PROJECTS:
                logger.info(f"  - {project['project_name']}: {project['project_workdir']}")

            logger.info(f"Loaded {len(AUTHORIZED_USERS)} authorized users (shared)")
            for user in AUTHORIZED_USERS:
                logger.info(f"  - {user}")

            if TELEGRAM_BOT_TOKEN:
                logger.info(f"Telegram configuration loaded")
                logger.info(f"  - {len(TELEGRAM_AUTHORIZED_GROUPS)} authorized groups")
                for group_info in TELEGRAM_AUTHORIZED_GROUPS:
                    if group_info.get('sub'):
                        logger.info(f"    - {group_info['group']} (sub: {group_info['sub']})")
                    else:
                        logger.info(f"    - {group_info['group']}")

            if LARK_APP_ID:
                logger.info(f"Lark configuration loaded (app_id: {LARK_APP_ID[:10]}...)")
                logger.info(f"  - {len(LARK_AUTHORIZED_USERS)} authorized users")
                logger.info(f"  - {len(LARK_AUTHORIZED_CHATS)} authorized chats")
                logger.info(f"  - Webhook port: {LARK_WEBHOOK_PORT}")

    except Exception as e:
        logger.error(f"Error loading config from {config_path}: {e}")
        PROJECTS = []
        AUTHORIZED_USERS = []
        TELEGRAM_AUTHORIZED_GROUPS = []


def get_project(project_name: str) -> dict | None:
    """Find a project by name."""
    for p in PROJECTS:
        if p['project_name'] == project_name:
            return p
    return None


def get_available_projects() -> str:
    """Get comma-separated list of available project names."""
    return ", ".join([p['project_name'] for p in PROJECTS])


# Telegram-specific helpers
def is_telegram_group_authorized(chat_id: str) -> bool:
    """Check if a Telegram chat/group is authorized."""
    for group_info in TELEGRAM_AUTHORIZED_GROUPS:
        if group_info['group'] == chat_id:
            return True
    return False


def get_telegram_thread_id(chat_id: str) -> int | None:
    """Get the thread_id (sub) for a Telegram group, if configured."""
    for group_info in TELEGRAM_AUTHORIZED_GROUPS:
        if group_info['group'] == chat_id and group_info.get('sub'):
            return int(group_info['sub'])
    return None


def get_telegram_authorized_group_ids() -> list:
    """Get list of authorized Telegram group IDs (for startup messages)."""
    return [group_info['group'] for group_info in TELEGRAM_AUTHORIZED_GROUPS]


# Lark-specific helpers
def is_lark_user_authorized(user_open_id: str) -> bool:
    """Check if a Lark user is authorized."""
    # Check both shared and Lark-specific authorized users
    return user_open_id in LARK_AUTHORIZED_USERS


def is_lark_chat_authorized(chat_id: str) -> bool:
    """Check if a Lark chat is authorized."""
    return chat_id in LARK_AUTHORIZED_CHATS


# Legacy aliases for backward compatibility
def is_group_authorized(chat_id: str) -> bool:
    """Alias for is_telegram_group_authorized for backward compatibility."""
    return is_telegram_group_authorized(chat_id)


def get_thread_id(chat_id: str) -> int | None:
    """Alias for get_telegram_thread_id for backward compatibility."""
    return get_telegram_thread_id(chat_id)


def get_authorized_group_ids() -> list:
    """Alias for get_telegram_authorized_group_ids for backward compatibility."""
    return get_telegram_authorized_group_ids()
