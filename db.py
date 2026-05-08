"""db.py — MongoDB connection singleton for the Nyaay platform."""

import os
import logging
from pymongo import MongoClient, ASCENDING

logger = logging.getLogger(__name__)

_client: MongoClient | None = None
_database = None


def get_db():
    """Return the shared MongoDB database object, connecting on first call."""
    global _client, _database
    if _database is None:
        uri = os.getenv("MONGODB_URI", "")
        if not uri:
            raise RuntimeError(
                "MONGODB_URI environment variable is not set. "
                "Add MONGODB_URI=mongodb+srv://... to your .env file."
            )
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _database = _client["nyaay"]
        logger.info("MongoDB connection established (database: nyaay).")
    return _database


def init_indexes():
    """Create indexes needed for the three collections. Safe to call repeatedly."""
    db = get_db()
    # messages: queries by session_id ordered by timestamp
    db.messages.create_index(
        [("session_id", ASCENDING), ("timestamp", ASCENDING)],
        name="messages_session_time",
    )
    # case_sessions: queries by chat_session_id ordered by created_at
    db.case_sessions.create_index(
        [("chat_session_id", ASCENDING), ("created_at", ASCENDING)],
        name="case_sessions_chat_session_time",
    )
    logger.info("MongoDB indexes ensured.")
