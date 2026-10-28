"""The dataset receipt: what `scripts/fetch_data.sh` checked, carried to the restore.

The script owns the pinned facts (URL, md5, size); Python learns them only
from this file, so nothing is declared twice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from orgos.mongostore import MongoStoreError

RECEIPT_NAME = "dataset.receipt"
RECEIPT_KEYS = ("file", "size", "md5", "record", "version", "url", "verified_at")


@dataclass(frozen=True)
class Receipt:
    """The seven `key=value` lines of `dataset.receipt`."""

    file: str
    size: int
    md5: str
    record: str
    version: str
    url: str
    verified_at: str

    def as_dict(self) -> dict[str, str | int]:
        """The receipt's keys and values, for the completion marker."""
        return asdict(self)


def parse_receipt(text: str) -> Receipt:
    """Parse receipt text, raising `MongoStoreError` naming what is malformed.

    Each line splits on its first `=` (the URL holds one); no quoting.
    """
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise MongoStoreError(f"receipt line {number} is not key=value")
        if key not in RECEIPT_KEYS:
            raise MongoStoreError(f"receipt has an unknown key {key!r}")
        if key in values:
            raise MongoStoreError(f"receipt has the key {key!r} twice")
        values[key] = value
    missing = [key for key in RECEIPT_KEYS if key not in values]
    if missing:
        raise MongoStoreError(f"receipt is missing {', '.join(missing)}")
    name = values["file"]
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise MongoStoreError(f"receipt file {name!r} must be a bare file name")
    if not values["size"].isdigit():
        raise MongoStoreError(f"receipt size {values['size']!r} is not a whole number")
    return Receipt(
        file=name,
        size=int(values["size"]),
        md5=values["md5"],
        record=values["record"],
        version=values["version"],
        url=values["url"],
        verified_at=values["verified_at"],
    )
