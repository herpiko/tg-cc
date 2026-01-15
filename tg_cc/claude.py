"""Claude SDK operations for tg-cc bot."""

import asyncio
import logging
import os
import subprocess
from datetime import datetime

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    ResultMessage
)

logger = logging.getLogger(__name__)


# Session storage for conversation continuity: {project_name: session_id}
PROJECT_SESSIONS = {}

# Running queries storage: {project_name: asyncio.Task}
RUNNING_QUERIES = {}


async def run_claude_query(prompt: str, system_prompt: str, cwd: str, resume: str = None, project_name: str = None) -> tuple:
    """Execute Claude query using SDK and return (duration_minutes, session_id)."""
    start_time = datetime.now()

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        permission_mode='bypassPermissions',
        cwd=cwd,
        setting_sources=["project"],
        resume=resume
    )

    logger.info(f"Starting Claude query in {cwd}" + (f" (resuming session {resume})" if resume else ""))

    # Track the current task if project_name is provided
    if project_name:
        RUNNING_QUERIES[project_name] = asyncio.current_task()
        logger.info(f"Tracking query for project {project_name}")

    session_id = None
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                logger.info(f"Query completed: {message.subtype}, turns: {message.num_turns}, session_id: {session_id}")
                if message.is_error:
                    logger.error(f"Query error: {message.result}")
    except asyncio.CancelledError:
        logger.info(f"Query cancelled for project {project_name}")
        raise
    finally:
        # Remove from running queries
        if project_name and project_name in RUNNING_QUERIES:
            del RUNNING_QUERIES[project_name]

    end_time = datetime.now()
    duration_minutes = (end_time - start_time).total_seconds() / 60

    logger.info(f"Duration: {duration_minutes:.2f} minutes")

    return duration_minutes, session_id


def get_running_query(project_name: str) -> asyncio.Task | None:
    """Get running query task for a project."""
    return RUNNING_QUERIES.get(project_name)


def cancel_query(project_name: str) -> bool:
    """Cancel a running query for a project. Returns True if cancelled."""
    task = RUNNING_QUERIES.get(project_name)
    if task and not task.done():
        task.cancel()
        logger.info(f"Cancelled query for project {project_name}")
        return True
    return False


def get_all_running_queries() -> dict:
    """Get all running queries."""
    return {k: v for k, v in RUNNING_QUERIES.items() if not v.done()}


async def initialize_claude_md(update, project_workdir: str) -> bool:
    """Check if CLAUDE.md exists, if not run claude /init to create it, then commit and push."""
    claude_md_path = os.path.join(project_workdir, "CLAUDE.md")

    if os.path.exists(claude_md_path):
        logger.info(f"CLAUDE.md already exists in {project_workdir}")
        return True

    await update.message.reply_text("CLAUDE.md not found. Preparing to initialize codebase...")
    logger.info(f"Preparing to run claude /init in {project_workdir}")

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
            await update.message.reply_text(f"Warning: Could not clean branch:\n{reset_result.stderr[:500]}")

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
            await update.message.reply_text(f"Warning: Could not checkout main:\n{checkout_result.stderr[:500]}")

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
            await update.message.reply_text(f"Warning: Could not pull from main:\n{pull_result.stderr[:500]}")

        # Now run claude /init using SDK
        await update.message.reply_text("Running claude /init to generate CLAUDE.md...")
        logger.info(f"Running claude /init in {project_workdir}")

        try:
            options = ClaudeAgentOptions(
                permission_mode='bypassPermissions',
                cwd=project_workdir
            )

            init_error = None
            async for message in query(prompt="/init", options=options):
                if isinstance(message, ResultMessage):
                    if message.is_error:
                        init_error = message.result
                        logger.error(f"claude /init failed: {init_error}")

            if init_error:
                await update.message.reply_text(f"Failed to initialize CLAUDE.md:\n{init_error[:500] if init_error else 'Unknown error'}")
                return False

        except Exception as e:
            logger.error(f"claude /init failed: {e}")
            await update.message.reply_text(f"Failed to initialize CLAUDE.md:\n{str(e)[:500]}")
            return False

        await update.message.reply_text("CLAUDE.md initialized successfully! Committing and pushing to main branch...")
        logger.info(f"Successfully initialized CLAUDE.md in {project_workdir}")

        # Commit and push CLAUDE.md to main
        try:
            # Add CLAUDE.md to git
            add_result = subprocess.run(
                ["git", "add", "CLAUDE.md"],
                cwd=project_workdir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if add_result.returncode != 0:
                logger.error(f"git add failed: {add_result.stderr}")
                await update.message.reply_text(f"Warning: Could not add CLAUDE.md to git:\n{add_result.stderr[:500]}")
                return True  # Still return True as initialization succeeded

            # Commit CLAUDE.md
            commit_message = """Add CLAUDE.md documentation for codebase architecture

Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"""

            commit_result = subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=project_workdir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if commit_result.returncode != 0:
                logger.error(f"git commit failed: {commit_result.stderr}")
                await update.message.reply_text(f"Warning: Could not commit CLAUDE.md:\n{commit_result.stderr[:500]}")
                return True  # Still return True as initialization succeeded

            # Push to main branch
            push_result = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=project_workdir,
                capture_output=True,
                text=True,
                timeout=300
            )

            if push_result.returncode != 0:
                logger.error(f"git push failed: {push_result.stderr}")
                await update.message.reply_text(f"Warning: Could not push CLAUDE.md to main:\n{push_result.stderr[:500]}")
                return True  # Still return True as initialization succeeded

            await update.message.reply_text("CLAUDE.md committed and pushed to main successfully!")
            logger.info(f"Successfully committed and pushed CLAUDE.md to main in {project_workdir}")

        except subprocess.TimeoutExpired:
            await update.message.reply_text("Warning: Git operation timed out")
            return True  # Still return True as initialization succeeded
        except Exception as e:
            logger.error(f"Error committing/pushing CLAUDE.md: {e}")
            await update.message.reply_text(f"Warning: Error with git operations: {str(e)}")
            return True  # Still return True as initialization succeeded

        return True

    except subprocess.TimeoutExpired:
        await update.message.reply_text("Git operation timed out during CLAUDE.md initialization")
        return False
    except Exception as e:
        logger.error(f"Error initializing CLAUDE.md: {e}")
        await update.message.reply_text(f"Error initializing CLAUDE.md: {str(e)}")
        return False


def get_session(project_name: str) -> str | None:
    """Get stored session ID for a project."""
    return PROJECT_SESSIONS.get(project_name)


def set_session(project_name: str, session_id: str):
    """Store session ID for a project."""
    if session_id:
        PROJECT_SESSIONS[project_name] = session_id
        logger.info(f"Stored session {session_id} for project {project_name}")


def clear_session(project_name: str):
    """Clear stored session ID for a project."""
    PROJECT_SESSIONS.pop(project_name, None)
    logger.info(f"Cleared existing session for project {project_name}")
