"""Claude SDK operations for ccc bot."""

import asyncio
import logging
import os
import subprocess
import uuid
from datetime import datetime
from typing import Any

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    ResultMessage,
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock
)

from .messenger import Messenger

logger = logging.getLogger(__name__)


# Session storage for conversation continuity: {project_name: session_id}
PROJECT_SESSIONS = {}

# Running queries storage: {project_name: {query_id: {"task": Task, "command": str, "prompt": str, "started_at": datetime, "worktree_path": str, "project_workdir": str}}}
RUNNING_QUERIES = {}


async def run_claude_query(prompt: str, system_prompt: str, cwd: str, resume: str = None, project_name: str = None, command: str = None, user_prompt: str = None, worktree_path: str = None, project_workdir: str = None, query_id: str = None) -> tuple:
    """Execute Claude query using SDK and return (duration_minutes, session_id).

    Args:
        prompt: Full prompt to send to Claude
        system_prompt: System prompt for Claude
        cwd: Working directory (should be worktree_path if using worktrees)
        resume: Session ID to resume (optional)
        project_name: Project name for tracking
        command: Command type (ask, feat, fix, plan, feedback)
        user_prompt: Original user prompt/request for display
        worktree_path: Path to the worktree (for cleanup)
        project_workdir: Original project workdir (for worktree cleanup)
        query_id: Optional query ID (generated if not provided)
    """
    start_time = datetime.now()
    if not query_id:
        query_id = str(uuid.uuid4())[:8]  # Short ID for easier reference

    options = ClaudeAgentOptions(
        model='opus',
        system_prompt=system_prompt,
        permission_mode='bypassPermissions',
        cwd=cwd,
        setting_sources=["project"],
        resume=resume
    )

    logger.info(f"Starting Claude query in {cwd}" + (f" (resuming session {resume})" if resume else ""))

    # Track the current task if project_name is provided
    if project_name:
        if project_name not in RUNNING_QUERIES:
            RUNNING_QUERIES[project_name] = {}

        RUNNING_QUERIES[project_name][query_id] = {
            "task": asyncio.current_task(),
            "command": command or "query",
            "prompt": user_prompt or prompt[:100],
            "started_at": start_time,
            "worktree_path": worktree_path,
            "project_workdir": project_workdir
        }
        logger.info(f"Tracking query {query_id} for project {project_name}")

    session_id = None
    was_cancelled = False
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                # Process each content block in the message
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"\n[Claude] {block.text}")
                    elif isinstance(block, ThinkingBlock):
                        print(f"\n[Thinking] {block.thinking}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"\n[Tool: {block.name}] {block.input}")
                    elif isinstance(block, ToolResultBlock):
                        result_preview = str(block.content)[:500] if block.content else ""
                        print(f"\n[Tool Result] {result_preview}{'...' if len(str(block.content or '')) > 500 else ''}")
            elif isinstance(message, ResultMessage):
                session_id = message.session_id
                logger.info(f"Query completed: {message.subtype}, turns: {message.num_turns}, session_id: {session_id}")
                print(f"\n[Completed] Turns: {message.num_turns}, Session: {session_id}")
                if message.is_error:
                    logger.error(f"Query error: {message.result}")
                    print(f"[Error] {message.result}")
    except asyncio.CancelledError:
        logger.info(f"Query {query_id} cancelled for project {project_name}")
        was_cancelled = True
    except Exception as e:
        # Catch stream-related exceptions that may occur during cancellation
        if "WouldBlock" in str(type(e).__name__) or "Cancelled" in str(e):
            logger.info(f"Query {query_id} stream interrupted for project {project_name}: {type(e).__name__}")
            was_cancelled = True
        else:
            raise
    finally:
        # Clean up worktree if used
        if worktree_path and project_workdir:
            from . import git
            logger.info(f"Cleaning up worktree for query {query_id}")
            git.cleanup_worktree(project_workdir, worktree_path)

        # Remove from running queries
        if project_name and project_name in RUNNING_QUERIES:
            if query_id in RUNNING_QUERIES[project_name]:
                del RUNNING_QUERIES[project_name][query_id]
            # Clean up empty project entries
            if not RUNNING_QUERIES[project_name]:
                del RUNNING_QUERIES[project_name]

    end_time = datetime.now()
    duration_minutes = (end_time - start_time).total_seconds() / 60

    if was_cancelled:
        logger.info(f"Query was cancelled after {duration_minutes:.2f} minutes")
        raise asyncio.CancelledError(f"Query cancelled for {project_name}")

    logger.info(f"Duration: {duration_minutes:.2f} minutes")

    return duration_minutes, session_id


def get_running_queries_for_project(project_name: str) -> dict:
    """Get all running queries for a project.

    Returns:
        Dict of {query_id: query_info} for the project
    """
    if project_name not in RUNNING_QUERIES:
        return {}

    # Filter out completed tasks
    active = {}
    for query_id, info in RUNNING_QUERIES[project_name].items():
        if not info["task"].done():
            active[query_id] = info
    return active


def cancel_query(project_name: str, query_id: str = None) -> list:
    """Cancel running queries for a project.

    Args:
        project_name: Project name
        query_id: Optional specific query ID. If None, cancels all queries for the project.

    Returns:
        List of cancelled query IDs
    """
    from . import git

    if project_name not in RUNNING_QUERIES:
        return []

    cancelled = []

    if query_id:
        # Cancel specific query
        if query_id in RUNNING_QUERIES[project_name]:
            info = RUNNING_QUERIES[project_name][query_id]
            if not info["task"].done():
                info["task"].cancel()
                cancelled.append(query_id)
                logger.info(f"Cancelled query {query_id} for project {project_name}")
                # Clean up worktree
                worktree_path = info.get("worktree_path")
                project_workdir = info.get("project_workdir")
                if worktree_path and project_workdir:
                    git.cleanup_worktree(project_workdir, worktree_path)
    else:
        # Cancel all queries for the project
        for qid, info in RUNNING_QUERIES[project_name].items():
            if not info["task"].done():
                info["task"].cancel()
                cancelled.append(qid)
                logger.info(f"Cancelled query {qid} for project {project_name}")
                # Clean up worktree
                worktree_path = info.get("worktree_path")
                project_workdir = info.get("project_workdir")
                if worktree_path and project_workdir:
                    git.cleanup_worktree(project_workdir, worktree_path)

    return cancelled


def get_all_running_queries() -> dict:
    """Get all running queries across all projects.

    Returns:
        Dict of {project_name: {query_id: query_info}}
    """
    result = {}
    for project_name, queries in RUNNING_QUERIES.items():
        active = {}
        for query_id, info in queries.items():
            if not info["task"].done():
                active[query_id] = info
        if active:
            result[project_name] = active
    return result


async def initialize_claude_md(messenger: Messenger, context: Any, project_workdir: str) -> bool:
    """Check if CLAUDE.md exists, if not run claude /init to create it, then commit and push.

    Args:
        messenger: Platform-specific messenger for sending replies
        context: Platform-specific context (Telegram update, Lark message dict, etc.)
        project_workdir: Working directory for the project
    """
    claude_md_path = os.path.join(project_workdir, "CLAUDE.md")

    if os.path.exists(claude_md_path):
        logger.info(f"CLAUDE.md already exists in {project_workdir}")
        return True

    await messenger.reply(context, "CLAUDE.md not found. Preparing to initialize codebase...")
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

        # Now run claude /init using SDK
        await messenger.reply(context, "Running claude /init to generate CLAUDE.md...")
        logger.info(f"Running claude /init in {project_workdir}")

        try:
            options = ClaudeAgentOptions(
                model='opus',
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
                await messenger.reply(context, f"Failed to initialize CLAUDE.md:\n{init_error[:500] if init_error else 'Unknown error'}")
                return False

        except Exception as e:
            logger.error(f"claude /init failed: {e}")
            await messenger.reply(context, f"Failed to initialize CLAUDE.md:\n{str(e)[:500]}")
            return False

        await messenger.reply(context, "CLAUDE.md initialized successfully! Committing and pushing to main branch...")
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
                await messenger.reply(context, f"Warning: Could not add CLAUDE.md to git:\n{add_result.stderr[:500]}")
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
                await messenger.reply(context, f"Warning: Could not commit CLAUDE.md:\n{commit_result.stderr[:500]}")
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
                await messenger.reply(context, f"Warning: Could not push CLAUDE.md to main:\n{push_result.stderr[:500]}")
                return True  # Still return True as initialization succeeded

            await messenger.reply(context, "CLAUDE.md committed and pushed to main successfully!")
            logger.info(f"Successfully committed and pushed CLAUDE.md to main in {project_workdir}")

        except subprocess.TimeoutExpired:
            await messenger.reply(context, "Warning: Git operation timed out")
            return True  # Still return True as initialization succeeded
        except Exception as e:
            logger.error(f"Error committing/pushing CLAUDE.md: {e}")
            await messenger.reply(context, f"Warning: Error with git operations: {str(e)}")
            return True  # Still return True as initialization succeeded

        return True

    except subprocess.TimeoutExpired:
        await messenger.reply(context, "Git operation timed out during CLAUDE.md initialization")
        return False
    except Exception as e:
        logger.error(f"Error initializing CLAUDE.md: {e}")
        await messenger.reply(context, f"Error initializing CLAUDE.md: {str(e)}")
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
