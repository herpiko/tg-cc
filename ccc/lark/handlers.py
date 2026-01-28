"""Lark command handlers for ccc bot."""

import asyncio
import logging
import os
import uuid

from ccc import config
from ccc import claude
from ccc import git
from ccc import process

logger = logging.getLogger(__name__)


def is_authorized(event: dict) -> bool:
    """Check if the user and chat are authorized to use the bot.

    Args:
        event: Lark event dict containing sender and chat info
    """
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", {})
    user_open_id = sender_id.get("open_id", "")

    message = event.get("message", {})
    chat_id = message.get("chat_id", "")

    logger.info(f"Checking authorization for user: {user_open_id}, chat_id: {chat_id}")

    user_authorized = config.is_lark_user_authorized(user_open_id)
    chat_authorized = config.is_lark_chat_authorized(chat_id)

    logger.info(f"Authorization result: user={user_authorized}, chat={chat_authorized}")

    if not user_authorized:
        logger.warning(f"User {user_open_id} not in authorized list: {config.LARK_AUTHORIZED_USERS}")
    if not chat_authorized:
        logger.warning(f"Chat {chat_id} not in authorized list: {config.LARK_AUTHORIZED_CHATS}")

    return user_authorized and chat_authorized


def parse_command(text: str) -> tuple:
    """Parse command and arguments from message text.

    Args:
        text: Message text, potentially with @mention

    Returns:
        Tuple of (command, args_list) or (None, []) if no command
    """
    # Remove @mentions (format: @_user_xxx or similar)
    import re
    text = re.sub(r'@\S+\s*', '', text).strip()

    if not text.startswith('/'):
        return None, []

    parts = text.split()
    command = parts[0][1:]  # Remove leading /
    args = parts[1:] if len(parts) > 1 else []

    return command, args


def get_thread_key(context: dict) -> str:
    """Get the thread key for this Lark context."""
    chat_id = context.get("chat_id", "")
    root_id = context.get("root_id")
    return claude.get_thread_key_lark(chat_id, root_id)


def get_thread_key_with_fallback(context: dict) -> tuple[str, dict | None]:
    """Get the thread key and try to find worktree context with fallback.

    Returns:
        Tuple of (thread_key, worktree_info or None)
    """
    chat_id = context.get("chat_id", "")
    root_id = context.get("root_id")

    # Primary: try with root_id
    primary_key = claude.get_thread_key_lark(chat_id, root_id)
    worktree_info = claude.get_thread_worktree(primary_key)

    if worktree_info:
        logger.info(f"Found thread context for key {primary_key}")
        return primary_key, worktree_info

    # Fallback 1: If we have a root_id, try the 'main' key for the same chat
    if root_id:
        fallback_key = claude.get_thread_key_lark(chat_id, None)
        worktree_info = claude.get_thread_worktree(fallback_key)
        if worktree_info:
            logger.info(f"Found thread context using fallback key {fallback_key}")
            return fallback_key, worktree_info

    # Fallback 2: If we don't have a root_id, search for any thread in this chat
    if not root_id:
        all_worktrees = claude.get_all_thread_worktrees()
        chat_prefix = f"lark:{chat_id}:"
        for key, info in all_worktrees.items():
            if key.startswith(chat_prefix):
                logger.info(f"Found thread context for chat using key {key}")
                return key, info

    logger.info(f"No thread context found for key {primary_key}")
    return primary_key, None


async def process_output_file(messenger, context: dict, output_file: str, duration_minutes: float):
    """Process output file and send to user with cleanup."""
    if os.path.exists(output_file):
        with open(output_file, 'a') as f:
            f.write(f"\n\nExecution time: {duration_minutes:.2f} minutes")

    if os.path.exists(output_file):
        with open(output_file, 'r') as f:
            output_content = f.read()

        if output_content:
            # Lark has different message limits, but keep similar truncation
            if len(output_content) > 4000:
                await messenger.reply(context, output_content[:4000] + "\n\n[Output truncated...]")
            else:
                await messenger.reply(context, output_content)
        else:
            await messenger.reply(context, f"Command completed but {output_file} is empty")

        os.remove(output_file)
        logger.info(f"Cleaned up {output_file}")
    else:
        await messenger.reply(context, f"Error: {output_file} was not created by Claude")


def cleanup_output_file(output_file: str):
    """Clean up output file if it exists."""
    if os.path.exists(output_file):
        os.remove(output_file)
        logger.info(f"Cleaned up {output_file}")


