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


def get_thread_key(update: Update) -> str:
    """Get the thread key for this Telegram update."""
    message = update.message
    chat_id = str(message.chat.id)
    # Use 'is not None' to handle thread_id = 0 correctly
    thread_id = str(message.message_thread_id) if message.message_thread_id is not None else None
    key = claude.get_thread_key_telegram(chat_id, thread_id)
    logger.debug(f"Thread key for chat {chat_id}, thread_id {message.message_thread_id}: {key}")
    return key


def get_thread_key_with_fallback(update: Update) -> tuple[str, dict | None]:
    """Get the thread key and try to find worktree context with fallback.

    Returns:
        Tuple of (thread_key, worktree_info or None)
    """
    message = update.message
    chat_id = str(message.chat.id)

    # Primary: try with message_thread_id
    thread_id = str(message.message_thread_id) if message.message_thread_id is not None else None
    primary_key = claude.get_thread_key_telegram(chat_id, thread_id)
    worktree_info = claude.get_thread_worktree(primary_key)

    if worktree_info:
        logger.info(f"Found thread context for key {primary_key}")
        return primary_key, worktree_info

    # Fallback 1: If we have a thread_id, try the 'main' key for the same chat
    if thread_id:
        fallback_key = claude.get_thread_key_telegram(chat_id, None)
        worktree_info = claude.get_thread_worktree(fallback_key)
        if worktree_info:
            logger.info(f"Found thread context using fallback key {fallback_key}")
            return fallback_key, worktree_info

    # Fallback 2: If we don't have a thread_id, search for any thread in this chat
    if not thread_id:
        all_worktrees = claude.get_all_thread_worktrees()
        chat_prefix = f"telegram:{chat_id}:"
        for key, info in all_worktrees.items():
            if key.startswith(chat_prefix):
                logger.info(f"Found thread context for chat using key {key}")
                return key, info

    logger.info(f"No thread context found for key {primary_key}")
    return primary_key, None


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
    """Handle all messages and reply if bot is mentioned.

    If mentioned in a thread with an active worktree context, continue the conversation there.
    """
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
        logger.info(f"Bot was mentioned!")

        # Skip if it's a command (starts with /)
        if text_without_mention.startswith('/'):
            logger.info("Message is a command, skipping mention handler")
            return

        if not text_without_mention:
            await reply(update, "Hello! Mention me with a message to continue our conversation.")
            return

        # Check if this thread has an active worktree context
        thread_key, worktree_info = get_thread_key_with_fallback(update)

        if worktree_info:
            # Continue conversation in the worktree context
            await _continue_in_worktree(update, text_without_mention, worktree_info, thread_key)
        else:
            # No worktree context, treat as casual conversation
            messenger = get_messenger()
            await _ask_casual(update, messenger, text_without_mention)

        logger.info(f"Reply sent!")
    else:
        logger.info("Bot was not mentioned in this message")


async def _continue_in_worktree(update: Update, user_text: str, worktree_info: dict, thread_key: str) -> None:
    """Continue conversation in an existing worktree context."""
    messenger = get_messenger()
    query_id = worktree_info["query_id"]
    worktree_path = worktree_info["worktree_path"]
    project_workdir = worktree_info["project_workdir"]
    project_name = worktree_info["project_name"]
    project_repo = worktree_info["project_repo"]
    existing_session = worktree_info.get("session_id")

    # Check if this is a casual conversation context
    if query_id.startswith("casual-") or project_name == "_casual":
        logger.info(f"Continuing casual conversation with session {existing_session}")
        await _ask_casual(update, messenger, user_text, existing_session)
        return

    logger.info(f"Continuing in worktree {query_id} for project {project_name}")

    # Check if this is from /up command (pseudo worktree) or if worktree doesn't exist
    is_up_context = query_id.startswith("up-")
    worktree_exists = worktree_path and os.path.isdir(worktree_path)

    if is_up_context or not worktree_exists:
        # Need to create a proper worktree for continuation
        logger.info(f"Worktree doesn't exist or is /up context, creating new worktree")
        query_id = str(uuid.uuid4())[:8]
        worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
        if not worktree_path:
            await reply(update, f"Failed to create worktree for continuation in {project_name}")
            return
        # Clear session since we're in a new worktree
        existing_session = None

    await reply(update, f"Continuing in worktree {query_id} for {project_name}...")

    output_file = f"/tmp/output_{query_id}_cont_{str(uuid.uuid4())[:4]}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_text}

