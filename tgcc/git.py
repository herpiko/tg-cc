"""Git operations for tgcc bot."""

import logging
import os
import subprocess

from . import config

logger = logging.getLogger(__name__)


async def _reply(update, text: str):
    """Reply to a message, sending to the configured thread if set."""
    chat_id = str(update.message.chat.id)
    thread_id = config.get_thread_id(chat_id)
    await update.message.reply_text(text, message_thread_id=thread_id)


async def clone_repository_if_needed(update, project_repo: str, project_workdir: str) -> bool:
    """Clone repository if project directory doesn't exist."""
    if os.path.exists(project_workdir):
        return True

    await _reply(update,f"Project directory not found. Cloning {project_repo}...")
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
            await _reply(update,f"Failed to clone repository:\n{clone_result.stderr[:500]}")
            return False

        await _reply(update,f"Repository cloned successfully!")
        logger.info(f"Successfully cloned {project_repo}")
        return True

    except subprocess.TimeoutExpired:
        await _reply(update,"Git clone timed out after 30 minutes")
        return False
    except Exception as e:
        logger.error(f"Error cloning repository: {e}")
        await _reply(update,f"Error cloning repository: {str(e)}")
        return False


async def refresh_to_main_branch(update, project_workdir: str) -> bool:
    """Reset to main branch and pull latest changes."""
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
            await _reply(update,f"Warning: Could not clean branch:\n{reset_result.stderr[:500]}")

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
            await _reply(update,f"Warning: Could not checkout main:\n{checkout_result.stderr[:500]}")

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
            await _reply(update,f"Warning: Could not pull from main:\n{pull_result.stderr[:500]}")

        return True

    except Exception as e:
        logger.error(f"Error refreshing to main branch")
        await _reply(update,f"Error refreshing to main branch: {str(e)}")
        return False
