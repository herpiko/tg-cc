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
        logger.info(f"Attempting to reply with context: {context}")

        try:
            from lark_oapi.api.im.v1 import (
                ReplyMessageRequest,
                ReplyMessageRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
            )
            import uuid as uuid_lib

            chat_id = context.get("chat_id")
            message_id = context.get("message_id")
            # For thread replies, use root_id if available, otherwise use message_id as root
            root_id = context.get("root_id") or message_id

            if root_id:
                # Use ReplyMessageRequest with reply_in_thread=True for true thread replies
                logger.info(f"Sending thread reply to message: {root_id}, reply_in_thread=True")
                request = (
                    ReplyMessageRequest.builder()
                    .message_id(root_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(json.dumps({"text": text}))
                        .msg_type("text")
                        .reply_in_thread(True)
                        .uuid(str(uuid_lib.uuid4()))
                        .build()
                    )
                    .build()
                )

                response = self.client.im.v1.message.reply(request)
                logger.info(f"Reply response: success={response.success()}, code={response.code}, msg={response.msg}")

                if not response.success():
                    logger.error(
                        f"Failed to send Lark thread reply: code={response.code}, msg={response.msg}"
                    )
                else:
                    logger.info(f"Sent Lark thread reply to message {root_id}")

            elif chat_id:
                # Fallback: send to chat directly if no message_id/root_id
                logger.warning(f"No message_id/root_id, sending to chat_id: {chat_id}")
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .content(json.dumps({"text": text}))
                        .msg_type("text")
                        .uuid(str(uuid_lib.uuid4()))
                        .build()
                    )
                    .build()
                )

                response = self.client.im.v1.message.create(request)

                if not response.success():
                    logger.error(
                        f"Failed to send Lark message: code={response.code}, msg={response.msg}"
                    )
                else:
                    logger.info(f"Sent Lark message to chat {chat_id}")
            else:
                logger.error("No message_id, root_id, or chat_id in context for reply")

        except Exception as e:
            logger.error(f"Error sending Lark reply: {e}", exc_info=True)

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