Write the output in {output_file}"""

        logger.info(f"Running continuation query in worktree {query_id}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.FEEDBACK_RULES, worktree_path,
            resume=existing_session, project_name=project_name, command="continue", user_prompt=user_text,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True
        )

        # Update thread worktree association with new info
        claude.set_thread_worktree(
            thread_key, query_id, session_id,
            worktree_path, project_workdir, project_name, project_repo
        )

        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Continuation query in worktree {query_id} was cancelled")
        await reply(update, f"Query in {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running continuation query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask command. Format: /ask [project-name] query

    If project-name is provided and exists, ask about that project.
    Otherwise, treat as casual conversation with the agent.
    """
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

    if not context.args or len(context.args) < 1:
        await reply(update, "Usage: /ask [project-name] query\n\nWith project-name: Ask about a specific project\nWithout project-name: Casual conversation with the agent")
        return

    # Check if first arg is a project name
    potential_project = context.args[0]
    project = config.get_project(potential_project)

    if project and len(context.args) >= 2:
        # Project-specific query
        project_name = potential_project
        user_text = " ".join(context.args[1:])
        await _ask_project(update, messenger, project_name, project, user_text)
    else:
        # Casual conversation (no project context)
        user_text = " ".join(context.args)
        await _ask_casual(update, messenger, user_text)


