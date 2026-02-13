"""Configuration management for ccc bot."""

import logging
import os
import re
import yaml

logger = logging.getLogger(__name__)

# Global configuration - Shared
AUTHORIZED_USERS = []  # Normalized list of dicts: [{"username": "...", "lark_ouid": "...", "name": "...", "email": "..."}, ...]
PROJECTS = []
ASK_RULES = ""
FEAT_RULES = ""
FIX_RULES = ""
PLAN_RULES = ""
FEEDBACK_RULES = ""
GENERAL_RULES = ""
WORKTREE_BASE = "/tmp/ccc-worktrees"  # Base directory for git worktrees

# Telegram-specific configuration
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_AUTHORIZED_GROUPS = []  # List of dicts: [{"group": "id", "sub": "thread_id"}, ...]

# Lark-specific configuration
LARK_APP_ID = ""
LARK_APP_SECRET = ""
LARK_VERIFICATION_TOKEN = ""
LARK_ENCRYPT_KEY = ""
LARK_WEBHOOK_PORT = 8080
LARK_AUTHORIZED_CHATS = []  # List of Lark chat_ids
LARK_DOCUMENTS = []  # List of dicts: [{"name": "...", "type": "doc"|"spreadsheet", "token": "..."}]


def load_config(config_path: str = None):
    """Load configuration from config.yaml"""
    global PROJECTS, AUTHORIZED_USERS, TELEGRAM_AUTHORIZED_GROUPS
    global ASK_RULES, FEAT_RULES, FIX_RULES, PLAN_RULES, FEEDBACK_RULES, GENERAL_RULES
    global TELEGRAM_BOT_TOKEN, WORKTREE_BASE
    global LARK_APP_ID, LARK_APP_SECRET, LARK_VERIFICATION_TOKEN, LARK_ENCRYPT_KEY
    global LARK_WEBHOOK_PORT, LARK_AUTHORIZED_CHATS, LARK_DOCUMENTS

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        config_path = os.path.abspath(config_path)

    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)

            # Shared configuration
            PROJECTS = data.get('projects', [])
            AUTHORIZED_USERS = _normalize_authorized_users(data.get('authorized_users', []))
            GENERAL_RULES = data.get('general_rules', '')
            ASK_RULES = data.get('ask_rules', '')
            FEAT_RULES = data.get('feat_rules', '')
            FIX_RULES = data.get('fix_rules', '')
            PLAN_RULES = data.get('plan_rules', '')
            FEEDBACK_RULES = data.get('feedback_rules', '')
            WORKTREE_BASE = data.get('worktree_base', '/tmp/ccc-worktrees')

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
                LARK_AUTHORIZED_CHATS = lark_config.get('authorized_chats', [])

            # Lark documents configuration
            LARK_DOCUMENTS = _parse_lark_documents(data.get('lark_documents', []))

            # Logging
            logger.info(f"Loaded {len(PROJECTS)} projects from {config_path}")
            logger.info(f"Worktree base: {WORKTREE_BASE}")
            for project in PROJECTS:
                logger.info(f"  - {project['project_name']}: {project['project_workdir']}")

            logger.info(f"Loaded {len(AUTHORIZED_USERS)} authorized users")
            for user in AUTHORIZED_USERS:
                name_part = f" ({user['name']})" if user.get('name') else ""
                lark_part = f" [lark: {user['lark_ouid']}]" if user.get('lark_ouid') else ""
                logger.info(f"  - {user['username']}{name_part}{lark_part}")

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
                lark_user_count = sum(1 for u in AUTHORIZED_USERS if u.get("lark_ouid"))
                logger.info(f"  - {lark_user_count} users with Lark open_id")
                logger.info(f"  - {len(LARK_AUTHORIZED_CHATS)} authorized chats")
                logger.info(f"  - Webhook port: {LARK_WEBHOOK_PORT}")

            if LARK_DOCUMENTS:
                logger.info(f"Loaded {len(LARK_DOCUMENTS)} Lark documents")
                for doc in LARK_DOCUMENTS:
                    logger.info(f"  - {doc['name']} ({doc['type']}): {doc['token'][:10]}...")

    except Exception as e:
        logger.error(f"Error loading config from {config_path}: {e}")
        PROJECTS = []
        AUTHORIZED_USERS = []
        TELEGRAM_AUTHORIZED_GROUPS = []


def _normalize_authorized_users(raw_users: list) -> list[dict]:
    """Normalize authorized_users to list of dicts with username, lark_ouid, name, email."""
    result = []
    for entry in raw_users:
        if isinstance(entry, dict):
            result.append({
                "username": entry.get("username", ""),
                "lark_ouid": entry.get("lark_ouid", ""),
                "name": entry.get("name", ""),
                "email": entry.get("email", ""),
            })
        else:
            # Plain string: treat as username only
            result.append({"username": str(entry), "lark_ouid": "", "name": "", "email": ""})
    return result


def is_user_authorized(username: str) -> bool:
    """Check if a username is in the authorized users list."""
    return any(u["username"] == username for u in AUTHORIZED_USERS)


def get_user_info(username: str) -> dict | None:
    """Get user info dict for a username. Returns {username, name, email} or None."""
    for u in AUTHORIZED_USERS:
        if u["username"] == username:
            return u
    return None


def get_lark_user_info(user_open_id: str) -> dict | None:
    """Get user info dict for a Lark user. Returns {username, lark_ouid, name, email} or None."""
    for u in AUTHORIZED_USERS:
        if u["lark_ouid"] == user_open_id:
            return u
    return None


def get_authorized_usernames() -> list[str]:
    """Get list of authorized usernames (for display)."""
    return [u["username"] for u in AUTHORIZED_USERS]


def get_project(project_name: str) -> dict | None:
    """Find a project by name."""
    for p in PROJECTS:
        if p['project_name'] == project_name:
            return p
    return None


def get_available_projects() -> str:
    """Get comma-separated list of available project names."""
    return ", ".join([p['project_name'] for p in PROJECTS])


def _parse_lark_documents(raw_docs: list) -> list[dict]:
    """Parse lark_documents config entries, extracting tokens from URLs."""
    result = []
    for entry in raw_docs:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        doc_type = entry.get("type", "doc")
        token = entry.get("token", "")

        if not token and entry.get("url"):
            token = _extract_token_from_url(entry["url"])

        if name and token:
            result.append({"name": name, "type": doc_type, "token": token})
        else:
            logger.warning(f"Skipping lark_documents entry with missing name or token: {entry}")
    return result


def _extract_token_from_url(url: str) -> str:
    """Extract document/spreadsheet token from a Lark/Feishu URL.

    Supports URLs like:
      https://xxx.larksuite.com/docx/XXXXX
      https://xxx.feishu.cn/docx/XXXXX
      https://xxx.larksuite.com/sheets/XXXXX
      https://xxx.feishu.cn/sheets/XXXXX?query=...
    """
    match = re.search(r'/(docx|sheets|wiki|base)/([A-Za-z0-9]+)', url)
    if match:
        return match.group(2)
    return ""


def get_lark_document(name: str) -> dict | None:
    """Find a Lark document by name."""
    for doc in LARK_DOCUMENTS:
        if doc["name"] == name:
            return doc
    return None


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
    return any(u["lark_ouid"] == user_open_id for u in AUTHORIZED_USERS)


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
