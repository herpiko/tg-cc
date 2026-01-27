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
        # Not a command, ignore or handle mentions differently
        logger.info("Message is not a command, ignoring")
        return

    logger.info(f"Received command: /{command} with args: {args}")

    # Route to appropriate handler
    handlers = {
        "help": cmd_help,
        "ask": cmd_ask,
        "feat": cmd_feat,
        "fix": cmd_fix,
        "plan": cmd_plan,
        "feedback": cmd_feedback,
        "init": cmd_init,
        "up": cmd_up,
        "stop": cmd_stop,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "log": cmd_log,
    }

    handler = handlers.get(command)
    if handler:
        await handler(messenger, context, args)
    else:
        await messenger.reply(context, f"Unknown command: /{command}. Use /help for available commands.")


async def cmd_help(messenger, context: dict, args: list) -> None:
    """Handle /help command."""
    help_text = """Available commands:

/help
  Show this help message

/ask <project-name> <query>
  Ask a question about a project

/feat <project-name> <task>
  Implement a new feature (creates new branch, commits, and opens MR)

/fix <project-name> <issue>
  Fix a bug (creates new branch, commits, and opens MR)

/plan <project-name> <task>
  Plan and explore a task (creates new branch)

/feedback <project-name> [job-id] <feedback>
  Continue work with context from previous command
  Optional: specify job-id to continue a specific job (see /status)

/init <project-name>
  Initialize CLAUDE.md for a project

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
  Show last N lines of project logs (default: 50)"""

    await messenger.reply(context, help_text)


async def cmd_ask(messenger, context: dict, args: list) -> None:
    """Handle /ask command. Format: /ask project-name query"""
    if len(args) < 2:
        await messenger.reply(context, "Usage: /ask project-name query")
        return

    project_name = args[0]
    user_text = " ".join(args[1:])

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

    # Store thread context for this project
    messenger.set_thread_context(project_name, context)

    # Generate query ID and create worktree
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await messenger.reply(context, f"Processing for project: {project_name} (query: {query_id})...")

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
        await process_output_file(messenger, context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
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

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await messenger.reply(context, f"Processing for project: {project_name} (query: {query_id})...")

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
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep worktree for potential feedback
        )
        claude.set_session(project_name, session_id)
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

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await messenger.reply(context, f"Processing for project: {project_name} (query: {query_id})...")

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
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep worktree for potential feedback
        )
        claude.set_session(project_name, session_id)
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

    # Generate query ID and create worktree (starts from origin/main)
    query_id = str(uuid.uuid4())[:8]
    worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
    if not worktree_path:
        return

    await messenger.reply(context, f"Planning for project: {project_name} (query: {query_id})...")

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
            worktree_path=worktree_path, project_workdir=project_workdir, query_id=query_id,
            keep_worktree=True  # Keep worktree for potential feedback
        )
        claude.set_session(project_name, session_id)
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
    """Handle /feedback command. Format: /feedback project-name [job-id] prompt"""
    if len(args) < 2:
        await messenger.reply(context, "Usage: /feedback project-name [job-id] prompt\n\nUse /status to see available job IDs.")
        return

    project_name = args[0]

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    # Check if second argument is a job ID (8 char hex) or part of the prompt
    job_id = None
    if len(args) >= 2:
        potential_job_id = args[1]
        # Check if it looks like a job ID (8 hex chars) and exists in completed jobs
        if len(potential_job_id) == 8 and claude.get_completed_job(potential_job_id):
            job_id = potential_job_id
            user_prompt = " ".join(args[2:]) if len(args) > 2 else ""
        else:
            user_prompt = " ".join(args[1:])

    if not user_prompt:
        await messenger.reply(context, "Usage: /feedback project-name [job-id] prompt\n\nPlease provide feedback text.")
        return

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(messenger, context, project_workdir):
        return

    # Try to use existing thread context, fall back to current context
    stored_context = messenger.get_project_thread(project_name)
    reply_context = stored_context if stored_context else context

    # Determine worktree and session based on job_id
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
        query_id = job_id  # Reuse the same job ID for continuity

        logger.info(f"Resuming job {job_id} with session {existing_session} in worktree {worktree_path}")
        await messenger.reply(reply_context, f"Continuing job {job_id} for project: {project_name}...")

        # Remove from completed jobs since we're continuing it
        del claude.COMPLETED_JOBS[job_id]
    else:
        # Original behavior: use project session and create new worktree
        existing_session = claude.get_session(project_name)

        if existing_session:
            logger.info(f"Resuming session {existing_session} for project {project_name}")
        else:
            logger.info(f"No existing session for project {project_name}, starting fresh")
            messenger.set_thread_context(project_name, context)
            reply_context = context

        # Generate query ID and create worktree
        query_id = str(uuid.uuid4())[:8]
        worktree_path = await git.create_worktree(messenger, context, project_workdir, project_name, query_id)
        if not worktree_path:
            return

        if existing_session:
            await messenger.reply(reply_context, f"Continuing session for project: {project_name} (query: {query_id})...")
        else:
            await messenger.reply(reply_context, f"No existing session found. Starting new session for project: {project_name} (query: {query_id})...")

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
            keep_worktree=True  # Keep for potential further feedback
        )
        claude.set_session(project_name, session_id)
        await process_output_file(messenger, reply_context, output_file, duration_minutes)

    except asyncio.CancelledError:
        logger.info(f"Query {query_id} for project {project_name} was cancelled")
        await messenger.reply(reply_context, f"Query {query_id} for {project_name} was cancelled.")
        cleanup_output_file(output_file)
    except Exception as e:
        logger.error(f"Error running query: {e}")
        await messenger.reply(reply_context, f"Error: {str(e)}")
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

    if not await git.clone_repository_if_needed(messenger, context, project_repo, project_workdir):
        return

    init_success = await claude.initialize_claude_md(messenger, context, project_workdir)

    if project_up:
        await process.spin_up_project(messenger, context, project_name, project_workdir, project_up)

    if not init_success:
        await messenger.reply(context, f"Failed to initialize CLAUDE.md for project: {project_name}")
        return

    await messenger.reply(context, f"Successfully initialized CLAUDE.md for project: {project_name}")


