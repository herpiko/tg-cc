"""Process management for ccc bot."""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from typing import Any

from . import config
from .messenger import Messenger

logger = logging.getLogger(__name__)


# Process storage for running project instances: {project_name: (subprocess.Popen, log_file_path, log_file)}
PROJECT_PROCESSES = {}

# Background threads for output streaming: {project_name: thread}
OUTPUT_THREADS = {}


def _stream_output(process: subprocess.Popen, project_name: str, log_file_path: str):
    """Stream process output to both log file and stdout. Runs in background thread."""
    try:
        with open(log_file_path, 'w') as log_file:
            prefix = f"[{project_name}] "
            for line in iter(process.stdout.readline, b''):
                if not line:
                    break
                try:
                    decoded = line.decode('utf-8', errors='replace')
                except Exception:
                    decoded = str(line)

                # Write to log file
                log_file.write(decoded)
                log_file.flush()

                # Write to stdout with project prefix
                sys.stdout.write(prefix + decoded)
                sys.stdout.flush()

            # Wait for process to finish
            process.wait()
    except Exception as e:
        logger.error(f"Error streaming output for {project_name}: {e}")
    finally:
        OUTPUT_THREADS.pop(project_name, None)


def _kill_processes_on_ports(ports: list[str], project_name: str) -> list[tuple[str, int]]:
    """Kill all processes occupying the specified ports using lsof.

    Works on both macOS and Linux.

    Args:
        ports: List of port numbers (as strings)
        project_name: Name of the project (for logging)

    Returns:
        List of (port, pid) tuples for processes that were killed
    """
    killed = []

    for port in ports:
        try:
            # lsof -t -i :PORT returns PIDs of processes using the port
            # This works on both macOS and Linux
            result = subprocess.run(
                ['lsof', '-t', '-i', f':{port}'],
                capture_output=True,
                text=True
            )

            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid_str in pids:
                    pid_str = pid_str.strip()
                    if pid_str:
                        try:
                            pid = int(pid_str)
                            os.kill(pid, signal.SIGTERM)
                            logger.info(f"[{project_name}] Killed PID {pid} on port {port}")
                            killed.append((port, pid))
                        except (ValueError, ProcessLookupError) as e:
                            logger.warning(f"[{project_name}] Could not kill PID {pid_str} on port {port}: {e}")
                        except PermissionError:
                            logger.warning(f"[{project_name}] Permission denied killing PID {pid_str} on port {port}")
        except FileNotFoundError:
            logger.warning(f"[{project_name}] lsof not found, cannot check port {port}")
        except Exception as e:
            logger.error(f"[{project_name}] Error checking port {port}: {e}")

    return killed


def _read_log_file(log_file_path: str, lines: int = 50) -> str | None:
    """Read the last N lines from a log file."""
    if not os.path.exists(log_file_path):
        return None

    try:
        with open(log_file_path, 'r') as f:
            content = ''.join(deque(f, maxlen=lines))
            # Truncate if too long for chat message
            if len(content) > 3000:
                content = content[-3000:]
            return content.strip() if content else None
    except Exception as e:
        logger.error(f"Error reading log file {log_file_path}: {e}")
        return None


async def spin_up_project(messenger: Messenger, context: Any, project_name: str, project_workdir: str, project_up: str, project_endpoint_url: str = None, project_ports: list[str] = None) -> bool:
    """Spin up a project using project_up command. Stores the process for later termination.

    Args:
        messenger: Platform-specific messenger for sending replies (can be None for silent mode)
        context: Platform-specific context (Telegram update, Lark message dict, etc.) (can be None for silent mode)
        project_name: Name of the project
        project_workdir: Working directory for the project
        project_up: Command to start the project
        project_endpoint_url: Optional URL where the project can be accessed
        project_ports: Optional list of ports to free up before starting
    """
    if not project_up:
        logger.info(f"No project_up command configured for {project_name}")
        return True

    # Helper to send message only if messenger is available
    async def send_msg(msg: str):
        if messenger and context:
            await messenger.reply(context, msg)

    # Kill existing process if any
    await kill_project_process(messenger, context, project_name, silent=True)

    # Kill processes occupying the configured ports
    if project_ports:
        killed = _kill_processes_on_ports(project_ports, project_name)
        if killed:
            killed_info = ", ".join([f"PID {pid} on port {port}" for port, pid in killed])
            await send_msg(f"Killed processes occupying ports: {killed_info}")
            # Brief wait for ports to be released
            await asyncio.sleep(0.5)

    try:
        await send_msg(f"Spinning up project {project_name}...")
        logger.info(f"Running project_up command for {project_name}: {project_up}")

        log_file_path = f"/tmp/ccc_{project_name}.log"

        # Run the command with PIPE for stdout so we can stream it
        process = subprocess.Popen(
            project_up,
            shell=True,
            cwd=project_workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            start_new_session=True  # Detach from parent process group
        )

        # Start background thread to stream output to both log file and stdout
        output_thread = threading.Thread(
            target=_stream_output,
            args=(process, project_name, log_file_path),
            daemon=True
        )
        output_thread.start()
        OUTPUT_THREADS[project_name] = output_thread

        PROJECT_PROCESSES[project_name] = (process, log_file_path, None)
        logger.info(f"Started process {process.pid} for project {project_name}, logging to {log_file_path}")

        await send_msg(f"Project {project_name} started (PID: {process.pid}). Output streaming to console.")

        # Wait briefly and show initial output for verbose logging in chat
        await asyncio.sleep(2)

        # Check if process is still running or exited
        poll_result = process.poll()
        if poll_result is not None:
            # Process already exited
            initial_logs = _read_log_file(log_file_path, lines=30)
            if initial_logs:
                await send_msg(f"Process exited with code {poll_result}. Output:\n```\n{initial_logs}\n```")
            else:
                await send_msg(f"Process exited with code {poll_result} (no output)")
        else:
            # Process still running, show initial output
            initial_logs = _read_log_file(log_file_path, lines=20)
            if initial_logs:
                await send_msg(f"Initial output:\n```\n{initial_logs}\n```")

        # Send endpoint URL after output so user can easily access it
        if project_endpoint_url:
            await send_msg(f"Endpoint: {project_endpoint_url}")

        return True

    except Exception as e:
        logger.error(f"Error spinning up project {project_name}: {e}")
        await send_msg(f"Error spinning up project: {str(e)}")
        return False


