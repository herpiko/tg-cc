"""Message deduplication for Lark bot using SQLite."""

import logging
import os
import sqlite3
import time
from threading import Lock

logger = logging.getLogger(__name__)

# Database path
DB_PATH = os.path.expanduser("~/.ccc_lark_dedup.db")

# Lock for thread-safe database access
_db_lock = Lock()

# Cache expiry time (24 hours in seconds)
CACHE_EXPIRY = 24 * 60 * 60


def _get_connection():
    """Get a database connection and ensure table exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_messages (
            message_id TEXT PRIMARY KEY,
            event_id TEXT,
            processed_at REAL
        )
    """)
    conn.commit()
    return conn


def is_duplicate(message_id: str, event_id: str = None) -> bool:
    """Check if a message has already been processed.

    Args:
        message_id: The Lark message ID
        event_id: The Lark event ID (optional, for additional dedup)

    Returns:
        True if this message was already processed
    """
    if not message_id and not event_id:
        return False

    with _db_lock:
        try:
            conn = _get_connection()
            cursor = conn.cursor()

            # Check by message_id
            if message_id:
                cursor.execute(
                    "SELECT 1 FROM processed_messages WHERE message_id = ?",
                    (message_id,)
                )
                if cursor.fetchone():
                    logger.info(f"Duplicate message detected: {message_id}")
                    conn.close()
                    return True

            # Also check by event_id if provided
            if event_id:
                cursor.execute(
                    "SELECT 1 FROM processed_messages WHERE event_id = ?",
                    (event_id,)
                )
                if cursor.fetchone():
                    logger.info(f"Duplicate event detected: {event_id}")
                    conn.close()
                    return True

            conn.close()
            return False

        except Exception as e:
            logger.error(f"Error checking for duplicate: {e}")
            return False


def mark_processed(message_id: str, event_id: str = None):
    """Mark a message as processed.

    Args:
        message_id: The Lark message ID
        event_id: The Lark event ID (optional)
    """
    if not message_id and not event_id:
        return

    with _db_lock:
        try:
            conn = _get_connection()
            cursor = conn.cursor()

            # Use message_id as primary key, store event_id too
            key = message_id or event_id
            cursor.execute(
                "INSERT OR REPLACE INTO processed_messages (message_id, event_id, processed_at) VALUES (?, ?, ?)",
                (key, event_id, time.time())
            )
            conn.commit()
            conn.close()

            logger.info(f"Marked message as processed: {key}")

        except Exception as e:
            logger.error(f"Error marking message as processed: {e}")


def cleanup_old_entries():
    """Remove entries older than CACHE_EXPIRY."""
    with _db_lock:
        try:
            conn = _get_connection()
            cursor = conn.cursor()

            cutoff = time.time() - CACHE_EXPIRY
            cursor.execute(
                "DELETE FROM processed_messages WHERE processed_at < ?",
                (cutoff,)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old dedup entries")

        except Exception as e:
            logger.error(f"Error cleaning up old entries: {e}")