async def handle_message(messenger, event: dict) -> None:
    """Handle incoming Lark message.

    Args:
        messenger: LarkMessenger instance
        event: Lark event dict
    """
    if not is_authorized(event):
        logger.info("Unauthorized user attempted to use bot")
        message = event.get("message", {})
        context = {
            "chat_id": message.get("chat_id"),
            "message_id": message.get("message_id"),
            "root_id": message.get("root_id"),
        }
        await messenger.reply(context, "Unauthorized access. Please contact administrator.")
        return

    message = event.get("message", {})
    content = message.get("content", "{}")

    # Log all message fields to debug
    logger.info(f"Message fields: {list(message.keys())}")

    # Parse message content (it's JSON)
    import json
    try:
        content_dict = json.loads(content)
        text = content_dict.get("text", "")
    except json.JSONDecodeError:
        text = content

    # message_id might be in different fields depending on Lark API version
    message_id = message.get("message_id") or message.get("msg_id") or message.get("id")

    context = {
        "chat_id": message.get("chat_id"),
        "message_id": message_id,
        "root_id": message.get("root_id") or message.get("parent_id"),
    }

    logger.info(f"Context for reply: {context}")

    # Parse command
    command, args = parse_command(text)

    if not command:
        # Not a command - check if bot is mentioned in a thread with worktree context
        import re
        # Remove @mentions and check if there's remaining text
        text_without_mention = re.sub(r'@\S+\s*', '', text).strip()

        if text_without_mention:
            # Check if this thread has an active worktree context
            thread_key, worktree_info = get_thread_key_with_fallback(context)

            if worktree_info:
                # Continue conversation in the worktree context
                await _continue_in_worktree(messenger, context, text_without_mention, worktree_info, thread_key)
                return
            else:
                # No worktree context, treat as casual conversation
                await _ask_casual(messenger, context, text_without_mention)
                return

        logger.info("Message is not a command and has no content, ignoring")
        return

    logger.info(f"Received command: /{command} with args: {args}")

    # Route to appropriate handler
    handlers = {
        "help": cmd_help,
        "list": cmd_list,
        "cleanup": cmd_cleanup,
        "ask": cmd_ask,
        "feat": cmd_feat,
        "fix": cmd_fix,
        "plan": cmd_plan,
        "feedback": cmd_feedback,
        "init": cmd_init,
        "up": cmd_up,
        "stop": cmd_stop,
        "down": cmd_down,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "log": cmd_log,
    }

    handler = handlers.get(command)
    if handler:
        await handler(messenger, context, args)
    else:
        await messenger.reply(context, f"Unknown command: /{command}. Use /help for available commands.")


async def cmd_cleanup(messenger, context: dict, args: list) -> None:
    """Handle /cleanup command. Clean up orphan worktrees."""
    import shutil

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
        await messenger.reply(context, "No worktrees directory found. Nothing to clean up.")
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

    await messenger.reply(context, "\n".join(lines))


async def cmd_list(messenger, context: dict, args: list) -> None:
    """Handle /list command. Display registered projects."""
    logger.info("Received /list command")

    if not config.PROJECTS:
        await messenger.reply(context, "No projects configured.")
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

    await messenger.reply(context, "\n".join(lines))


async def cmd_help(messenger, context: dict, args: list) -> None:
    """Handle /help command."""
    help_text = """Available commands:

/help
  Show this help message

/list
  List all registered projects

/ask [project-name] <query>
  Ask a question (with project-name: about that project, without: casual chat)

/feat <project-name> <task>
  Implement a new feature (creates new branch, commits, and opens MR)

/fix <project-name> <issue>
  Fix a bug (creates new branch, commits, and opens MR)

/plan <project-name> <task>
  Plan and explore a task (creates new branch)

/feedback [project-name] [job-id] <feedback>
  Continue work with context from previous command
  Without project-name: uses thread context
  Optional job-id: continue a specific job (see /status)

/init <project-name>
  Initialize CLAUDE.md for a project

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
  Clean up orphan worktrees (those without running/completed jobs)"""

    await messenger.reply(context, help_text)


