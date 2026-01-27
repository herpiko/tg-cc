"""Telegram command handlers for ccc bot."""

import asyncio
import logging
import os
import subprocess
import uuid

from telegram import Update
from telegram.ext import ContextTypes

from ccc import config
from ccc import claude
from ccc import git
from ccc import process
from ccc.telegram.messenger import TelegramMessenger

logger = logging.getLogger(__name__)

# Global messenger instance
_messenger = None


def get_messenger() -> TelegramMessenger:
    """Get or create the global TelegramMessenger instance."""
    global _messenger
    if _messenger is None:
        _messenger = TelegramMessenger()
    return _messenger


def is_authorized(update: Update) -> bool:
    """Check if the user and chat are authorized to use the bot."""
    if not update.message or not update.message.from_user:
        return False

    username = update.message.from_user.username
    chat_id = str(update.message.chat.id)

    logger.info(f"Checking authorization for user: {username}, chat_id: {chat_id}")

    user_authorized = username in config.AUTHORIZED_USERS
    group_authorized = config.is_telegram_group_authorized(chat_id)

    return user_authorized and group_authorized


async def reply(update: Update, text: str):
    """Reply to a message using the messenger."""
    messenger = get_messenger()
    await messenger.reply(update, text)


async def process_output_file(update, output_file: str, duration_minutes: float):
    """Process output file and send to user with cleanup."""
    if os.path.exists(output_file):
        with open(output_file, 'a') as f:
            f.write(f"\n\nExecution time: {duration_minutes:.2f} minutes")

    if os.path.exists(output_file):
        with open(output_file, 'r') as f:
            output_content = f.read()

        if output_content:
            if len(output_content) > 4000:
                await reply(update, output_content[:4000] + "\n\n[Output truncated...]")
            else:
                await reply(update, output_content)
        else:
            await reply(update, f"Command completed but {output_file} is empty")

        os.remove(output_file)
        logger.info(f"Cleaned up {output_file}")
    else:
        await reply(update, f"Error: {output_file} was not created by Claude")


