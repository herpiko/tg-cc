"""Unified entry point for ccc bot (Telegram and Lark support)."""

import argparse
import logging
import os
import threading

from . import config

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point for the ccc command."""
    parser = argparse.ArgumentParser(
        description="Claude Code Chat Bot for Telegram and Lark"
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to config.yaml file (default: ./config.yaml)"
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Run Telegram bot only"
    )
    parser.add_argument(
        "--lark",
        action="store_true",
        help="Run Lark bot only"
    )

    args = parser.parse_args()

    # If config path not specified, look in current directory
    config_path = args.config
    if config_path is None:
        config_path = os.path.join(os.getcwd(), "config.yaml")
        if not os.path.exists(config_path):
            # Try looking relative to package
            config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
            config_path = os.path.abspath(config_path)

    # Load configuration
    config.load_config(config_path)

    # Determine which bots to run
    # Default: run all available bots if neither flag is specified
    run_telegram = args.telegram or (not args.telegram and not args.lark)
    run_lark = args.lark or (not args.telegram and not args.lark)

    threads = []

    # Start Telegram bot if configured
    if run_telegram and config.TELEGRAM_BOT_TOKEN:
        logger.info("Telegram bot token found, starting Telegram bot...")
        from ccc.telegram import bot as tg_bot

        def run_telegram_bot():
            tg_bot.run(config_path)

        telegram_thread = threading.Thread(target=run_telegram_bot, daemon=True, name="TelegramBot")
        threads.append(telegram_thread)
    elif run_telegram:
        logger.warning("--telegram specified but no telegram_bot_token in config")

    # Start Lark bot if configured
    if run_lark and config.LARK_APP_ID:
        logger.info("Lark app_id found, starting Lark bot...")
        from ccc.lark import bot as lark_bot

        def run_lark_bot():
            lark_bot.run(config_path)

        lark_thread = threading.Thread(target=run_lark_bot, daemon=True, name="LarkBot")
        threads.append(lark_thread)
    elif run_lark:
        logger.warning("--lark specified but no lark.app_id in config")

    if not threads:
        logger.error("No bots configured. Please add telegram_bot_token or lark configuration to config.yaml")
        return

    # Start all bot threads
    for thread in threads:
        logger.info(f"Starting {thread.name}...")
        thread.start()

    logger.info(f"Started {len(threads)} bot(s)")

    # Wait for all threads
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