async def kill_project_process(messenger: Messenger, context: Any, project_name: str, silent: bool = False) -> bool:
    """Kill a running project process.

    Args:
        messenger: Platform-specific messenger for sending replies (can be None)
        context: Platform-specific context (Telegram update, Lark message dict, etc.) (can be None)
        project_name: Name of the project
        silent: If True, don't send messages about the operation
    """
    # Helper to send message only if messenger is available and not silent
    async def send_msg(msg: str):
        if not silent and messenger and context:
            await messenger.reply(context, msg)

    process_info = PROJECT_PROCESSES.get(project_name)
    if not process_info:
        await send_msg(f"No running process found for project {project_name}")
        return False

    process, log_file_path, _ = process_info

    try:
        # Kill the entire process group
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
        logger.info(f"Killed process {process.pid} for project {project_name}")
        await send_msg(f"Stopped project {project_name} (PID: {process.pid})")
    except subprocess.TimeoutExpired:
        # Force kill if SIGTERM didn't work
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        logger.info(f"Force killed process {process.pid} for project {project_name}")
        await send_msg(f"Force stopped project {project_name} (PID: {process.pid})")
    except ProcessLookupError:
        logger.info(f"Process {process.pid} for project {project_name} already terminated")
    except Exception as e:
        logger.error(f"Error killing process for {project_name}: {e}")
        await send_msg(f"Error stopping project: {str(e)}")
        return False

    # Clean up output thread reference
    OUTPUT_THREADS.pop(project_name, None)

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


async def startup_all_projects() -> list[tuple[str, bool, str]]:
    """Start all configured projects that have project_up commands.

    Called during bot startup to automatically spin up all projects.

    Returns:
        List of tuples: (project_name, success, message)
    """
    results = []

    for project in config.PROJECTS:
        project_name = project.get('project_name')
        project_workdir = project.get('project_workdir')
        project_up = project.get('project_up')
        project_endpoint_url = project.get('project_endpoint_url')
        project_ports = project.get('project_ports')

        if not project_up:
            logger.info(f"[startup] Skipping {project_name} - no project_up command configured")
            continue

        if not project_workdir or not os.path.exists(project_workdir):
            msg = f"Workdir not found: {project_workdir}"
            logger.warning(f"[startup] Skipping {project_name} - {msg}")
            results.append((project_name, False, msg))
            continue

        logger.info(f"[startup] Starting project {project_name}...")

        try:
            # Run spin_up_project in silent mode (no messenger/context)
            success = await spin_up_project(
                messenger=None,
                context=None,
                project_name=project_name,
                project_workdir=project_workdir,
                project_up=project_up,
                project_endpoint_url=project_endpoint_url,
                project_ports=project_ports
            )

            if success:
                msg = f"Started successfully"
                if project_endpoint_url:
                    msg += f" - {project_endpoint_url}"
                results.append((project_name, True, msg))
            else:
                results.append((project_name, False, "Failed to start"))

        except Exception as e:
            logger.error(f"[startup] Error starting {project_name}: {e}")
            results.append((project_name, False, str(e)))

    return results


def format_startup_summary(results: list[tuple[str, bool, str]]) -> str:
    """Format startup results into a summary message.

    Args:
        results: List of (project_name, success, message) tuples

    Returns:
        Formatted summary string
    """
    if not results:
        return "No projects with project_up configured."

    lines = ["Project startup summary:"]
    for project_name, success, message in results:
        status = "OK" if success else "FAILED"
        lines.append(f"  {project_name}: {status} - {message}")

    return "\n".join(lines)
