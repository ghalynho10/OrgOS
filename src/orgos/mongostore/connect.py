"""Host side Mongo clients: root for restore and verify, the reader for everything else.

Ingest and the known answers code connect with `reader_client`, which can
read `JiraReposAnon` and nothing more, so a bug there cannot edit raw data.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr
from pymongo import MongoClient

from orgos.config import Settings

SERVER_SELECTION_TIMEOUT_MS = 5000


def secrets(settings: Settings) -> tuple[SecretStr, ...]:
    """The Mongo passwords a message must never show."""
    return (settings.mongo_password, settings.mongo_reader_password)


def _client(uri: str) -> MongoClient[dict[str, Any]]:
    return MongoClient(uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS, tz_aware=True)


def root_client(settings: Settings) -> MongoClient[dict[str, Any]]:
    """A client logged in as the Mongo root user (restore and verify only)."""
    return _client(settings.mongo_uri)


def reader_client(settings: Settings) -> MongoClient[dict[str, Any]]:
    """A client logged in as the read only raw data user."""
    return _client(settings.mongo_reader_uri)
