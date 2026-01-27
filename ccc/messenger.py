"""Abstract messenger interface for platform-agnostic messaging."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class Messenger(ABC):
    """Abstract interface for platform-specific messaging."""

    @abstractmethod
    async def reply(self, context: Any, text: str) -> None:
        """Send a reply message.

        Args:
            context: Platform-specific context (update object, message dict, etc.)
            text: The text message to send
        """
        pass

    @abstractmethod
    def get_thread_context(self, context: Any) -> Optional[str]:
        """Get thread/conversation context for replies.

        Args:
            context: Platform-specific context

        Returns:
            Thread identifier string or None
        """
        pass
