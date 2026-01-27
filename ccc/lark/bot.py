"""Lark bot application for ccc - Flask webhook server."""

import asyncio
import hashlib
import json
import logging
import os

from flask import Flask, request, jsonify

from ccc import config
from ccc.lark.messenger import LarkMessenger
from ccc.lark import handlers

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
    if not config.LARK_VERIFICATION_TOKEN:
        return True  # Skip verification if no token configured

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
        data = request.get_json()

        # Handle URL verification challenge
        if "challenge" in data:
            logger.info("Received URL verification challenge")
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
        if "header" in data:
            event_type = data.get("header", {}).get("event_type", "")
            event = data.get("event", {})

            logger.info(f"Received event type: {event_type}")

            # Handle message events
            if event_type == "im.message.receive_v1":
                message = event.get("message", {})
                message_type = message.get("message_type", "")

                # Only handle text messages
                if message_type == "text":
                    # Run the handler asynchronously
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(handlers.handle_message(messenger, event))
                    finally:
                        loop.close()

            return jsonify({"code": 0, "msg": "success"})

        # Handle v1 event format (legacy)
        event_type = data.get("type", "")
        if event_type == "url_verification":
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