def cleanup_output_file(output_file: str):
    """Clean up output file if it exists."""
    if os.path.exists(output_file):
        os.remove(output_file)
        logger.info(f"Cleaned up {output_file}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all messages and reply if bot is mentioned."""
    if not update.message:
        logger.info("Received update with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use bot")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    message = update.message
    if not message or not message.text:
        logger.info("Received update with no text")
        return

    bot_username = f"@{context.bot.username}"

    logger.info(f"Chat type: {message.chat.type}")
    logger.info(f"Message from: {message.from_user.username}")
    logger.info(f"Message text: {message.text}")
    logger.info(f"Bot username: {bot_username}")

    is_mentioned = False

    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mentioned_text = message.text[entity.offset:entity.offset + entity.length]
                logger.info(f"Found mention: {mentioned_text}")
                if mentioned_text == bot_username:
                    is_mentioned = True
                    break
            elif entity.type == "text_mention":
                if entity.user and entity.user.id == context.bot.id:
                    is_mentioned = True
                    break

    if not is_mentioned and bot_username.lower() in message.text.lower():
        is_mentioned = True
        logger.info("Found bot username in text (case insensitive)")

    if is_mentioned:
        text_without_mention = message.text.replace(bot_username, "").strip()
        logger.info(f"Bot was mentioned! Replying...")

        if text_without_mention:
            await reply(update, text_without_mention)
        else:
            await reply(update, "Hello")

        logger.info(f"Reply sent!")
    else:
        logger.info("Bot was not mentioned in this message")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask command. Format: /ask project-name query"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /ask command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /ask command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /ask command")

    if not context.args or len(context.args) < 2:
        await reply(update, "Usage: /ask project-name query")
        return

    project_name = context.args[0]
    user_text = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Generate query ID and create worktree
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await reply(update, f"Processing for project: {project_name} (query: {query_id})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Query: {user_text}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name}")

        duration_minutes, _ = await claude.run_claude_query(
            prompt, config.ASK_RULES, worktree_path,
            project_name=project_name, command="ask", user_prompt=user_text,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id
        )
        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await reply(update, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_feat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /feat command. Format: /feat project-name prompt"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /feat command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /feat command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /feat command")

    if not context.args or len(context.args) < 2:
        await reply(update, "Usage: /feat project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Clear existing session for this project (starting fresh)
    claude.clear_session(project_name)

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await reply(update, f"Processing for project: {project_name} (query: {query_id})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.FEAT_RULES, worktree_path,
            project_name=project_name, command="feat", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id
        )

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await reply(update, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fix command. Format: /fix project-name prompt"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /fix command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /fix command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /fix command")

    if not context.args or len(context.args) < 2:
        await reply(update, "Usage: /fix project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Clear existing session for this project (starting fresh)
    claude.clear_session(project_name)

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await reply(update, f"Processing for project: {project_name} (query: {query_id})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.FIX_RULES, worktree_path,
            project_name=project_name, command="fix", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id
        )

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await reply(update, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /plan command. Format: /plan project-name prompt"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /plan command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /plan command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /plan command")

    if not context.args or len(context.args) < 2:
        await reply(update, "Usage: /plan project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Clear existing session for this project (starting fresh)
    claude.clear_session(project_name)

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await reply(update, f"Planning for project: {project_name} (query: {query_id})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.PLAN_RULES, worktree_path,
            project_name=project_name, command="plan", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id
        )

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await reply(update, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /feedback command. Format: /feedback project-name prompt"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /feedback command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /feedback command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /feedback command")

    if not context.args or len(context.args) < 2:
        await reply(update, "Usage: /feedback project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Get existing session for this project (to continue conversation)
    existing_session = claude.get_session(project_name)
    if existing_session:
        logger.info(f"Resuming session {existing_session} for project {project_name}")
    else:
        logger.info(f"No existing session for project {project_name}, starting fresh")

    # Generate query ID and create worktree
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    if existing_session:
        await reply(update, f"Continuing session for project: {project_name} (query: {query_id})...")
    else:
        await reply(update, f"No existing session found. Starting new session for project: {project_name} (query: {query_id})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.FEEDBACK_RULES, worktree_path,
            resume=existing_session, project_name=project_name, command="feedback", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id
        )

        # Update session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await reply(update, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /init command. Format: /init project-name"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /init command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /init command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /init command")

    if not context.args or len(context.args) < 1:
        await reply(update, "Usage: /init project-name")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']
    project_up = project.get('project_up')

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    init_success = await claude.initialize_claude_md(messenger, update, project_workdir)

    # Spin up the project regardless of CLAUDE.md initialization result
    if project_up:
        await process.spin_up_project(messenger, update, project_name, project_workdir, project_up)

    if not init_success:
        await reply(update, f"Failed to initialize CLAUDE.md for project: {project_name}")
        return

    await reply(update, f"Successfully initialized CLAUDE.md for project: {project_name}")


async def cmd_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /up command. Format: /up project-name"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /up command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /up command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /up command")

    if not context.args or len(context.args) < 1:
        await reply(update, "Usage: /up project-name")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_workdir = project['project_workdir']
    project_up = project.get('project_up')

    if not project_up:
        await reply(update, f"No project_up command configured for {project_name}")
        return

    await process.spin_up_project(messenger, update, project_name, project_workdir, project_up)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command. Format: /stop project-name"""
    messenger = get_messenger()

    if not update.message:
        logger.info("Received /stop command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /stop command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /stop command")

    if not context.args or len(context.args) < 1:
        await reply(update, "Usage: /stop project-name")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    await process.kill_project_process(messenger, update, project_name)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command. Shows running projects."""
    if not update.message:
        logger.info("Received /status command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /status command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /status command")

    from datetime import datetime as dt
    status_lines = []

    # Check running Claude queries
    running_queries = claude.get_all_running_queries()
    if running_queries:
        status_lines.append("Running Claude queries:")
        for project_name, queries in running_queries.items():
            for query_id, info in queries.items():
                cmd = info.get("command", "query")
                prompt = info.get("prompt", "")[:50]
                if len(info.get("prompt", "")) > 50:
                    prompt += "..."
                started = info.get("started_at")
                if started:
                    elapsed = (dt.now() - started).total_seconds() / 60
                    elapsed_str = f"{elapsed:.1f}m"
                else:
                    elapsed_str = "?"
                status_lines.append(f"  [{query_id}] {project_name} /{cmd}: {prompt} ({elapsed_str})")

        status_lines.append("")
        status_lines.append("Use /cancel <project> to cancel all, or /cancel <project> <id> for specific query")

    # Check running background processes (from /up)
    running_projects = process.get_running_projects()
    if running_projects:
        status_lines.append("\nRunning background processes:")
        for project_name, process_info in running_projects.items():
            proc, log_path, _ = process_info
            if proc.poll() is None:
                status_lines.append(f"  - {project_name} (PID: {proc.pid})")
            else:
                status_lines.append(f"  - {project_name} (PID: {proc.pid}, exited with code {proc.returncode})")

    if not status_lines:
        await reply(update, "No running queries or processes.")
        return

    await reply(update, "\n".join(status_lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command. Format: /cancel [project-name] [query-id]"""
    if not update.message:
        logger.info("Received /cancel command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /cancel command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /cancel command")

    # If no project specified, cancel all queries across all projects
    if not context.args or len(context.args) < 1:
        running = claude.get_all_running_queries()
        if not running:
            await reply(update, "No running queries to cancel.")
            return

        # Cancel all running queries
        all_cancelled = []
        for project_name in list(running.keys()):
            cancelled = claude.cancel_query(project_name)
            all_cancelled.extend([f"{project_name}:{qid}" for qid in cancelled])

        if all_cancelled:
            await reply(update, f"Cancelled {len(all_cancelled)} queries: {', '.join(all_cancelled)}")
        else:
            await reply(update, "No queries were cancelled.")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    # Check if a specific query ID was provided
    query_id = context.args[1] if len(context.args) > 1 else None

    # Check if there are running queries for this project
    queries = claude.get_running_queries_for_project(project_name)
    if not queries:
        await reply(update, f"No running queries for project {project_name}.")
        return

    if query_id:
        # Cancel specific query
        if query_id not in queries:
            await reply(update, f"Query ID '{query_id}' not found for project {project_name}. Running queries: {', '.join(queries.keys())}")
            return

        cancelled = claude.cancel_query(project_name, query_id)
        if cancelled:
            await reply(update, f"Cancelled query {query_id} for project {project_name}.")
        else:
            await reply(update, f"Failed to cancel query {query_id} for project {project_name}.")
    else:
        # Cancel all queries for the project
        cancelled = claude.cancel_query(project_name)
        if cancelled:
            await reply(update, f"Cancelled {len(cancelled)} queries for project {project_name}: {', '.join(cancelled)}")
        else:
            await reply(update, f"Failed to cancel queries for project {project_name}.")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /log command. Format: /log project-name [lines]"""
    if not update.message:
        logger.info("Received /log command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /log command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /log command")

    if not context.args or len(context.args) < 1:
        await reply(update, "Usage: /log project-name [lines]\nDefault: 50 lines")
        return

    project_name = context.args[0]

    # Optional: number of lines (default 50)
    lines = 50
    if len(context.args) >= 2:
        try:
            lines = int(context.args[1])
            lines = min(max(lines, 1), 200)  # Clamp between 1 and 200
        except ValueError:
            await reply(update, "Invalid number of lines. Using default (50).")

    project = config.get_project(project_name)
    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    # Check if project is running
    is_running, pid, _ = process.get_process_status(project_name)
    if not is_running and pid is None:
        await reply(update, f"No running instance found for project {project_name}. Use /up to start it.")
        return

    # Get logs
    logs = process.get_project_logs(project_name, lines)
    if not logs:
        await reply(update, f"No logs available for project {project_name}.")
        return

    # Format output
    status = "running" if is_running else f"exited"
    header = f"Logs for {project_name} (PID: {pid}, {status}) - last {lines} lines:\n\n"

    output = header + logs
    if len(output) > 4000:
        output = output[:4000] + "\n\n[Output truncated...]"

    await reply(update, output)


async def cmd_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cost command. Displays Claude usage costs via claude-monitor."""
    if not update.message:
        logger.info("Received /cost command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /cost command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /cost command")

    await reply(update, "Fetching Claude usage costs...")

    log_file = "claude-monitor.log"

    try:
        # Run the claude-monitor command
        cmd = f"rm {log_file} || true && claude-monitor --view daily >{log_file} 2>&1 < /dev/null & sleep 3 && pkill -f \"claude-monitor --view\""

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )

        logger.info(f"claude-monitor command completed with return code: {result.returncode}")

        prompt = f"""
Please edit the {log_file}, just take the Summary, remove everything else. Also remove the ASCII lines and make it chat message friendly.
"""

        project_workdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        _, _ = await claude.run_claude_query(prompt, config.ASK_RULES, project_workdir)

        # Read the log file instead of stdout
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                log_content = f.read()

            if log_content:
                if len(log_content) > 4000:
                    await reply(update, log_content[:4000] + "\n\n[Output truncated...]")
                else:
                    await reply(update, log_content)
            else:
                await reply(update, "No cost data available in log file.")
        else:
            await reply(update, "claude-monitor.log file not found. Make sure claude-monitor is installed.")

    except subprocess.TimeoutExpired:
        await reply(update, "Command timed out after 30 seconds")
    except Exception as e:
        logger.error(f"Error running claude-monitor command: {e}")
        await reply(update, f"Error fetching cost data: {str(e)}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command. Display available commands."""
    if not update.message:
        logger.info("Received /help command with no message object")
        return

    help_text = """Available commands:

/help
  Show this help message

/ask <project-name> <query>
  Ask a question about a project

/feat <project-name> <task>
  Implement a new feature (creates new branch, commits, and opens MR)
  Starts a new session (clears previous context)

/fix <project-name> <issue>
  Fix a bug (creates new branch, commits, and opens MR)
  Starts a new session (clears previous context)

/plan <project-name> <task>
  Plan and explore a task (creates new branch)
  Starts a new session (clears previous context)

/feedback <project-name> <feedback>
  Continue work on the current branch with context from previous /feat, /fix, or /plan

/init <project-name>
  Initialize CLAUDE.md for a project and spin up if project_up is configured

/up <project-name>
  Spin up a project using project_up command

/stop <project-name>
  Stop a running project

/status
  Show running projects

/cancel [project-name] [query-id]
  Cancel running Claude queries. Without args: cancel all.
  With project: cancel all for that project.
  With project + id: cancel specific query.

/log <project-name> [lines]
  Show last N lines of project logs (default: 50)

/cost
  Display Claude API usage costs

/selfupdate
  Update bot from GitHub and restart"""

    await reply(update, help_text)


async def cmd_selfupdate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /selfupdate command. Updates bot from GitHub and restarts."""
    import sys
    import shutil

    if not update.message:
        logger.info("Received /selfupdate command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /selfupdate command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /selfupdate command")

    # Get the bot's installation directory
    bot_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config_path = os.path.join(bot_dir, "config.yaml")
    config_backup_path = os.path.join("/tmp", "ccc_config_backup.yaml")

    # Get current commit
    current_commit_result = subprocess.run(
        ["git", "log", "-1", "--format=%h %s"],
        cwd=bot_dir,
        capture_output=True,
        text=True,
        timeout=10
    )
    current_commit = current_commit_result.stdout.strip() if current_commit_result.returncode == 0 else "unknown"

    await reply(update, f"Starting self-update...\nCurrent: {current_commit}")

    try:
        # Backup config.yaml
        if os.path.exists(config_path):
            shutil.copy2(config_path, config_backup_path)
            logger.info(f"Backed up config.yaml to {config_backup_path}")

        # Fetch latest from origin
        await reply(update, "Fetching latest code from GitHub...")
        fetch_result = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if fetch_result.returncode != 0:
            await reply(update, f"Failed to fetch: {fetch_result.stderr[:500]}")
            return

        # Reset to origin/main
        reset_result = subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if reset_result.returncode != 0:
            await reply(update, f"Failed to reset: {reset_result.stderr[:500]}")
            return

        # Restore config.yaml
        if os.path.exists(config_backup_path):
            shutil.copy2(config_backup_path, config_path)
            logger.info(f"Restored config.yaml from backup")

        # Reinstall package (in case dependencies changed)
        await reply(update, "Reinstalling package...")
        pip_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=300
        )

        if pip_result.returncode != 0:
            await reply(update, f"Warning: pip install failed: {pip_result.stderr[:500]}")
            # Continue anyway, the code update might still work

        # Get new commit
        new_commit_result = subprocess.run(
            ["git", "log", "-1", "--format=%h %s"],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        new_commit = new_commit_result.stdout.strip() if new_commit_result.returncode == 0 else "unknown"

        await reply(update, f"Update complete! Restarting bot...\nUpdated to: {new_commit}")
        logger.info(f"Self-update complete ({current_commit} -> {new_commit}), restarting...")

        # Give telegram time to send the message
        import asyncio
        await asyncio.sleep(1)

        # Restart the process
        os.execv(sys.executable, [sys.executable, "-m", "ccc"] + sys.argv[1:])

    except subprocess.TimeoutExpired:
        await reply(update, "Update timed out")
    except Exception as e:
        logger.error(f"Error during self-update: {e}")
        await reply(update, f"Error during self-update: {str(e)}")

        # Try to restore config if something went wrong
        if os.path.exists(config_backup_path) and not os.path.exists(config_path):
            shutil.copy2(config_backup_path, config_path)
            logger.info("Restored config.yaml after error")
