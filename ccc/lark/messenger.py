"""Lark-specific messenger implementation."""

import json
import logging
from typing import Any, Optional

from ccc.messenger import Messenger

logger = logging.getLogger(__name__)


class LarkMessenger(Messenger):
    """Lark-specific messenger implementation."""

    def __init__(self, client):
        """Initialize the Lark messenger.

        Args:
            client: Lark SDK client instance
        """
        self.client = client
        # Store thread contexts: {project_name: {"chat_id": str, "root_id": str}}
        self.thread_contexts = {}

    async def reply(self, context: dict, text: str) -> None:
        """Send a reply message via Lark.

        Args:
            context: Dict containing chat_id, message_id, and optionally root_id
            text: The text message to send
        """
        try:
            from lark_oapi.api.im.v1 import (
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )

            # Use root_id for thread replies, fall back to message_id
            root_id = context.get("root_id") or context.get("message_id")

            if not root_id:
                logger.error("No message_id or root_id in context for reply")
                return

            # Build and send the reply
            request = (
                ReplyMessageRequest.builder()
                .message_id(root_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(json.dumps({"text": text}))
                    .msg_type("text")
                    .build()
                )
                .build()
            )

            response = self.client.im.v1.message.reply(request)

            if not response.success():
                logger.error(
                    f"Failed to send Lark reply: code={response.code}, msg={response.msg}"
                )
            else:
                logger.info(f"Sent Lark reply to message {root_id}")

        except Exception as e:
            logger.error(f"Error sending Lark reply: {e}")

    def get_thread_context(self, context: dict) -> Optional[str]:
        """Get thread/conversation context from Lark message.

        Args:
            context: Dict containing chat_id, message_id, etc.

        Returns:
            Root message ID for thread or None
        """
        return context.get("root_id") or context.get("message_id")

    def set_thread_context(self, project_name: str, context: dict):
        """Store thread context for a project.

        Args:
            project_name: Name of the project
            context: Dict containing chat_id and root_id
        """
        self.thread_contexts[project_name] = {
            "chat_id": context.get("chat_id"),
            "root_id": context.get("root_id") or context.get("message_id"),
        }
        logger.info(f"Stored thread context for project {project_name}")

    def get_project_thread(self, project_name: str) -> Optional[dict]:
        """Get stored thread context for a project.

        Args:
            project_name: Name of the project

        Returns:
            Thread context dict or None
        """
        return self.thread_contexts.get(project_name)

    def clear_project_thread(self, project_name: str):
        """Clear stored thread context for a project.

        Args:
            project_name: Name of the project
        """
        self.thread_contexts.pop(project_name, None)
        logger.info(f"Cleared thread context for project {project_name}")