async def cmd_ask(messenger, context: dict, args: list) -> None:
    """Handle /ask command. Format: /ask [project-name] query

    If project-name is provided and exists, ask about that project.
    Otherwise, treat as casual conversation with the agent.
    """
    if len(args) < 1:
        await messenger.reply(context, "Usage: /ask [project-name] query\n\nWith project-name: Ask about a specific project\nWithout project-name: Casual conversation with the agent")
        return

    # Check if first arg is a project name
    potential_project = args[0]
    project = config.get_project(potential_project)

    if project and len(args) >= 2:
        # Project-specific query
        project_name = potential_project
        user_text = " ".join(args[1:])
        await _ask_project(messenger, context, project_name, project, user_text)
    else:
        # Casual conversation (no project context)
        user_text = " ".join(args)
        await _ask_casual(messenger, context, user_text)


async def _ask_project(messenger, context: dict, project_name: str, project: dict, user_text: str) -> None:
    """Handle project-specific /ask query."""
    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, context, project_workdir):
        return

    # Store thread context for this project
    messenger.set_thread_context(project_name, context)

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(context)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /ask: {error}")
        await messenger.reply(context, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
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
        await messenger.reply(context, f"Error: Failed to set up thread context. {str(e)}")
        return

    await messenger.reply(context, f"Processing for project: {project_name} (query: {query_id}, thread: {thread_key})...")

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

        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def _ask_casual(messenger, context: dict, user_text: str, existing_session: str = None) -> None:
    """Handle casual conversation /ask query (no project context)."""
    query_id = str(uuid.uuid4())[:8]

    # Get and validate thread key
    thread_key = get_thread_key(context)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for casual /ask: {error}")
        await messenger.reply(context, f"Error: Cannot determine thread context. {error}")
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
        await messenger.reply(context, f"Error: Failed to set up thread context. {str(e)}")
        return

    if existing_session:
        await messenger.reply(context, f"Continuing casual conversation (query: {query_id}, thread: {thread_key})...")
    else:
        await messenger.reply(context, f"Processing casual query (query: {query_id}, thread: {thread_key})...")

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
            await process_output_file(messenger, context, output_file, duration_minutes)
        else:
            await messenger.reply(context, f"Query completed in {duration_minutes:.2f} minutes, but no output file was generated. The assistant may have responded directly in the logs.")

    except asyncio.CancelledError:
        logger.info(f"Casual query {query_id} was cancelled")
        await messenger.reply(context, f"Query {query_id} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running casual query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def _continue_in_worktree(messenger, context: dict, user_text: str, worktree_info: dict, thread_key: str) -> None:
    """Continue conversation in an existing worktree context."""
    query_id = worktree_info["query_id"]
    worktree_path = worktree_info["worktree_path"]
    project_workdir = worktree_info["project_workdir"]
    project_name = worktree_info["project_name"]
    project_repo = worktree_info["project_repo"]
    existing_session = worktree_info.get("session_id")

    # Check if this is a casual conversation context
    if query_id.startswith("casual-") or project_name == "_casual":
        logger.info(f"Continuing casual conversation with session {existing_session}")
        await _ask_casual(messenger, context, user_text, existing_session)
        return

    logger.info(f"Continuing in worktree {query_id} for project {project_name}")

    # Check if this is from /up command (pseudo worktree) or if worktree doesn't exist
    is_up_context = query_id.startswith("up-")
    worktree_exists = worktree_path and os.path.isdir(worktree_path)

    if is_up_context or not worktree_exists:
        # Need to create a proper worktree for continuation
        logger.info(f"Worktree doesn't exist or is /up context, creating new worktree")
        query_id = str(uuid.uuid4())[:8]
        worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
        if not worktree_path:
            await messenger.reply(context, f"Failed to create worktree for continuation in {project_name}")
            return
        # Clear session since we're in a new worktree
        existing_session = None

    await messenger.reply(context, f"Continuing with query {query_id} for {project_name}...")

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

        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Continuation query in worktree {query_id} was cancelled")
        await messenger.reply(context, f"Query in {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running continuation query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_feat(messenger, context: dict, args: list) -> None:
    """Handle /feat command. Format: /feat project-name prompt"""
    if len(args) < 2:
        await messenger.reply(context, "Usage: /feat project-name prompt")
        return

    project_name = args[0]
    user_prompt = " ".join(args[1:])

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, context, project_workdir):
        return

    # Clear existing session and store new thread context
    claude.clear_session(project_name)
    messenger.set_thread_context(project_name, context)

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(context)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /feat: {error}")
        await messenger.reply(context, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
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
        await messenger.reply(context, f"Error: Failed to set up thread context. {str(e)}")
        return

    await messenger.reply(context, f"Processing for project: {project_name} (query: {query_id}, thread: {thread_key})...")

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
        claude.set_session(project_name, session_id)

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_fix(messenger, context: dict, args: list) -> None:
    """Handle /fix command. Format: /fix project-name prompt"""
    if len(args) < 2:
        await messenger.reply(context, "Usage: /fix project-name prompt")
        return

    project_name = args[0]
    user_prompt = " ".join(args[1:])

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, context, project_workdir):
        return

    # Clear existing session and store new thread context
    claude.clear_session(project_name)
    messenger.set_thread_context(project_name, context)

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(context)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /fix: {error}")
        await messenger.reply(context, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
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
        await messenger.reply(context, f"Error: Failed to set up thread context. {str(e)}")
        return

    await messenger.reply(context, f"Processing for project: {project_name} (query: {query_id}, thread: {thread_key})...")

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
        claude.set_session(project_name, session_id)

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_plan(messenger, context: dict, args: list) -> None:
    """Handle /plan command. Format: /plan project-name prompt"""
    if len(args) < 2:
        await messenger.reply(context, "Usage: /plan project-name prompt")
        return

    project_name = args[0]
    user_prompt = " ".join(args[1:])

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, context, project_workdir):
        return

    # Clear existing session and store new thread context
    claude.clear_session(project_name)
    messenger.set_thread_context(project_name, context)

    # Get and validate thread key BEFORE creating worktree
    thread_key = get_thread_key(context)
    is_valid, error = claude.validate_thread_key(thread_key)
    if not is_valid:
        logger.error(f"Invalid thread key for /plan: {error}")
        await messenger.reply(context, f"Error: Cannot determine thread context. {error}")
        return

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
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
        await messenger.reply(context, f"Error: Failed to set up thread context. {str(e)}")
        return

    await messenger.reply(context, f"Planning for project: {project_name} (query: {query_id}, thread: {thread_key})...")

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
        claude.set_session(project_name, session_id)

        # Update thread-worktree association with session_id
        claude.update_thread_session(thread_key, session_id)

        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_feedback(messenger, context: dict, args: list) -> None:
    """Handle /feedback command. Format: /feedback [project-name] [job-id] prompt

    If project-name is not provided, uses the project and worktree from thread context.
    """
    if len(args) < 1:
        await messenger.reply(context, "Usage: /feedback [project-name] [job-id] prompt\n\nUse /status to see available job IDs.")
        return

    # Try to determine project from args or thread context
    thread_key, worktree_info = get_thread_key_with_fallback(context)

    project_name = None
    project = None
    job_id = None
    user_prompt = None
    args_index = 0

    # Check if first arg is a project name
    first_arg = args[0]
    if config.get_project(first_arg):
        project_name = first_arg
        project = config.get_project(project_name)
        args_index = 1
    elif worktree_info:
        # Use thread context for project
        project_name = worktree_info.get("project_name")
        project = config.get_project(project_name)
    else:
        await messenger.reply(context, f"Project '{first_arg}' not found and no project context in this thread.\n\nAvailable projects: {config.get_available_projects()}")
        return

    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    # Check remaining args for job-id and prompt
    remaining_args = args[args_index:]
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
        await messenger.reply(context, "Usage: /feedback [project-name] [job-id] prompt\n\nPlease provide feedback text.")
        return

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, context, project_workdir):
        return

    # Determine worktree and session
    if job_id:
        # Use existing job's worktree and session
        job_info = claude.get_completed_job(job_id)
        if not job_info:
            await messenger.reply(context, f"Job '{job_id}' not found. Use /status to see available jobs.")
            return

        if job_info.get("project_name") != project_name:
            await messenger.reply(context, f"Job '{job_id}' belongs to project '{job_info.get('project_name')}', not '{project_name}'.")
            return

        worktree_path = job_info.get("worktree_path")
        existing_session = job_info.get("session_id")
        query_id = job_id

        logger.info(f"Resuming job {job_id} with session {existing_session} in worktree {worktree_path}")
        await messenger.reply(context, f"Continuing job {job_id} for project: {project_name}...")

        del claude.COMPLETED_JOBS[job_id]
    elif worktree_info and worktree_info.get("project_name") == project_name:
        # Use thread's worktree context
        worktree_path = worktree_info.get("worktree_path")
        existing_session = worktree_info.get("session_id")
        query_id = worktree_info.get("query_id")

        logger.info(f"Using thread worktree {query_id} with session {existing_session}")
        await messenger.reply(context, f"Continuing with query {query_id} for project: {project_name}...")
    else:
        # Create new worktree
        existing_session = claude.get_session(project_name)
        if existing_session:
            logger.info(f"Resuming session {existing_session} for project {project_name}")
        else:
            logger.info(f"No existing session for project {project_name}, starting fresh")

        query_id = str(uuid.uuid4())[:8]
        worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
        if not worktree_path:
            return

        if existing_session:
            await messenger.reply(context, f"Continuing session for project: {project_name} (query: {query_id})...")
        else:
            await messenger.reply(context, f"No existing session found. Starting new session for project: {project_name} (query: {query_id})...")

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

        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await messenger.reply(context, f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_init(messenger, context: dict, args: list) -> None:
    """Handle /init command. Format: /init project-name"""
    if len(args) < 1:
        await messenger.reply(context, "Usage: /init project-name")
        return

    project_name = args[0]

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']
    project_up = project.get('project_up')
    project_endpoint_url = project.get('project_endpoint_url')
    project_ports = project.get('project_ports')

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    init_success = await claude.initialize_claude_md(messenger, context, project_workdir)

    if project_up:
        # Clean up workdir and pull from main before spinning up
        if not await git.refresh_to_main_branch(messenger, context, project_workdir):
            return
        await process.spin_up_project(messenger, context, project_name, project_workdir, project_up, project_endpoint_url, project_ports)

    if not init_success:
        await messenger.reply(context, f"Failed to initialize CLAUDE.md for project: {project_name}")
        return

    await messenger.reply(context, f"Successfully initialized CLAUDE.md for project: {project_name}")


async def cmd_up(messenger, context: dict, args: list) -> None:
    """Handle /up command. Format: /up [project-name]

    If project-name is not provided, uses the project from thread context.
    """
    # Get project name from args or thread context
    project_name = None
    project = None

    if args and len(args) >= 1:
        project_name = args[0]
        project = config.get_project(project_name)
        if not project:
            await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
            return
    else:
        # Try to get project from thread context with fallback
        thread_key, worktree_info = get_thread_key_with_fallback(context)
        if worktree_info:
            project_name = worktree_info.get("project_name")
            project = config.get_project(project_name)

    if not project_name or not project:
        await messenger.reply(context, "Usage: /up [project-name]\n\nNo project specified and no project context in this thread.")
        return

    project_workdir = project['project_workdir']
    project_up = project.get('project_up')
    project_endpoint_url = project.get('project_endpoint_url')
    project_ports = project.get('project_ports')
    project_repo = project.get('project_repo')

    if not project_up:
        await messenger.reply(context, f"No project_up command configured for {project_name}")
        return

    # Get current thread key
    thread_key = get_thread_key(context)

    # Clear any existing thread associations for this project from other threads
    for existing_key, info in list(claude.get_all_thread_worktrees().items()):
        if info.get("project_name") == project_name and existing_key != thread_key:
            claude.clear_thread_worktree(existing_key)
            logger.info(f"Cleared old thread association {existing_key} for project {project_name}")

    # Clean up workdir and pull from main before spinning up
    if not await git.refresh_to_main_branch(messenger, context, project_workdir):
        return

    await process.spin_up_project(messenger, context, project_name, project_workdir, project_up, project_endpoint_url, project_ports)

    # Associate this thread with the project
    claude.set_thread_worktree(
        thread_key, f"up-{project_name}", None,
        project_workdir, project_workdir, project_name, project_repo
    )


async def cmd_stop(messenger, context: dict, args: list) -> None:
    """Handle /stop command. Format: /stop [project-name]

    If project-name is not provided, uses the project from thread context.
    """
    # Get project name from args or thread context
    project_name = None

    if args and len(args) >= 1:
        project_name = args[0]
        project = config.get_project(project_name)
        if not project:
            await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
            return
    else:
        # Try to get project from thread context with fallback
        thread_key, worktree_info = get_thread_key_with_fallback(context)
        if worktree_info:
            project_name = worktree_info.get("project_name")

    if not project_name:
        await messenger.reply(context, "Usage: /stop [project-name]\n\nNo project specified and no project context in this thread.")
        return

    await process.kill_project_process(messenger, context, project_name)


async def cmd_down(messenger, context: dict, args: list) -> None:
    """Handle /down command. Alias for /stop. Format: /down project-name"""
    await cmd_stop(messenger, context, args)


async def cmd_status(messenger, context: dict, args: list) -> None:
    """Handle /status command. Shows running queries, completed jobs, and processes."""
    from datetime import datetime
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
                    elapsed = (datetime.now() - started).total_seconds() / 60
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
                age = (datetime.now() - completed).total_seconds() / 60
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
        await messenger.reply(context, "No running queries, completed jobs, or processes.")
        return

    await messenger.reply(context, "\n".join(status_lines))


async def cmd_cancel(messenger, context: dict, args: list) -> None:
    """Handle /cancel command. Format: /cancel [project-name] [query-id]

    If no args and thread has context, cancel queries for that project.
    If no args and no thread context, cancel all running queries.
    """
    project_name = None
    query_id = None

    # Get thread context with fallback once
    thread_key, worktree_info = get_thread_key_with_fallback(context)

    if args and len(args) >= 1:
        # Check if first arg is a project name or query ID
        potential_project = args[0]
        if config.get_project(potential_project):
            project_name = potential_project
            query_id = args[1] if len(args) > 1 else None
        else:
            # First arg might be a query ID if thread has context
            if worktree_info:
                project_name = worktree_info.get("project_name")
                query_id = potential_project  # Treat first arg as query ID
            else:
                await messenger.reply(context, f"Project '{potential_project}' not found. Available projects: {config.get_available_projects()}")
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
            await messenger.reply(context, "No running queries to cancel.")
            return

        all_cancelled = []
        for pname in list(running.keys()):
            cancelled = claude.cancel_query(pname)
            all_cancelled.extend([f"{pname}:{qid}" for qid in cancelled])

        if all_cancelled:
            await messenger.reply(context, f"Cancelled {len(all_cancelled)} queries: {', '.join(all_cancelled)}")
        else:
            await messenger.reply(context, "No queries were cancelled.")
        return

    queries = claude.get_running_queries_for_project(project_name)
    if not queries:
        await messenger.reply(context, f"No running queries for project {project_name}.")
        return

    if query_id:
        # Cancel specific query
        if query_id not in queries:
            await messenger.reply(context, f"Query ID '{query_id}' not found for project {project_name}. Running queries: {', '.join(queries.keys())}")
            return

        cancelled = claude.cancel_query(project_name, query_id)
        if cancelled:
            await messenger.reply(context, f"Cancelled query {query_id} for project {project_name}.")
        else:
            await messenger.reply(context, f"Failed to cancel query {query_id} for project {project_name}.")
    else:
        # Cancel all queries for the project
        cancelled = claude.cancel_query(project_name)
        if cancelled:
            await messenger.reply(context, f"Cancelled {len(cancelled)} queries for project {project_name}: {', '.join(cancelled)}")
        else:
            await messenger.reply(context, f"Failed to cancel queries for project {project_name}.")


async def cmd_log(messenger, context: dict, args: list) -> None:
    """Handle /log command. Format: /log [project-name] [lines]

    If project-name is not provided, uses the project from thread context.
    """
    project_name = None
    lines = 50

    if args and len(args) >= 1:
        # Check if first arg is a project name or number of lines
        first_arg = args[0]
        if config.get_project(first_arg):
            project_name = first_arg
            if len(args) >= 2:
                try:
                    lines = int(args[1])
                    lines = min(max(lines, 1), 200)
                except ValueError:
                    await messenger.reply(context, "Invalid number of lines. Using default (50).")
        else:
            # First arg might be lines if thread has context
            try:
                lines = int(first_arg)
                lines = min(max(lines, 1), 200)
            except ValueError:
                await messenger.reply(context, f"Project '{first_arg}' not found. Available projects: {config.get_available_projects()}")
                return

    # If no project name, try thread context with fallback
    if not project_name:
        thread_key, worktree_info = get_thread_key_with_fallback(context)
        if worktree_info:
            project_name = worktree_info.get("project_name")

    if not project_name:
        await messenger.reply(context, "Usage: /log [project-name] [lines]\n\nNo project specified and no project context in this thread.")
        return

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    is_running, pid, _ = process.get_process_status(project_name)
    if not is_running and pid is None:
        await messenger.reply(context, f"No running instance found for project {project_name}. Use /up to start it.")
        return

    logs = process.get_project_logs(project_name, lines)
    if not logs:
        await messenger.reply(context, f"No logs available for project {project_name}.")
        return

    status = "running" if is_running else "exited"
    header = f"Logs for {project_name} (PID: {pid}, {status}) - last {lines} lines:\n\n"

    output = header + logs
    if len(output) > 4000:
        output = output[:4000] + "\n\n[Output truncated...]"

    await messenger.reply(context, output)
