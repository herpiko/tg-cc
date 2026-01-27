"""Telegram-specific messenger implementation."""

from typing import Optional

from telegram import Update

from ccc.messenger import Messenger
from ccc import config


class TelegramMessenger(Messenger):
    """Telegram-specific messenger implementation."""

    async def reply(self, update: Update, text: str) -> None:
        """Send a reply message via Telegram.

        Args:
            update: Telegram Update object
            text: The text message to send
        """
        if not update.message:
            return
        chat_id = str(update.message.chat.id)
        thread_id = config.get_telegram_thread_id(chat_id)
        await update.message.reply_text(text, message_thread_id=thread_id)

    def get_thread_context(self, update: Update) -> Optional[str]:
        """Get thread/conversation context from Telegram update.

        Args:
            update: Telegram Update object

        Returns:
            Chat ID as string or None
        """
        if not update.message:
            return None
        return str(update.message.chat.id)
