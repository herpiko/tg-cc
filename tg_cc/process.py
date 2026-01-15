"""Process management for tg-cc bot."""

import logging
import os
import signal
import subprocess
from collections import deque

logger = logging.getLogger(__name__)


# Process storage for running project instances: {project_name: (subprocess.Popen, log_file_path)}
PROJECT_PROCESSES = {}


async def spin_up_project(update, project_name: str, project_workdir: str, project_up: str) -> bool:
    """Spin up a project using project_up command. Stores the process for later termination."""
    if not project_up:
        logger.info(f"No project_up command configured for {project_name}")
        return True

    # Kill existing process if any
    await kill_project_process(update, project_name, silent=True)

    try:
        await update.message.reply_text(f"Spinning up project {project_name}...")
        logger.info(f"Running project_up command for {project_name}: {project_up}")

        # Create log file for output
        log_file_path = f"/tmp/tg_cc_{project_name}.log"
        log_file = open(log_file_path, 'w')

        # Run the command in background using Popen, redirect output to log file
        process = subprocess.Popen(
            project_up,
            shell=True,
            cwd=project_workdir,
            stdout=log_file,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            start_new_session=True  # Detach from parent process group
        )

        PROJECT_PROCESSES[project_name] = (process, log_file_path, log_file)
        logger.info(f"Started process {process.pid} for project {project_name}, logging to {log_file_path}")
        await update.message.reply_text(f"Project {project_name} started (PID: {process.pid})")
        return True

    except Exception as e:
        logger.error(f"Error spinning up project {project_name}: {e}")
        await update.message.reply_text(f"Error spinning up project: {str(e)}")
        return False


async def kill_project_process(update, project_name: str, silent: bool = False) -> bool:
    """Kill a running project process."""
    process_info = PROJECT_PROCESSES.get(project_name)
    if not process_info:
        if not silent:
            await update.message.reply_text(f"No running process found for project {project_name}")
        return False

    process, log_file_path, log_file = process_info

    try:
        # Kill the entire process group
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
        logger.info(f"Killed process {process.pid} for project {project_name}")
        if not silent:
            await update.message.reply_text(f"Stopped project {project_name} (PID: {process.pid})")
    except subprocess.TimeoutExpired:
        # Force kill if SIGTERM didn't work
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        logger.info(f"Force killed process {process.pid} for project {project_name}")
        if not silent:
            await update.message.reply_text(f"Force stopped project {project_name} (PID: {process.pid})")
    except ProcessLookupError:
        logger.info(f"Process {process.pid} for project {project_name} already terminated")
    except Exception as e:
        logger.error(f"Error killing process for {project_name}: {e}")
        if not silent:
            await update.message.reply_text(f"Error stopping project: {str(e)}")
        return False

    # Close log file
    try:
        log_file.close()
    except Exception:
        pass

    PROJECT_PROCESSES.pop(project_name, None)
    return True


def get_running_projects() -> dict:
    """Get dictionary of running project processes. Returns {project_name: (process, log_path, log_file)}."""
    return PROJECT_PROCESSES


def get_process_status(project_name: str) -> tuple:
    """Get status of a project process. Returns (is_running, pid, returncode)."""
    process_info = PROJECT_PROCESSES.get(project_name)
    if not process_info:
        return (False, None, None)

    process, _, _ = process_info
    poll_result = process.poll()
    if poll_result is None:
        return (True, process.pid, None)
    else:
        return (False, process.pid, poll_result)


def get_project_logs(project_name: str, lines: int = 50) -> str | None:
    """Get the last N lines from a project's log file."""
    process_info = PROJECT_PROCESSES.get(project_name)
    if not process_info:
        return None

    _, log_file_path, _ = process_info

    if not os.path.exists(log_file_path):
        return None

    try:
        # Read last N lines efficiently
        with open(log_file_path, 'r') as f:
            return ''.join(deque(f, maxlen=lines))
    except Exception as e:
        logger.error(f"Error reading log file for {project_name}: {e}")
        return None