async def _ask_project(update: Update, messenger, project_name: str, project: dict, user_text: str) -> None:
    """Handle project-specific /ask query."""
    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(update)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /ask: {error}")
        await reply(update, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    # Associate thread with worktree BEFORE calling Claude
    try:
        claude.set_thread_worktree(
            thread_key, query_id, None,  # session_id is None initially
            worktree_path, project_workdir, project_name, project_repo
        )
        logger.info(f"Pre-registered thread {thread_key} with worktree {query_id}")
    except ValueError as e:
        logger.error(f"Failed to associate thread with worktree: {e}")
        await reply(update, f"Error: Failed to set up thread context. {str(e)}")
        return

    await reply(update, f"Processing for project: {project_name} (query: {query_id}, thread: {thread_key})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Query: {user_text}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name} in thread {thread_key}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.ASK_RULES, worktree_path,
            project_name=project_name, command="ask", user_prompt=user_text,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep for thread context
        )

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await reply(update, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await reply(update, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def _ask_casual(update: Update, messenger, user_text: str, existing_session: str = None) -> None:
    """Handle casual conversation /ask query (no project context)."""
    query_id = str(uuid.uuid4())[:8]

    # Get and validate thread key
    thread_key = get_thread_key(update)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for casual /ask: {error}")
        await reply(update, f"Error: Cannot determine thread context. {error}")
        return

    # Pre-register thread context BEFORE calling Claude
    try:
        claude.set_thread_worktree(
            thread_key, f"casual-{query_id}", None,  # session_id is None initially
            None, None, "_casual", None  # No worktree for casual queries
        )
        logger.info(f"Pre-registered thread {thread_key} for casual query {query_id}")
    except ValueError as e:
        logger.error(f"Failed to set up thread context: {e}")
        await reply(update, f"Error: Failed to set up thread context. {str(e)}")
        return

    if existing_session:
        await reply(update, f"Continuing casual conversation (query: {query_id}, thread: {thread_key})...")
    else:
        await reply(update, f"Processing casual query (query: {query_id}, thread: {thread_key})...")

    output_file = f"/tmp/output_{query_id}.txt"

    # Use a temporary directory for casual queries
    cwd = "/tmp"

    try:
        prompt = f"""You are a helpful assistant. Please respond to the following query.

Query: {user_text}

IMPORTANT: Write your complete response to the file {output_file}. Use the Write tool to create this file with your response."""

        logger.info(f"Running casual query {query_id} in thread {thread_key}" + (f" (resuming session {existing_session})" if existing_session else ""))

        # Use GENERAL_RULES for casual queries, fall back to empty string
        system_prompt = config.GENERAL_RULES if config.GENERAL_RULES else ""

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, system_prompt, cwd,
            resume=existing_session,
            project_name="_casual", command="ask", user_prompt=user_text,
            query_id=query_id
        )

        # Update thread context with session_id
        claude.update_thread_session(thread_key, session_id)

        # Check if output file exists, provide fallback message if not
        if os.path.exists(output_file):
            await process_output_file(update, output_file, duration_minutes)
        else:
            await reply(update, f"Query completed in {duration_minutes:.2f} minutes, but no output file was generated. The assistant may have responded directly in the logs.")

    except asyncio.CancelledError:
        logger.info(f"Casual query {query_id} was cancelled")
        await reply(update, f"Query {query_id} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running casual query: {e}")
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

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(update)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /feat: {error}")
        await reply(update, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    # Associate thread with worktree BEFORE calling Claude
    try:
        claude.set_thread_worktree(
            thread_key, query_id, None,  # session_id is None initially
            worktree_path, project_workdir, project_name, project_repo
        )
        logger.info(f"Pre-registered thread {thread_key} with worktree {query_id}")
    except ValueError as e:
        logger.error(f"Failed to associate thread with worktree: {e}")
        await reply(update, f"Error: Failed to set up thread context. {str(e)}")
        return

    await reply(update, f"Processing for project: {project_name} (query: {query_id}, thread: {thread_key})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name} in thread {thread_key}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.FEAT_RULES, worktree_path,
            project_name=project_name, command="feat", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep worktree for potential feedback
        )

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

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

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(update)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /fix: {error}")
        await reply(update, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    # Associate thread with worktree BEFORE calling Claude
    try:
        claude.set_thread_worktree(
            thread_key, query_id, None,  # session_id is None initially
            worktree_path, project_workdir, project_name, project_repo
        )
        logger.info(f"Pre-registered thread {thread_key} with worktree {query_id}")
    except ValueError as e:
        logger.error(f"Failed to associate thread with worktree: {e}")
        await reply(update, f"Error: Failed to set up thread context. {str(e)}")
        return

    await reply(update, f"Processing for project: {project_name} (query: {query_id}, thread: {thread_key})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name} in thread {thread_key}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.FIX_RULES, worktree_path,
            project_name=project_name, command="fix", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep worktree for potential feedback
        )

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

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

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(update)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /plan: {error}")
        await reply(update, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, update, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    # Associate thread with worktree BEFORE calling Claude
    try:
        claude.set_thread_worktree(
            thread_key, query_id, None,  # session_id is None initially
            worktree_path, project_workdir, project_name, project_repo
        )
        logger.info(f"Pre-registered thread {thread_key} with worktree {query_id}")
    except ValueError as e:
        logger.error(f"Failed to associate thread with worktree: {e}")
        await reply(update, f"Error: Failed to set up thread context. {str(e)}")
        return

    await reply(update, f"Planning for project: {project_name} (query: {query_id}, thread: {thread_key})...")

    output_file = f"/tmp/output_{query_id}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {worktree_path}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query {query_id} for project {project_name} in thread {thread_key}")

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, config.PLAN_RULES, worktree_path,
            project_name=project_name, command="plan", user_prompt=user_prompt,
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep worktree for potential feedback
        )

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

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
    """Handle /feedback command. Format: /feedback [project-name] [job-id] prompt

    If project-name is not provided, uses the project and worktree from thread context.
    """
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

    if not context.args or len(context.args) < 1:
        await reply(update, "Usage: /feedback [project-name] [job-id] prompt\n\nUse /status to see available job IDs.")
        return

    # Try to determine project from args or thread context
    thread_key, worktree_info = get_thread_key_with_fallback(update)

    project_name = None
    project = None
    job_id = None
    user_prompt = None
    args_index = 0

    # Check if first arg is a project name
    first_arg = context.args[0]
    if config.get_project(first_arg):
        project_name = first_arg
        project = config.get_project(project_name)
        args_index = 1
    elif worktree_info:
        # Use thread context for project
        project_name = worktree_info.get("project_name")
        project = config.get_project(project_name)
    else:
        await reply(update, f"Project '{first_arg}' not found and no project context in this thread.\n\nAvailable projects: {config.get_available_projects()}")
        return

    if not project:
        await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    # Check remaining args for job-id and prompt
    remaining_args = context.args[args_index:]
    if remaining_args:
        potential_job_id = remaining_args[0]
        if len(potential_job_id) == 8 and claude.get_completed_job(potential_job_id):
            job_id = potential_job_id
            user_prompt = " ".join(remaining_args[1:]) if len(remaining_args) > 1 else ""
        else:
            user_prompt = " ".join(remaining_args)
    else:
        user_prompt = ""

    if not user_prompt:
        await reply(update, "Usage: /feedback [project-name] [job-id] prompt\n\nPlease provide feedback text.")
        return

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, update, project_workdir):
        return

    # Determine worktree and session
    if job_id:
        # Use existing job's worktree and session
        job_info = claude.get_completed_job(job_id)
        if not job_info:
            await reply(update, f"Job '{job_id}' not found. Use /status to see available jobs.")
            return

        if job_info.get("project_name") != project_name:
            await reply(update, f"Job '{job_id}' belongs to project '{job_info.get('project_name')}', not '{project_name}'.")
            return

        worktree_path = job_info.get("worktree_path")
        existing_session = job_info.get("session_id")
        query_id = job_id

        logger.info(f"Resuming job {job_id} with session {existing_session} in worktree {worktree_path}")
        await reply(update, f"Continuing job {job_id} for project: {project_name}...")

        del claude.COMPLETED_JOBS[job_id]
    elif worktree_info and worktree_info.get("project_name") == project_name:
        # Use thread's worktree context
        worktree_path = worktree_info.get("worktree_path")
        existing_session = worktree_info.get("session_id")
        query_id = worktree_info.get("query_id")

        logger.info(f"Using thread worktree {query_id} with session {existing_session}")
        await reply(update, f"Continuing in worktree {query_id} for project: {project_name}...")
    else:
        # Create new worktree
        existing_session = claude.get_session(project_name)
        if existing_session:
            logger.info(f"Resuming session {existing_session} for project {project_name}")
        else:
            logger.info(f"No existing session for project {project_name}, starting fresh")

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
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True
        )

        # Update session for future commands
        claude.set_session(project_name, session_id)

        # Update thread-worktree association
        claude.set_thread_worktree(
            thread_key, query_id, session_id,
            worktree_path, project_workdir, project_name, project_repo
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
    project_endpoint_url = project.get('project_endpoint_url')
    project_ports = project.get('project_ports')

    if not await git.clone_repository_if_needed(messenger, update, project_repo, project_workdir):
        return

    init_success = await claude.initialize_claude_md(messenger, update, project_workdir)

    # Spin up the project regardless of CLAUDE.md initialization result
    if project_up:
        # Clean up workdir and pull from main before spinning up
        if not await git.refresh_to_main_branch(messenger, update, project_workdir):
            return
        await process.spin_up_project(messenger, update, project_name, project_workdir, project_up, project_endpoint_url, project_ports)

    if not init_success:
        await reply(update, f"Failed to initialize CLAUDE.md for project: {project_name}")
        return

    await reply(update, f"Successfully initialized CLAUDE.md for project: {project_name}")


async def cmd_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /up command. Format: /up [project-name]

    If project-name is not provided, uses the project from thread context.
    """
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

    # Get project name from args or thread context
    project_name = None
    project = None

    if context.args and len(context.args) >= 1:
        project_name = context.args[0]
        project = config.get_project(project_name)
        if not project:
            await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
            return
    else:
        # Try to get project from thread context with fallback
        thread_key, worktree_info = get_thread_key_with_fallback(update)
        if worktree_info:
            project_name = worktree_info.get("project_name")
            project = config.get_project(project_name)

    if not project_name or not project:
        await reply(update, "Usage: /up [project-name]\n\nNo project specified and no project context in this thread.")
        return

    project_workdir = project['project_workdir']
    project_up = project.get('project_up')
    project_endpoint_url = project.get('project_endpoint_url')
    project_ports = project.get('project_ports')
    project_repo = project.get('project_repo')

    if not project_up:
        await reply(update, f"No project_up command configured for {project_name}")
        return

    # Get current thread key
    thread_key = get_thread_key(update)

    # Clear any existing thread associations for this project from other threads
    # This ensures only one thread is associated with the running project
    for existing_key, info in list(claude.get_all_thread_worktrees().items()):
        if info.get("project_name") == project_name and existing_key != thread_key:
            claude.clear_thread_worktree(existing_key)
            logger.info(f"Cleared old thread association {existing_key} for project {project_name}")

    # Clean up workdir and pull from main before spinning up
    if not await git.refresh_to_main_branch(messenger, update, project_workdir):
        return

    await process.spin_up_project(messenger, update, project_name, project_workdir, project_up, project_endpoint_url, project_ports)

    # Associate this thread with the project (even without a worktree for /up)
    # Create a pseudo worktree entry for the project context
    claude.set_thread_worktree(
        thread_key, f"up-{project_name}", None,
        project_workdir, project_workdir, project_name, project_repo
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command. Format: /stop [project-name]

    If project-name is not provided, uses the project from thread context.
    """
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

    # Get project name from args or thread context
    project_name = None

    if context.args and len(context.args) >= 1:
        project_name = context.args[0]
        project = config.get_project(project_name)
        if not project:
            await reply(update, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
            return
    else:
        # Try to get project from thread context with fallback
        thread_key, worktree_info = get_thread_key_with_fallback(update)
        if worktree_info:
            project_name = worktree_info.get("project_name")

    if not project_name:
        await reply(update, "Usage: /stop [project-name]\n\nNo project specified and no project context in this thread.")
        return

    await process.kill_project_process(messenger, update, project_name)


async def cmd_down(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /down command. Alias for /stop. Format: /down project-name"""
    await cmd_stop(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command. Shows running projects and completed jobs."""
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
        status_lines.append("Use /cancel <project> [id] to cancel queries")

    # Check completed jobs available for feedback
    completed_jobs = claude.COMPLETED_JOBS
    if completed_jobs:
        status_lines.append("\nCompleted jobs (available for /feedback):")
        for job_id, info in completed_jobs.items():
            project = info.get("project_name", "?")
            cmd = info.get("command", "?")
            completed = info.get("completed_at")
            if completed:
                age = (dt.now() - completed).total_seconds() / 60
                if age < 60:
                    age_str = f"{age:.0f}m ago"
                else:
                    age_str = f"{age/60:.1f}h ago"
            else:
                age_str = "?"
            status_lines.append(f"  [{job_id}] {project} /{cmd} ({age_str})")

        status_lines.append("")
        status_lines.append("Use /feedback <project> <job-id> <prompt> to continue")

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
        await reply(update, "No running queries, completed jobs, or processes.")
        return

    await reply(update, "\n".join(status_lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command. Format: /cancel [project-name] [query-id]

    If no args and thread has context, cancel queries for that project.
    If no args and no thread context, cancel all running queries.
    """
    if not update.message:
        logger.info("Received /cancel command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /cancel command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /cancel command")

    project_name = None
    query_id = None

    # Get thread context with fallback once
    thread_key, worktree_info = get_thread_key_with_fallback(update)

    if context.args and len(context.args) >= 1:
        # Check if first arg is a project name or query ID
        potential_project = context.args[0]
        if config.get_project(potential_project):
            project_name = potential_project
            query_id = context.args[1] if len(context.args) > 1 else None
        else:
            # First arg might be a query ID if thread has context
            if worktree_info:
                project_name = worktree_info.get("project_name")
                query_id = potential_project  # Treat first arg as query ID
            else:
                await reply(update, f"Project '{potential_project}' not found. Available projects: {config.get_available_projects()}")
                return
    else:
        # No args - try thread context
        if worktree_info:
            project_name = worktree_info.get("project_name")
            query_id = worktree_info.get("query_id")

    # If still no project, cancel all
    if not project_name:
        running = claude.get_all_running_queries()
        if not running:
            await reply(update, "No running queries to cancel.")
            return

        all_cancelled = []
        for pname in list(running.keys()):
            cancelled = claude.cancel_query(pname)
            all_cancelled.extend([f"{pname}:{qid}" for qid in cancelled])

        if all_cancelled:
            await reply(update, f"Cancelled {len(all_cancelled)} queries: {', '.join(all_cancelled)}")
        else:
            await reply(update, "No queries were cancelled.")
        return

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
    """Handle /log command. Format: /log [project-name] [lines]

    If project-name is not provided, uses the project from thread context.
    """
    if not update.message:
        logger.info("Received /log command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /log command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /log command")

    project_name = None
    lines = 50

    if context.args and len(context.args) >= 1:
        # Check if first arg is a project name or number of lines
        first_arg = context.args[0]
        if config.get_project(first_arg):
            project_name = first_arg
            if len(context.args) >= 2:
                try:
                    lines = int(context.args[1])
                    lines = min(max(lines, 1), 200)
                except ValueError:
                    await reply(update, "Invalid number of lines. Using default (50).")
        else:
            # First arg might be lines if thread has context
            try:
                lines = int(first_arg)
                lines = min(max(lines, 1), 200)
            except ValueError:
                await reply(update, f"Project '{first_arg}' not found. Available projects: {config.get_available_projects()}")
                return

    # If no project name, try thread context with fallback
    if not project_name:
        thread_key, worktree_info = get_thread_key_with_fallback(update)
        if worktree_info:
            project_name = worktree_info.get("project_name")

    if not project_name:
        await reply(update, "Usage: /log [project-name] [lines]\n\nNo project specified and no project context in this thread.")
        return

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


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cleanup command. Clean up orphan worktrees."""
    if not update.message:
        logger.info("Received /cleanup command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /cleanup command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /cleanup command")

    # Get all active worktree IDs (running queries + completed jobs + thread worktrees)
    active_ids = set()

    # Add running query IDs
    running_queries = claude.get_all_running_queries()
    for project_name, queries in running_queries.items():
        for query_id in queries.keys():
            active_ids.add(query_id)

    # Add completed job IDs
    for job_id in claude.COMPLETED_JOBS.keys():
        active_ids.add(job_id)

    # Add thread-worktree IDs
    for thread_key, info in claude.get_all_thread_worktrees().items():
        active_ids.add(info.get("query_id"))

    logger.info(f"Active worktree IDs to preserve: {active_ids}")

    # Scan worktree base directory for orphan worktrees
    worktree_base = config.WORKTREE_BASE
    if not os.path.exists(worktree_base):
        await reply(update, "No worktrees directory found. Nothing to clean up.")
        return

    cleaned = []
    errors = []

    for project_dir in os.listdir(worktree_base):
        project_path = os.path.join(worktree_base, project_dir)
        if not os.path.isdir(project_path):
            continue

        # Get the project's main workdir for git worktree commands
        project = config.get_project(project_dir)
        project_workdir = project['project_workdir'] if project else None

        for worktree_id in os.listdir(project_path):
            worktree_path = os.path.join(project_path, worktree_id)
            if not os.path.isdir(worktree_path):
                continue

            # Check if this worktree is active
            if worktree_id not in active_ids:
                logger.info(f"Cleaning up orphan worktree: {worktree_path}")
                try:
                    if project_workdir:
                        git.cleanup_worktree(project_workdir, worktree_path)
                    else:
                        # Fallback: just remove the directory
                        import shutil
                        shutil.rmtree(worktree_path, ignore_errors=True)
                    cleaned.append(f"{project_dir}/{worktree_id}")
                except Exception as e:
                    logger.error(f"Error cleaning up {worktree_path}: {e}")
                    errors.append(f"{project_dir}/{worktree_id}: {str(e)[:50]}")

    # Build response
    lines = []
    if cleaned:
        lines.append(f"Cleaned up {len(cleaned)} orphan worktree(s):")
        for item in cleaned:
            lines.append(f"  - {item}")
    else:
        lines.append("No orphan worktrees found.")

    if errors:
        lines.append(f"\nErrors ({len(errors)}):")
        for err in errors:
            lines.append(f"  - {err}")

    await reply(update, "\n".join(lines))


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /list command. Display registered projects."""
    if not update.message:
        logger.info("Received /list command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /list command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await reply(update, f"I only respond to {authorized_list}")
        return

    logger.info("Received /list command")

    if not config.PROJECTS:
        await reply(update, "No projects configured.")
        return

    lines = ["Registered projects:\n"]
    for project in config.PROJECTS:
        name = project.get('project_name', '?')
        repo = project.get('project_repo', '?')
        workdir = project.get('project_workdir', '?')
        has_up = "Yes" if project.get('project_up') else "No"

        lines.append(f"**{name}**")
        lines.append(f"  Repo: {repo}")
        lines.append(f"  Workdir: {workdir}")
        lines.append(f"  Has project_up: {has_up}")
        lines.append("")

    await reply(update, "\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command. Display available commands."""
    if not update.message:
        logger.info("Received /help command with no message object")
        return

    help_text = """Available commands:

/help
  Show this help message

/list
  List all registered projects

/ask [project-name] <query>
  Ask a question (with project-name: about that project, without: casual chat)

/feat <project-name> <task>
  Implement a new feature (creates new branch, commits, and opens MR)
  Starts a new session (clears previous context)

/fix <project-name> <issue>
  Fix a bug (creates new branch, commits, and opens MR)
  Starts a new session (clears previous context)

/plan <project-name> <task>
  Plan and explore a task (creates new branch)
  Starts a new session (clears previous context)

/feedback [project-name] [job-id] <feedback>
  Continue work with context from previous command
  Without project-name: uses thread context
  Optional job-id: continue a specific job (see /status)

/init <project-name>
  Initialize CLAUDE.md for a project and spin up if project_up is configured

/up [project-name]
  Spin up a project using project_up command
  Without project-name: uses thread context

/stop [project-name]
  Stop a running project
  Without project-name: uses thread context

/down [project-name]
  Alias for /stop - stop a running project

/status
  Show running projects

/cancel [project-name] [query-id]
  Cancel running Claude queries
  Without args: uses thread context, or cancel all if no context
  With project: cancel all for that project

/log [project-name] [lines]
  Show last N lines of project logs (default: 50)
  Without project-name: uses thread context

/cleanup
  Clean up orphan worktrees (those without running/completed jobs)

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
