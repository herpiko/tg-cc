"""Lark bot application for ccc - Flask webhook server."""

import asyncio
import hashlib
import json
import logging
import os

from flask import Flask, request, jsonify

from ccc import config
from ccc import process
from ccc.lark.messenger import LarkMessenger
from ccc.lark import handlers
from ccc.lark import dedup

# Ensure ~/.local/bin is in PATH for commands like claude-monitor
user_local_bin = os.path.expanduser("~/.local/bin")
if user_local_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{user_local_bin}:{os.environ.get('PATH', '')}"

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global instances
lark_client = None
messenger = None


def verify_signature(timestamp: str, nonce: str, body: str, signature: str) -> bool:
    """Verify the signature of an incoming webhook request.

    Args:
        timestamp: Request timestamp
        nonce: Request nonce
        body: Request body
        signature: Provided signature

    Returns:
        True if signature is valid
    """
    # Skip verification if no token configured
    if not config.LARK_VERIFICATION_TOKEN or config.LARK_VERIFICATION_TOKEN in ("", "xxx"):
        logger.info("Skipping signature verification (no token configured)")
        return True

    string_to_sign = timestamp + nonce + config.LARK_VERIFICATION_TOKEN + body
    calculated_signature = hashlib.sha256(string_to_sign.encode('utf-8')).hexdigest()

    return calculated_signature == signature


def decrypt_message(encrypt_key: str, encrypted_data: str) -> str:
    """Decrypt an encrypted message from Lark.

    Args:
        encrypt_key: The encryption key
        encrypted_data: Base64 encoded encrypted data

    Returns:
        Decrypted message as string
    """
    import base64
    from Crypto.Cipher import AES

    # Derive key from encrypt_key
    key = hashlib.sha256(encrypt_key.encode('utf-8')).digest()

    # Decode the encrypted data
    encrypted_bytes = base64.b64decode(encrypted_data)

    # Create AES cipher
    cipher = AES.new(key, AES.MODE_CBC, iv=encrypted_bytes[:16])

    # Decrypt
    decrypted = cipher.decrypt(encrypted_bytes[16:])

    # Remove PKCS7 padding
    padding_length = decrypted[-1]
    decrypted = decrypted[:-padding_length]

    return decrypted.decode('utf-8')