async def cmd_up(messenger, context: dict, args: list) -> None:
    """Handle /up command. Format: /up project-name"""
    if len(args) < 1:
        await messenger.reply(context, "Usage: /up project-name")
        return

    project_name = args[0]

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_workdir = project['project_workdir']
    project_up = project.get('project_up')

    if not project_up:
        await messenger.reply(context, f"No project_up command configured for {project_name}")
        return

    await process.spin_up_project(messenger, context, project_name, project_workdir, project_up)


async def cmd_stop(messenger, context: dict, args: list) -> None:
    """Handle /stop command. Format: /stop project-name"""
    if len(args) < 1:
        await messenger.reply(context, "Usage: /stop project-name")
        return

    project_name = args[0]

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    await process.kill_project_process(messenger, context, project_name)


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
    """Handle /cancel command. Format: /cancel [project-name] [query-id]"""
    if not args:
        # Cancel all queries across all projects
        running = claude.get_all_running_queries()
        if not running:
            await messenger.reply(context, "No running queries to cancel.")
            return

        all_cancelled = []
        for project_name in list(running.keys()):
            cancelled = claude.cancel_query(project_name)
            all_cancelled.extend([f"{project_name}:{qid}" for qid in cancelled])

        if all_cancelled:
            await messenger.reply(context, f"Cancelled {len(all_cancelled)} queries: {', '.join(all_cancelled)}")
        else:
            await messenger.reply(context, "No queries were cancelled.")
        return

    project_name = args[0]

    project = config.get_project(project_name)
    if not project:
        await messenger.reply(context, f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    # Check if a specific query ID was provided
    query_id = args[1] if len(args) > 1 else None

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
    """Handle /log command. Format: /log project-name [lines]"""
    if len(args) < 1:
        await messenger.reply(context, "Usage: /log project-name [lines]\nDefault: 50 lines")
        return

    project_name = args[0]

    lines = 50
    if len(args) >= 2:
        try:
            lines = int(args[1])
            lines = min(max(lines, 1), 200)
        except ValueError:
            await messenger.reply(context, "Invalid number of lines. Using default (50).")

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
