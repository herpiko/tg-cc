"""Telegram command handlers for tg-cc bot."""

import logging
import os
import subprocess
import uuid

from telegram import Update
from telegram.ext import ContextTypes

from . import config
from . import claude
from . import git
from . import process

logger = logging.getLogger(__name__)


def is_authorized(update: Update) -> bool:
    """Check if the user and chat are authorized to use the bot."""
    if not update.message or not update.message.from_user:
        return False

    username = update.message.from_user.username
    chat_id = str(update.message.chat.id)

    logger.info(f"Checking authorization for user: {username}, chat_id: {chat_id}")

    user_authorized = username in config.AUTHORIZED_USERS
    group_authorized = chat_id in config.AUTHORIZED_GROUPS

    return user_authorized and group_authorized


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
                await update.message.reply_text( output_content[:4000] + "\n\n[Output truncated...]")
            else:
                await update.message.reply_text( output_content)
        else:
            await update.message.reply_text( f"Command completed but {output_file} is empty")

        os.remove(output_file)
        logger.info(f"Cleaned up {output_file}")
    else:
        await update.message.reply_text( f"Error: {output_file} was not created by Claude")


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
        await update.message.reply_text( f"I only respond to {authorized_list}")
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

        thread_id = message.message_thread_id
        if text_without_mention:
            await message.reply_text(text_without_mention, message_thread_id=thread_id)
        else:
            await message.reply_text("Hello", message_thread_id=thread_id)

        logger.info(f"Reply sent!")
    else:
        logger.info("Bot was not mentioned in this message")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask command. Format: /ask project-name query"""
    if not update.message:
        logger.info("Received /ask command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /ask command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /ask command")

    if not context.args or len(context.args) < 2:
        await update.message.reply_text( "Usage: /ask project-name query")
        return

    project_name = context.args[0]
    user_text = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(update, project_workdir):
        return

    await update.message.reply_text( f"Processing for project: {project_name}...")

    request_uuid = str(uuid.uuid4())
    output_file = f"/tmp/output_{request_uuid}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {project_workdir}

Query: {user_text}

Write the output in {output_file}"""

        logger.info(f"Running query for project {project_name} with UUID {request_uuid}")

        duration_minutes, _ = await claude.run_claude_query(prompt, config.ASK_RULES, project_workdir, project_name=project_name)
        await process_output_file(update, output_file, duration_minutes)

    except Exception as e:
        logger.error(f"Error running query: {e}")
        await update.message.reply_text( f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_feat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /feat command. Format: /feat project-name prompt"""
    if not update.message:
        logger.info("Received /feat command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /feat command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /feat command")

    if not context.args or len(context.args) < 2:
        await update.message.reply_text( "Usage: /feat project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(update, project_workdir):
        return

    if not await git.refresh_to_main_branch(update, project_workdir):
        return

    # Clear existing session for this project (starting fresh)
    claude.clear_session(project_name)

    await update.message.reply_text( f"Processing for project: {project_name}...")

    request_uuid = str(uuid.uuid4())
    output_file = f"/tmp/output_{request_uuid}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {project_workdir}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query for project {project_name} with UUID {request_uuid}")

        duration_minutes, session_id = await claude.run_claude_query(prompt, config.FEAT_RULES, project_workdir, project_name=project_name)

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except Exception as e:
        logger.error(f"Error running query: {e}")
        await update.message.reply_text( f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fix command. Format: /fix project-name prompt"""
    if not update.message:
        logger.info("Received /fix command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /fix command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /fix command")

    if not context.args or len(context.args) < 2:
        await update.message.reply_text( "Usage: /fix project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(update, project_workdir):
        return

    await update.message.reply_text( f"Processing for project: {project_name}...")

    if not await git.refresh_to_main_branch(update, project_workdir):
        return

    # Clear existing session for this project (starting fresh)
    claude.clear_session(project_name)

    request_uuid = str(uuid.uuid4())
    output_file = f"/tmp/output_{request_uuid}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {project_workdir}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query for project {project_name} with UUID {request_uuid}")

        duration_minutes, session_id = await claude.run_claude_query(prompt, config.FIX_RULES, project_workdir, project_name=project_name)

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except Exception as e:
        logger.error(f"Error running query: {e}")
        await update.message.reply_text( f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /plan command. Format: /plan project-name prompt"""
    if not update.message:
        logger.info("Received /plan command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /plan command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /plan command")

    if not context.args or len(context.args) < 2:
        await update.message.reply_text( "Usage: /plan project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(update, project_workdir):
        return

    if not await git.refresh_to_main_branch(update, project_workdir):
        return

    # Clear existing session for this project (starting fresh)
    claude.clear_session(project_name)

    await update.message.reply_text( f"Planning for project: {project_name}...")

    request_uuid = str(uuid.uuid4())
    output_file = f"/tmp/output_{request_uuid}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {project_workdir}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query for project {project_name} with UUID {request_uuid}")

        duration_minutes, session_id = await claude.run_claude_query(prompt, config.PLAN_RULES, project_workdir, project_name=project_name)

        # Store session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except Exception as e:
        logger.error(f"Error running query: {e}")
        await update.message.reply_text( f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /feedback command. Format: /feedback project-name prompt"""
    if not update.message:
        logger.info("Received /feedback command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /feedback command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /feedback command")

    if not context.args or len(context.args) < 2:
        await update.message.reply_text( "Usage: /feedback project-name prompt")
        return

    project_name = context.args[0]
    user_prompt = " ".join(context.args[1:])

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']

    if not await git.clone_repository_if_needed(update, project_repo, project_workdir):
        return

    if not await claude.initialize_claude_md(update, project_workdir):
        return

    # Get existing session for this project (to continue conversation)
    existing_session = claude.get_session(project_name)
    if existing_session:
        await update.message.reply_text( f"Continuing session for project: {project_name}...")
        logger.info(f"Resuming session {existing_session} for project {project_name}")
    else:
        await update.message.reply_text( f"No existing session found. Starting new session for project: {project_name}...")
        logger.info(f"No existing session for project {project_name}, starting fresh")

    request_uuid = str(uuid.uuid4())
    output_file = f"/tmp/output_{request_uuid}.txt"

    try:
        prompt = f"""Project: {project_name}
Repository: {project_repo}
Working Directory: {project_workdir}

Task: {user_prompt}

Write the output in {output_file}"""

        logger.info(f"Running query for project {project_name} with UUID {request_uuid}")

        duration_minutes, session_id = await claude.run_claude_query(prompt, config.FEEDBACK_RULES, project_workdir, resume=existing_session, project_name=project_name)

        # Update session for future /feedback commands
        claude.set_session(project_name, session_id)

        await process_output_file(update, output_file, duration_minutes)

    except Exception as e:
        logger.error(f"Error running query: {e}")
        await update.message.reply_text( f"Error: {str(e)}")
        cleanup_output_file(output_file)


async def cmd_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /init command. Format: /init project-name"""
    if not update.message:
        logger.info("Received /init command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /init command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /init command")

    if not context.args or len(context.args) < 1:
        await update.message.reply_text( "Usage: /init project-name")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_repo = project['project_repo']
    project_workdir = project['project_workdir']
    project_up = project.get('project_up')

    if not await git.clone_repository_if_needed(update, project_repo, project_workdir):
        return

    init_success = await claude.initialize_claude_md(update, project_workdir)

    # Spin up the project regardless of CLAUDE.md initialization result
    if project_up:
        await process.spin_up_project(update, project_name, project_workdir, project_up)

    if not init_success:
        await update.message.reply_text( f"Failed to initialize CLAUDE.md for project: {project_name}")
        return

    await update.message.reply_text( f"Successfully initialized CLAUDE.md for project: {project_name}")


async def cmd_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /up command. Format: /up project-name"""
    if not update.message:
        logger.info("Received /up command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /up command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /up command")

    if not context.args or len(context.args) < 1:
        await update.message.reply_text( "Usage: /up project-name")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    project_workdir = project['project_workdir']
    project_up = project.get('project_up')

    if not project_up:
        await update.message.reply_text( f"No project_up command configured for {project_name}")
        return

    await process.spin_up_project(update, project_name, project_workdir, project_up)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command. Format: /stop project-name"""
    if not update.message:
        logger.info("Received /stop command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /stop command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /stop command")

    if not context.args or len(context.args) < 1:
        await update.message.reply_text( "Usage: /stop project-name")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    await process.kill_project_process(update, project_name)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command. Shows running projects."""
    if not update.message:
        logger.info("Received /status command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /status command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /status command")

    running_projects = process.get_running_projects()
    if not running_projects:
        await update.message.reply_text( "No running projects.")
        return

    status_lines = ["Running projects:"]
    for project_name, process_info in running_projects.items():
        proc, log_path, _ = process_info
        # Check if process is still running
        if proc.poll() is None:
            status_lines.append(f"  - {project_name} (PID: {proc.pid})")
        else:
            status_lines.append(f"  - {project_name} (PID: {proc.pid}, exited with code {proc.returncode})")

    await update.message.reply_text( "\n".join(status_lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command. Format: /cancel [project-name]"""
    if not update.message:
        logger.info("Received /cancel command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /cancel command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /cancel command")

    # If no project specified, show running queries and cancel all
    if not context.args or len(context.args) < 1:
        running = claude.get_all_running_queries()
        if not running:
            await update.message.reply_text( "No running queries to cancel.")
            return

        # Cancel all running queries
        cancelled = []
        for project_name in list(running.keys()):
            if claude.cancel_query(project_name):
                cancelled.append(project_name)

        if cancelled:
            await update.message.reply_text( f"Cancelled queries for: {', '.join(cancelled)}")
        else:
            await update.message.reply_text( "No queries were cancelled.")
        return

    project_name = context.args[0]

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    # Check if there's a running query for this project
    task = claude.get_running_query(project_name)
    if not task or task.done():
        await update.message.reply_text( f"No running query for project {project_name}.")
        return

    # Cancel the query
    if claude.cancel_query(project_name):
        await update.message.reply_text( f"Cancelled query for project {project_name}.")
    else:
        await update.message.reply_text( f"Failed to cancel query for project {project_name}.")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /log command. Format: /log project-name [lines]"""
    if not update.message:
        logger.info("Received /log command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /log command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /log command")

    if not context.args or len(context.args) < 1:
        await update.message.reply_text( "Usage: /log project-name [lines]\nDefault: 50 lines")
        return

    project_name = context.args[0]

    # Optional: number of lines (default 50)
    lines = 50
    if len(context.args) >= 2:
        try:
            lines = int(context.args[1])
            lines = min(max(lines, 1), 200)  # Clamp between 1 and 200
        except ValueError:
            await update.message.reply_text( "Invalid number of lines. Using default (50).")

    project = config.get_project(project_name)
    if not project:
        await update.message.reply_text( f"Project '{project_name}' not found. Available projects: {config.get_available_projects()}")
        return

    # Check if project is running
    is_running, pid, _ = process.get_process_status(project_name)
    if not is_running and pid is None:
        await update.message.reply_text( f"No running instance found for project {project_name}. Use /up to start it.")
        return

    # Get logs
    logs = process.get_project_logs(project_name, lines)
    if not logs:
        await update.message.reply_text( f"No logs available for project {project_name}.")
        return

    # Format output
    status = "running" if is_running else f"exited"
    header = f"Logs for {project_name} (PID: {pid}, {status}) - last {lines} lines:\n\n"

    output = header + logs
    if len(output) > 4000:
        output = output[:4000] + "\n\n[Output truncated...]"

    await update.message.reply_text( output)


async def cmd_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cost command. Displays Claude usage costs via claude-monitor."""
    if not update.message:
        logger.info("Received /cost command with no message object")
        return

    if not is_authorized(update):
        logger.info("Unauthorized user attempted to use /cost command")
        authorized_list = ", ".join(config.AUTHORIZED_USERS)
        await update.message.reply_text( f"I only respond to {authorized_list}")
        return

    logger.info("Received /cost command")

    await update.message.reply_text( "Fetching Claude usage costs...")

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

        project_workdir = os.path.dirname(os.path.dirname(__file__))
        _, _ = await claude.run_claude_query(prompt, config.ASK_RULES, project_workdir)

        # Read the log file instead of stdout
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                log_content = f.read()

            if log_content:
                if len(log_content) > 4000:
                    await update.message.reply_text( log_content[:4000] + "\n\n[Output truncated...]")
                else:
                    await update.message.reply_text( log_content)
            else:
                await update.message.reply_text( "No cost data available in log file.")
        else:
            await update.message.reply_text( "claude-monitor.log file not found. Make sure claude-monitor is installed.")

    except subprocess.TimeoutExpired:
        await update.message.reply_text( "Command timed out after 30 seconds")
    except Exception as e:
        logger.error(f"Error running claude-monitor command: {e}")
        await update.message.reply_text( f"Error fetching cost data: {str(e)}")


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

/cancel [project-name]
  Cancel running Claude query (cancels all if no project specified)

/log <project-name> [lines]
  Show last N lines of project logs (default: 50)

/cost
  Display Claude API usage costs"""

    await update.message.reply_text( help_text)