@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle incoming Lark webhook requests."""
    global messenger

    try:
        # Get request data
        raw_body = request.get_data(as_text=True)
        logger.info(f"Received webhook request (truncated): {raw_body[:200]}...")

        try:
            data = request.get_json()
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")
            return jsonify({"code": 1, "msg": "Invalid JSON"})

        if data is None:
            logger.error("Request body is not valid JSON")
            return jsonify({"code": 1, "msg": "Invalid JSON"})

        # Handle URL verification challenge (v2 format)
        if "challenge" in data:
            logger.info(f"Received URL verification challenge: {data['challenge']}")
            return jsonify({"challenge": data["challenge"]})

        # Handle encrypted events
        if "encrypt" in data:
            if not config.LARK_ENCRYPT_KEY:
                logger.error("Received encrypted event but no encrypt_key configured")
                return jsonify({"code": 1, "msg": "No encrypt key"})

            try:
                decrypted = decrypt_message(config.LARK_ENCRYPT_KEY, data["encrypt"])
                data = json.loads(decrypted)
            except Exception as e:
                logger.error(f"Failed to decrypt message: {e}")
                return jsonify({"code": 1, "msg": "Decryption failed"})

        # Verify signature if provided
        signature = request.headers.get("X-Lark-Signature", "")
        timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
        nonce = request.headers.get("X-Lark-Request-Nonce", "")

        if signature and not verify_signature(timestamp, nonce, raw_body, signature):
            logger.warning("Invalid signature on webhook request")
            return jsonify({"code": 1, "msg": "Invalid signature"})

        # Handle event callback
        logger.info(f"Checking for header in data: {'header' in data}")
        logger.info(f"Messenger initialized: {messenger is not None}")

        if "header" in data:
            event_type = data.get("header", {}).get("event_type", "")
            event = data.get("event", {})

            logger.info(f"Received event type: {event_type}")

            # Handle message events
            if event_type == "im.message.receive_v1":
                message = event.get("message", {})
                message_type = message.get("message_type", "")
                content = message.get("content", "")

                # Log full message structure to see all available fields
                logger.info(f"=== FULL MESSAGE OBJECT ===")
                for key, value in message.items():
                    logger.info(f"  {key}: {value}")
                logger.info(f"=== END MESSAGE OBJECT ===")

                logger.info(f"Message type: {message_type}, content: {content[:200]}")

                # Check if it's a processable message (text or post/rich-text)
                is_processable = message_type in ("text", "post")
                if not is_processable and content:
                    try:
                        content_dict = json.loads(content)
                        is_processable = "text" in content_dict or "content" in content_dict
                    except (json.JSONDecodeError, TypeError):
                        pass

                if is_processable:
                    logger.info("Processing as text message")

                    # Extract message_id and event_id for deduplication
                    message_id = message.get("message_id") or message.get("msg_id") or message.get("id")
                    event_id = data.get("header", {}).get("event_id")

                    logger.info(f"Dedup check: message_id={message_id}, event_id={event_id}")

                    # Check for duplicate message
                    if dedup.is_duplicate(message_id, event_id):
                        logger.info(f"Skipping duplicate message: {message_id}")
                        return jsonify({"code": 0, "msg": "success"})

                    # Mark as processed before handling to prevent race conditions
                    dedup.mark_processed(message_id, event_id)

                    # Run the handler asynchronously
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(handlers.handle_message(messenger, event))
                    except Exception as e:
                        logger.error(f"Error in message handler: {e}")
                    finally:
                        loop.close()
                else:
                    logger.info(f"Skipping non-text message type: {message_type}")

            return jsonify({"code": 0, "msg": "success"})

        # Handle v1 event format (legacy)
        event_type = data.get("type", "")
        if event_type == "url_verification":
            logger.info(f"Received URL verification (v1 format): {data.get('challenge', '')}")
            return jsonify({"challenge": data.get("challenge", "")})

        return jsonify({"code": 0, "msg": "success"})

    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy"})


def run(config_path: str = None):
    """Run the Lark bot webhook server.

    Args:
        config_path: Path to config file (optional, may already be loaded)
    """
    global lark_client, messenger

    if config_path:
        config.load_config(config_path)

    if not config.LARK_APP_ID or not config.LARK_APP_SECRET:
        logger.error("Lark app_id or app_secret not set in config.yaml")
        return

    try:
        import lark_oapi as lark

        # Initialize Lark client
        lark_client = (
            lark.Client.builder()
            .app_id(config.LARK_APP_ID)
            .app_secret(config.LARK_APP_SECRET)
            .build()
        )

        messenger = LarkMessenger(lark_client)

        # Clean up old dedup entries on startup
        dedup.cleanup_old_entries()

        # Auto-start all configured projects
        logger.info("Auto-starting configured projects...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(process.startup_all_projects())
            if results:
                summary = process.format_startup_summary(results)
                logger.info(summary)
                # Send summary to all authorized chats
                for chat_id in config.LARK_AUTHORIZED_CHATS:
                    try:
                        # Use reply with just chat_id in context to send direct message
                        loop.run_until_complete(messenger.reply({"chat_id": chat_id}, summary))
                    except Exception as e:
                        logger.error(f"Failed to send startup summary to chat {chat_id}: {e}")
            else:
                logger.info("No projects with project_up configured for auto-start")
        except Exception as e:
            logger.error(f"Error during project auto-start: {e}")
        finally:
            loop.close()

        logger.info(f"Lark bot is starting on port {config.LARK_WEBHOOK_PORT}...")
        logger.info("Listening for webhook events...")

        # Run Flask app
        app.run(
            host="0.0.0.0",
            port=config.LARK_WEBHOOK_PORT,
            debug=False,
            use_reloader=False
        )

    except ImportError:
        logger.error("lark-oapi package not installed. Install with: pip install lark-oapi")
        return
    except Exception as e:
        logger.error(f"Error starting Lark bot: {e}")
        return
