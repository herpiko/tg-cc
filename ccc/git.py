"""Git operations for ccc bot."""

import logging
import os
import subprocess
from typing import Any

from .messenger import Messenger

logger = logging.getLogger(__name__)


async def clone_repository_if_needed(messenger: Messenger, context: Any, project_repo: str, project_workdir: str) -> bool:
    """Clone repository if project directory doesn't exist.

    Args:
        messenger: Platform-specific messenger for sending replies
        context: Platform-specific context (Telegram update, Lark message dict, etc.)
        project_repo: Git repository URL
        project_workdir: Working directory for the project
    """
    if os.path.exists(project_workdir):
        return True

    await messenger.reply(context, f"Project directory not found. Cloning {project_repo}...")
    logger.info(f"Cloning {project_repo} into {project_workdir}")

    try:
        parent_dir = os.path.dirname(project_workdir)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        clone_result = subprocess.run(
            ["git", "clone", project_repo, project_workdir],
            capture_output=True,
            text=True,
            timeout=1800
        )

        if clone_result.returncode != 0:
            await messenger.reply(context, f"Failed to clone repository:\n{clone_result.stderr[:500]}")
            return False

        await messenger.reply(context, f"Repository cloned successfully!")
        logger.info(f"Successfully cloned {project_repo}")
        return True

    except subprocess.TimeoutExpired:
        await messenger.reply(context, "Git clone timed out after 30 minutes")
        return False
    except Exception as e:
        logger.error(f"Error cloning repository: {e}")
        await messenger.reply(context, f"Error cloning repository: {str(e)}")
        return False


async def refresh_to_main_branch(messenger: Messenger, context: Any, project_workdir: str) -> bool:
    """Reset to main branch and pull latest changes.

    Args:
        messenger: Platform-specific messenger for sending replies
        context: Platform-specific context (Telegram update, Lark message dict, etc.)
        project_workdir: Working directory for the project
    """
    logger.info(f"Preparing fresh main branch")

    try:
        # Clean up the branch - reset any uncommitted changes
        logger.info("Cleaning up branch")

        reset_result = subprocess.run(
            ["git", "reset", "--hard"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if reset_result.returncode != 0:
            logger.error(f"git reset failed: {reset_result.stderr}")
            await messenger.reply(context, f"Warning: Could not clean branch:\n{reset_result.stderr[:500]}")

        # Clean untracked files
        clean_result = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if clean_result.returncode != 0:
            logger.error(f"git clean failed: {clean_result.stderr}")

        # Checkout to main branch
        logger.info("Checking out main branch")

        checkout_result = subprocess.run(
            ["git", "checkout", "main"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if checkout_result.returncode != 0:
            logger.error(f"git checkout main failed: {checkout_result.stderr}")
            await messenger.reply(context, f"Warning: Could not checkout main:\n{checkout_result.stderr[:500]}")

        # Pull latest from main
        logger.info("Pulling from origin/main")

        pull_result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=300
        )

        if pull_result.returncode != 0:
            logger.error(f"git pull failed: {pull_result.stderr}")
            await messenger.reply(context, f"Warning: Could not pull from main:\n{pull_result.stderr[:500]}")

        return True

    except Exception as e:
        logger.error(f"Error refreshing to main branch")
        await messenger.reply(context, f"Error refreshing to main branch: {str(e)}")
        return False
