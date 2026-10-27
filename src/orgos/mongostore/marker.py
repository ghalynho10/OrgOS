"""The completion marker and the live state of `JiraReposAnon`, read and written at the edge.

The marker lives outside the raw database (`orgos_meta.staging_restore`),
is one document written in one operation, and only after a PASS. A later
step that drops a collection must update it in the same step, or verify fails.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from pydantic import SecretStr
from pymongo import MongoClient

from orgos.mongostore import MongoStoreError
from orgos.mongostore.dump import DATABASE

MARKER_DATABASE = "orgos_meta"
MARKER_COLLECTION = "staging_restore"


@dataclass(frozen=True)
class Marker:
    """What a passing restore recorded."""

    completed_at: datetime
    receipt: Mapping[str, str | int]
    dump_server_version: str
    dump_tool_version: str
    server_version: str
    counts: Mapping[str, int]

    def document(self) -> dict[str, object]:
        """The marker as the one document stored under `_id: JiraReposAnon`."""
        return {
            "_id": DATABASE,
            "completed_at": self.completed_at,
            "receipt": dict(self.receipt),
            "dump": {
                "server_version": self.dump_server_version,
                "tool_version": self.dump_tool_version,
            },
            "server_version": self.server_version,
            "counts": dict(self.counts),
        }


def _marker_collection(client: MongoClient[dict[str, Any]]) -> Any:
    return client[MARKER_DATABASE][MARKER_COLLECTION]


def read_marker(client: MongoClient[dict[str, Any]]) -> Marker | None:
    """The marker for `JiraReposAnon`, or `None` when no restore has passed."""
    document = _marker_collection(client).find_one({"_id": DATABASE})
    if document is None:
        return None
    try:
        return Marker(
            completed_at=document["completed_at"],
            receipt=MappingProxyType(dict(document["receipt"])),
            dump_server_version=str(document["dump"]["server_version"]),
            dump_tool_version=str(document["dump"]["tool_version"]),
            server_version=str(document["server_version"]),
            counts=MappingProxyType({str(k): int(v) for k, v in document["counts"].items()}),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise MongoStoreError(
            f"the restore record in {MARKER_DATABASE}.{MARKER_COLLECTION} is malformed ({error!r})"
        ) from None


def write_marker(client: MongoClient[dict[str, Any]], marker: Marker) -> None:
    """Write the marker as one document, replacing any earlier one."""
    _marker_collection(client).replace_one({"_id": DATABASE}, marker.document(), upsert=True)


def delete_marker(client: MongoClient[dict[str, Any]]) -> None:
    """Delete the marker (the first step of `--reset`, before the drop)."""
    _marker_collection(client).delete_one({"_id": DATABASE})


def database_exists(client: MongoClient[dict[str, Any]]) -> bool:
    """Whether Mongo lists `JiraReposAnon` at all."""
    return DATABASE in client.list_database_names()


def live_counts(client: MongoClient[dict[str, Any]]) -> dict[str, int]:
    """`count_documents({})` for every collection in `JiraReposAnon` not named `system.*`."""
    database = client[DATABASE]
    return {
        name: database[name].count_documents({})
        for name in sorted(database.list_collection_names())
        if not name.startswith("system.")
    }


def reclaimable_bytes(client: MongoClient[dict[str, Any]]) -> int:
    """`storageSize` plus `indexSize` of `JiraReposAnon`, or 0 when it does not exist."""
    if not database_exists(client):
        return 0
    stats = client[DATABASE].command("dbStats", scale=1)
    return int(stats["storageSize"]) + int(stats["indexSize"])


def ensure_reader(client: MongoClient[dict[str, Any]], user: str, password: SecretStr) -> None:
    """Create or update the read only user: `read` on `JiraReposAnon`, nothing else."""
    admin = client.admin
    roles = [{"role": "read", "db": DATABASE}]
    exists = admin.command("usersInfo", user)["users"]
    command = "updateUser" if exists else "createUser"
    admin.command(command, user, pwd=password.get_secret_value(), roles=roles)
