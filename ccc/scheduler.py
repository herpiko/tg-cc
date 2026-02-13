"""Scheduled reminder system for ccc bot.

Runs reminders at configured times, sends Claude's response to all active
chat platforms, and creates thread contexts so users can reply to continue.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime

from . import config
from . import claude

logger = logging.getLogger(__name__)


def should_fire(reminder: dict, index: int, last_fired: dict) -> bool:
    """Check if a reminder should fire now.

    Args:
        reminder: Parsed reminder dict from config
        index: Reminder index for duplicate tracking
        last_fired: Dict of {index: datetime} tracking last fire times

    Returns:
        True if the reminder should fire now
    """
    now = datetime.now(reminder["parsed_tz"])
    day_name = now.strftime('%A').lower()

    if day_name not in reminder["repeat"]:
        return False

    if now.hour != reminder["parsed_hour"] or now.minute != reminder["parsed_minute"]:
        return False

    # Prevent duplicate fires within 60s
    if index in last_fired:
        elapsed = (datetime.now() - last_fired[index]).total_seconds()
        if elapsed < 60:
            return False

    return True


async def fire_reminder(reminder: dict):
    """Fire a single reminder: run Claude query and send results to all chats.

    Args:
        reminder: Parsed reminder dict from config
    """
    query_id = f"reminder-{str(uuid.uuid4())[:8]}"
    output_file = f"/tmp/output_{query_id}.txt"

    logger.info(f"Firing reminder {query_id}: {reminder['prompt'][:80]}...")

    try:
        system_prompt = config.GENERAL_RULES if config.GENERAL_RULES else ""

        prompt = f"""You are a helpful assistant responding to a scheduled reminder.

Prompt: {reminder['prompt']}

