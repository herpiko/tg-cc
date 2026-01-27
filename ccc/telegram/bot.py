"""Telegram bot application for ccc."""

import asyncio
import logging
import os
import signal

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

from ccc import config
from ccc.telegram import handlers
from ccc.telegram.messenger import TelegramMessenger

# Ensure ~/.local/bin is in PATH for commands like claude-monitor
user_local_bin = os.path.expanduser("~/.local/bin")
if user_local_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{user_local_bin}:{os.environ.get('PATH', '')}"

logger = logging.getLogger(__name__)

# Global messenger instance
messenger = None


def get_messenger() -> TelegramMessenger:
    """Get the global TelegramMessenger instance."""
    global messenger
    if messenger is None:
        messenger = TelegramMessenger()
    return messenger


async def send_startup_messages(application: Application) -> None:
    """Send startup notification to all authorized groups."""
    logger.info("Sending startup notifications to authorized groups...")

    for group_id in config.get_telegram_authorized_group_ids():
        try:
            thread_id = config.get_telegram_thread_id(group_id)
            await application.bot.send_message(
                chat_id=group_id,
                text="Agent is now online and ready to receive commands.",
                message_thread_id=thread_id
            )
            thread_info = f" (thread {thread_id})" if thread_id else ""
            logger.info(f"Sent startup message to group {group_id}{thread_info}")
        except Exception as e:
            logger.error(f"Failed to send startup message to group {group_id}: {e}")


async def send_shutdown_messages(application: Application) -> None:
    """Send shutdown notification to all authorized groups."""
    logger.info("Sending shutdown notifications to authorized groups...")

    for group_id in config.get_telegram_authorized_group_ids():
        try:
            thread_id = config.get_telegram_thread_id(group_id)
            await application.bot.send_message(
                chat_id=group_id,
                text="Agent is going offline.",
                message_thread_id=thread_id
            )
            thread_info = f" (thread {thread_id})" if thread_id else ""
            logger.info(f"Sent shutdown message to group {group_id}{thread_info}")
        except Exception as e:
            logger.error(f"Failed to send shutdown message to group {group_id}: {e}")


def run(config_path: str = None):
    """Run the Telegram bot."""
    if config_path:
        config.load_config(config_path)

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error('telegram_bot_token not set in config.yaml')
        return

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("help", handlers.cmd_help))
    application.add_handler(CommandHandler("ask", handlers.cmd_ask))
    application.add_handler(CommandHandler("feat", handlers.cmd_feat))
    application.add_handler(CommandHandler("fix", handlers.cmd_fix))
    application.add_handler(CommandHandler("plan", handlers.cmd_plan))
    application.add_handler(CommandHandler("feedback", handlers.cmd_feedback))
    application.add_handler(CommandHandler("init", handlers.cmd_init))
    application.add_handler(CommandHandler("up", handlers.cmd_up))
    application.add_handler(CommandHandler("stop", handlers.cmd_stop))
    application.add_handler(CommandHandler("status", handlers.cmd_status))
    application.add_handler(CommandHandler("cancel", handlers.cmd_cancel))
    application.add_handler(CommandHandler("log", handlers.cmd_log))
    application.add_handler(CommandHandler("cost", handlers.cmd_cost))
    application.add_handler(CommandHandler("selfupdate", handlers.cmd_selfupdate))

    # Register message handler for mentions
    application.add_handler(MessageHandler(
        filters.ALL,
        handlers.handle_message
    ))

    logger.info("Telegram bot is starting...")
    logger.info("Listening for messages and commands...")

    # Run with custom signal handling to send shutdown messages
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def main():
        async with application:
            await application.start()
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

            # Send startup messages after application is ready
            await send_startup_messages(application)

            # Wait for stop signal
            stop_event = asyncio.Event()

            def signal_handler():
                stop_event.set()

            loop.add_signal_handler(signal.SIGINT, signal_handler)
            loop.add_signal_handler(signal.SIGTERM, signal_handler)

            await stop_event.wait()

            # Send shutdown messages before stopping
            await send_shutdown_messages(application)

            await application.updater.stop()
            await application.stop()

    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
