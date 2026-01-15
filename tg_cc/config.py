"""Configuration management for tg-cc bot."""

import logging
import os
import yaml

logger = logging.getLogger(__name__)

# Global configuration
AUTHORIZED_USERS = []
AUTHORIZED_GROUPS = []
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
            AUTHORIZED_GROUPS = data.get('authorized_groups', [])
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
            for group in AUTHORIZED_GROUPS:
                logger.info(f"  - {group}")

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
