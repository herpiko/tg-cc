"""Main bot application for tg-cc."""

import logging
import os

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

from . import config
from . import handlers

# Ensure ~/.local/bin is in PATH for commands like claude-monitor
user_local_bin = os.path.expanduser("~/.local/bin")
if user_local_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{user_local_bin}:{os.environ.get('PATH', '')}"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def send_startup_messages(application: Application) -> None:
    """Send startup notification to all authorized groups."""
    logger.info("Sending startup notifications to authorized groups...")

    for group_id in config.AUTHORIZED_GROUPS:
        try:
            await application.bot.send_message(
                chat_id=group_id,
                text="Bot is now online and ready to receive commands."
            )
            logger.info(f"Sent startup message to group {group_id}")
        except Exception as e:
            logger.error(f"Failed to send startup message to group {group_id}: {e}")


def run(config_path: str = None):
    """Run the Telegram bot."""
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

    # Register message handler for mentions
    application.add_handler(MessageHandler(
        filters.ALL,
        handlers.handle_message
    ))

    # Set up post-init hook to send startup messages
    async def post_init(app: Application) -> None:
        await send_startup_messages(app)

    application.post_init = post_init

    logger.info("Bot is starting...")
    logger.info("Listening for messages and commands...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