IMPORTANT: Write your complete response to the file {output_file}. Use the Write tool to create this file with your response."""

        duration_minutes, session_id = await claude.run_claude_query(
            prompt, system_prompt, "/tmp",
            project_name="_reminder", command="reminder",
            user_prompt=reminder["prompt"], query_id=query_id
        )

        # Read output
        output_text = ""
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                output_text = f.read()
            os.remove(output_file)
            logger.info(f"Cleaned up {output_file}")

        if not output_text:
            logger.warning(f"Reminder {query_id} produced no output")
            output_text = f"(Reminder completed in {duration_minutes:.1f}m but produced no output)"

        # Truncate if needed
        if len(output_text) > 4000:
            output_text = output_text[:4000] + "\n\n[Output truncated...]"

        # Send to all active platforms
        await _send_to_all_chats(query_id, session_id, output_text)

    except Exception as e:
        logger.error(f"Error firing reminder {query_id}: {e}", exc_info=True)
        # Clean up output file on error
        if os.path.exists(output_file):
            os.remove(output_file)


async def _send_to_all_chats(query_id: str, session_id: str, text: str):
    """Send reminder output to all configured Telegram groups and Lark chats.

    Also stores thread context so users can reply to continue the conversation.

    Args:
        query_id: The reminder query ID
        session_id: Claude session ID from the query
        text: The text to send
    """
    # Send to Telegram groups
    if config.TELEGRAM_BOT_TOKEN:
        for group_info in config.TELEGRAM_AUTHORIZED_GROUPS:
            chat_id = group_info["group"]
            thread_id = int(group_info["sub"]) if group_info.get("sub") else None
            try:
                message_id = await _send_to_telegram(chat_id, text, thread_id)
                if message_id:
                    # Store thread context using the topic thread_id (same as other commands)
                    thread_key = claude.get_thread_key_telegram(
                        chat_id,
                        str(thread_id) if thread_id is not None else None
                    )
                    claude.set_thread_worktree(
                        thread_key, query_id, session_id,
                        None, None, "_reminder", None
                    )
                    logger.info(f"Sent reminder to Telegram chat {chat_id}, thread_key={thread_key}")
            except Exception as e:
                logger.error(f"Failed to send reminder to Telegram chat {chat_id}: {e}")

    # Send to Lark chats
    if config.LARK_APP_ID:
        for chat_id in config.LARK_AUTHORIZED_CHATS:
            try:
                message_id = _send_to_lark(chat_id, text)
                if message_id:
                    # For Lark, the message_id becomes the root_id for thread replies
                    thread_key = claude.get_thread_key_lark(chat_id, message_id)
                    claude.set_thread_worktree(
                        thread_key, query_id, session_id,
                        None, None, "_reminder", None
                    )
                    logger.info(f"Sent reminder to Lark chat {chat_id}, thread_key={thread_key}")
            except Exception as e:
                logger.error(f"Failed to send reminder to Lark chat {chat_id}: {e}")


async def _send_to_telegram(chat_id: str, text: str, thread_id: int = None) -> str | None:
    """Send a proactive message to Telegram using a standalone Bot instance.

    Args:
        chat_id: Telegram chat ID
        text: Message text
        thread_id: Optional message_thread_id for topic groups

    Returns:
        message_id as string, or None on failure
    """
    try:
        import telegram

        bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
        async with bot:
            msg = await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                message_thread_id=thread_id
            )
            return str(msg.message_id)
    except Exception as e:
        logger.error(f"Error sending Telegram message to {chat_id}: {e}")
        return None


def _send_to_lark(chat_id: str, text: str) -> str | None:
    """Send a proactive message to Lark using a standalone Client instance.

    Args:
        chat_id: Lark chat ID
        text: Message text

    Returns:
        message_id as string, or None on failure
    """
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = (
            lark.Client.builder()
            .app_id(config.LARK_APP_ID)
            .app_secret(config.LARK_APP_SECRET)
            .build()
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .content(json.dumps({"text": text}))
                .msg_type("text")
                .uuid(str(uuid.uuid4()))
                .build()
            )
            .build()
        )

        response = client.im.v1.message.create(request)
        if response.success():
            return response.data.message_id
        else:
            logger.error(f"Lark send failed: code={response.code}, msg={response.msg}")
            return None
    except ImportError:
        logger.error("lark-oapi not installed, cannot send Lark reminder")
        return None
    except Exception as e:
        logger.error(f"Error sending Lark message to {chat_id}: {e}")
        return None


async def run_scheduler():
    """Main scheduler loop. Checks every 30s and fires matching reminders."""
    last_fired = {}
    logger.info(f"Scheduler started, monitoring {len(config.REMINDERS)} reminder(s)")
    for i, r in enumerate(config.REMINDERS):
        logger.info(f"  Reminder [{i}]: {r['time']} days={r['repeat']} prompt={r['prompt'][:60]}")

    check_count = 0
    while True:
        check_count += 1
        for i, reminder in enumerate(config.REMINDERS):
            now = datetime.now(reminder["parsed_tz"])
            # Log every ~5 minutes (every 10th check at 30s intervals)
            if check_count % 10 == 1:
                logger.info(
                    f"Scheduler check #{check_count}: reminder [{i}] target={reminder['parsed_hour']:02d}:{reminder['parsed_minute']:02d} "
                    f"now={now.strftime('%A %H:%M:%S')} tz={reminder['parsed_tz']}"
                )

            if should_fire(reminder, i, last_fired):
                last_fired[i] = datetime.now()
                logger.info(f"Firing reminder [{i}]!")
                # Run fire_reminder directly (awaited) instead of create_task
                # to avoid issues with the event loop in a daemon thread
                try:
                    await fire_reminder(reminder)
                except Exception as e:
                    logger.error(f"Error in fire_reminder [{i}]: {e}", exc_info=True)

        await asyncio.sleep(30)


def start_scheduler_thread():
    """Start the scheduler in a daemon thread with its own event loop."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_scheduler())
        except Exception as e:
            logger.error(f"Scheduler crashed: {e}", exc_info=True)
        finally:
            loop.close()

    thread = threading.Thread(target=_run, daemon=True, name="Scheduler")
    thread.start()
    logger.info("Scheduler thread started")
    return thread
