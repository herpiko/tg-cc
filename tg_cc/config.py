"""Configuration management for tg-cc bot."""

import logging
import os
import yaml

logger = logging.getLogger(__name__)

# Global configuration
AUTHORIZED_USERS = []
AUTHORIZED_GROUPS = []  # List of dicts: [{"group": "id", "sub": "thread_id"}, ...]
PROJECTS = []
ASK_RULES = ""
FEAT_RULES = ""
FIX_RULES = ""
PLAN_RULES = ""
FEEDBACK_RULES = ""
TELEGRAM_BOT_TOKEN = ""


def load_config(config_path: str = None):
    """Load configuration from config.yaml"""
    global PROJECTS, AUTHORIZED_USERS, AUTHORIZED_GROUPS
    global ASK_RULES, FEAT_RULES, FIX_RULES, PLAN_RULES, FEEDBACK_RULES, TELEGRAM_BOT_TOKEN

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        config_path = os.path.abspath(config_path)

    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)

            TELEGRAM_BOT_TOKEN = data.get('telegram_bot_token', '')
            PROJECTS = data.get('projects', [])
            AUTHORIZED_USERS = data.get('authorized_users', [])

            # Parse authorized_groups - supports new format with optional sub (thread_id)
            raw_groups = data.get('authorized_groups', [])
            AUTHORIZED_GROUPS = []
            for group in raw_groups:
                if isinstance(group, dict):
                    # New format: {group: "id", sub: "thread_id"}
                    AUTHORIZED_GROUPS.append({
                        "group": str(group.get('group', '')),
                        "sub": str(group.get('sub', '')) if group.get('sub') else None
                    })
                else:
                    # Legacy format: just the group id as string
                    AUTHORIZED_GROUPS.append({"group": str(group), "sub": None})

            ASK_RULES = data.get('ask_rules', '')
            FEAT_RULES = data.get('feat_rules', '')
            FIX_RULES = data.get('fix_rules', '')
            PLAN_RULES = data.get('plan_rules', '')
            FEEDBACK_RULES = data.get('feedback_rules', '')

            logger.info(f"Loaded {len(PROJECTS)} projects from {config_path}")
            for project in PROJECTS:
                logger.info(f"  - {project['project_name']}: {project['project_workdir']}")

            logger.info(f"Loaded {len(AUTHORIZED_USERS)} authorized users")
            for user in AUTHORIZED_USERS:
                logger.info(f"  - {user}")

            logger.info(f"Loaded {len(AUTHORIZED_GROUPS)} authorized groups")
            for group_info in AUTHORIZED_GROUPS:
                if group_info.get('sub'):
                    logger.info(f"  - {group_info['group']} (sub: {group_info['sub']})")
                else:
                    logger.info(f"  - {group_info['group']}")

    except Exception as e:
        logger.error(f"Error loading config from {config_path}: {e}")
        PROJECTS = []
        AUTHORIZED_USERS = []
        AUTHORIZED_GROUPS = []


def get_project(project_name: str) -> dict | None:
    """Find a project by name."""
    for p in PROJECTS:
        if p['project_name'] == project_name:
            return p
    return None


def get_available_projects() -> str:
    """Get comma-separated list of available project names."""
    return ", ".join([p['project_name'] for p in PROJECTS])


def is_group_authorized(chat_id: str) -> bool:
    """Check if a chat/group is authorized."""
    for group_info in AUTHORIZED_GROUPS:
        if group_info['group'] == chat_id:
            return True
    return False


def get_thread_id(chat_id: str) -> int | None:
    """Get the thread_id (sub) for a group, if configured."""
    for group_info in AUTHORIZED_GROUPS:
        if group_info['group'] == chat_id and group_info.get('sub'):
            return int(group_info['sub'])
    return None


def get_authorized_group_ids() -> list:
    """Get list of authorized group IDs (for startup messages)."""
    return [group_info['group'] for group_info in AUTHORIZED_GROUPS]
