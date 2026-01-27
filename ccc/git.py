"""Git operations for ccc bot."""

import logging
import os
import shutil
import subprocess
from typing import Any, Optional

from .messenger import Messenger

logger = logging.getLogger(__name__)

# Base path for worktrees
WORKTREE_BASE = "/tmp/ccc-worktrees"


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


def get_worktree_path(project_name: str, query_id: str) -> str:
    """Get the worktree path for a specific query.

    Args:
        project_name: Name of the project
        query_id: Unique query ID

    Returns:
        Path to the worktree directory
    """
    return os.path.join(WORKTREE_BASE, project_name, query_id)


async def create_worktree(messenger: Messenger, context: Any, project_workdir: str, project_name: str, query_id: str) -> Optional[str]:
    """Create a git worktree for isolated query execution.

    Args:
        messenger: Platform-specific messenger for sending replies
        context: Platform-specific context
        project_workdir: Main project working directory (git repo)
        project_name: Name of the project
        query_id: Unique query ID

    Returns:
        Path to the created worktree, or None if failed
    """
    worktree_path = get_worktree_path(project_name, query_id)

    try:
        # Ensure base directory exists
        os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

        # First, make sure main repo is on main branch and up to date
        logger.info(f"Fetching latest changes in {project_workdir}")
        fetch_result = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=300
        )
        if fetch_result.returncode != 0:
            logger.warning(f"git fetch failed: {fetch_result.stderr}")

        # Create worktree from origin/main (detached HEAD, will create branch in Claude)
        logger.info(f"Creating worktree at {worktree_path}")
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", worktree_path, "origin/main"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            logger.error(f"Failed to create worktree: {result.stderr}")
            await messenger.reply(context, f"Failed to create isolated workspace: {result.stderr[:200]}")
            return None

        logger.info(f"Created worktree at {worktree_path}")
        return worktree_path

    except subprocess.TimeoutExpired:
        logger.error("Worktree creation timed out")
        await messenger.reply(context, "Failed to create workspace: operation timed out")
        return None
    except Exception as e:
        logger.error(f"Error creating worktree: {e}")
        await messenger.reply(context, f"Failed to create workspace: {str(e)[:200]}")
        return None


def cleanup_worktree(project_workdir: str, worktree_path: str) -> bool:
    """Clean up a git worktree after query completion.

    Args:
        project_workdir: Main project working directory (git repo)
        worktree_path: Path to the worktree to remove

    Returns:
        True if cleanup succeeded
    """
    if not worktree_path or not os.path.exists(worktree_path):
        return True

    try:
        # Remove the worktree using git command
        logger.info(f"Removing worktree at {worktree_path}")
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            logger.warning(f"git worktree remove failed: {result.stderr}")
            # Fall back to manual removal
            if os.path.exists(worktree_path):
                shutil.rmtree(worktree_path, ignore_errors=True)

        # Prune any stale worktree references
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=project_workdir,
            capture_output=True,
            text=True,
            timeout=30
        )

        logger.info(f"Cleaned up worktree at {worktree_path}")
        return True

    except Exception as e:
        logger.error(f"Error cleaning up worktree: {e}")
        # Try manual cleanup as last resort
        try:
            if os.path.exists(worktree_path):
                shutil.rmtree(worktree_path, ignore_errors=True)
        except Exception:
            pass
        return False


def cleanup_all_project_worktrees(project_workdir: str, project_name: str) -> int:
    """Clean up all worktrees for a project.

    Args:
        project_workdir: Main project working directory
        project_name: Name of the project

    Returns:
        Number of worktrees cleaned up
    """
    project_worktree_base = os.path.join(WORKTREE_BASE, project_name)
    if not os.path.exists(project_worktree_base):
        return 0

    count = 0
    try:
        for query_id in os.listdir(project_worktree_base):
            worktree_path = os.path.join(project_worktree_base, query_id)
            if os.path.isdir(worktree_path):
                if cleanup_worktree(project_workdir, worktree_path):
                    count += 1
    except Exception as e:
        logger.error(f"Error cleaning up project worktrees: {e}")

    return count
