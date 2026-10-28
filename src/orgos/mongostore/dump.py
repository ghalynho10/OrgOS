"""What the pinned dump holds, and a pure reader for its archive header.

The counts are fixed in spec 0003 before any restore runs, so the PASS rule
cannot be bent after seeing the numbers. Changing one is a spec amendment
with a stated reason, never a silent edit here.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import bson
from bson.errors import BSONError

from orgos.mongostore import MongoStoreError

DATABASE = "JiraReposAnon"
ARCHIVE_MEMBER = "ThePublicJiraDataset/3. DataDump/mongodump-JiraReposAnon.archive"
COMPOSE_PROJECT = "orgos"
COMPOSE_SERVICE = "mongo"

# Source: the zip's `2. OverviewAnalysis/OverviewAnalysis.ipynb`, cells 9 and 10
# (spec 0003, "The pinned expected counts"). They sum to 2,686,282.
EXPECTED_COUNTS: Mapping[str, int] = MappingProxyType(
    {
        "Apache": 1_014_926,
        "Hyperledger": 28_146,
        "IntelDAOS": 9_474,
        "JFrog": 15_535,
        "Jira": 274_545,
        "JiraEcosystem": 41_866,
        "MariaDB": 31_229,
        "Mindville": 2_134,
        "MongoDB": 137_172,
        "Qt": 148_579,
        "RedHat": 353_000,
        "Sakai": 50_550,
        "SecondLife": 1_867,
        "Sonatype": 87_284,
        "Spring": 69_156,
        "Mojang": 420_819,
    }
)

ARCHIVE_MAGIC = 0x8199E26D
TERMINATOR = -1
# The most decompressed bytes read for the header (spec 0003).
HEADER_LIMIT = 4 * 1024 * 1024


class TruncatedHeader(MongoStoreError):
    """Raised when the bytes end before the header does (read more, or give up)."""


@dataclass(frozen=True)
class HeaderEntry:
    """One collection the archive lists."""

    db: str
    collection: str
    type: str


@dataclass(frozen=True)
class DumpHeader:
    """The archive's prelude: the versions it was dumped with and what it holds."""

    server_version: str
    tool_version: str
    entries: tuple[HeaderEntry, ...]

    @property
    def collections(self) -> frozenset[str]:
        return frozenset(entry.collection for entry in self.entries)

    @property
    def databases(self) -> frozenset[str]:
        return frozenset(entry.db for entry in self.entries)


def _int32(data: bytes, pos: int) -> int:
    if pos + 4 > len(data):
        raise TruncatedHeader(
            f"archive header is cut off after {len(data)} bytes (no terminator found)"
        )
    value: int = struct.unpack_from("<i", data, pos)[0]
    return value


def _document(data: bytes, pos: int) -> tuple[dict[str, object], int]:
    size = _int32(data, pos)
    if size < 5:
        raise MongoStoreError(f"archive header has a malformed document at byte {pos}")
    if pos + size > len(data):
        raise TruncatedHeader(f"archive header is cut off inside the document at byte {pos}")
    try:
        document: dict[str, object] = bson.decode(data[pos : pos + size])
    except BSONError as error:
        raise MongoStoreError(f"archive header has a malformed document at byte {pos}") from error
    return document, pos + size


def parse_header(data: bytes) -> DumpHeader:
    """Parse the start of a decompressed mongodump archive.

    Layout: an int32 little endian magic, one prelude document with the
    versions, one document per collection, then an int32 `-1`.
    """
    if _int32(data, 0) & 0xFFFFFFFF != ARCHIVE_MAGIC:
        raise MongoStoreError("not a mongodump archive (the magic number does not match)")
    prelude, pos = _document(data, 4)
    entries = []
    while _int32(data, pos) != TERMINATOR:
        entry, pos = _document(data, pos)
        entries.append(
            HeaderEntry(
                db=str(entry.get("db", "")),
                collection=str(entry.get("collection", "")),
                type=str(entry.get("type", "")),
            )
        )
    return DumpHeader(
        server_version=str(prelude.get("server_version", "")),
        tool_version=str(prelude.get("tool_version", "")),
        entries=tuple(entries),
    )
