"""The Mongo staging store: restore the pinned Jira dump and prove it arrived whole.

`orgos mongo restore` streams the archive from the dataset zip into
mongorestore inside the mongo container, checks every collection's count, and
writes a completion marker; `orgos mongo verify` compares the live database
with that marker. Every failure the store can expect surfaces as one
`MongoStoreError`, which the CLI turns into exit code 3.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import SecretStr
from pymongo.errors import PyMongoError


class MongoStoreError(Exception):
    """Raised when the staging store cannot be checked, restored, or verified."""


# Errors from Mongo, Docker, the zip, and the file system that become a
# `MongoStoreError`. A missing `docker` binary is a `FileNotFoundError`, an `OSError`.
WRAPPED_ERRORS: tuple[type[Exception], ...] = (PyMongoError, zipfile.BadZipFile, OSError)


def redact(message: str, secrets: tuple[SecretStr, ...]) -> str:
    """`message` with every non empty secret value replaced by `***`."""
    for secret in secrets:
        value = secret.get_secret_value()
        if value:
            message = message.replace(value, "***")
    return message


@contextmanager
def mongo_errors(action: str, secrets: tuple[SecretStr, ...] = ()) -> Iterator[None]:
    """Turn the expected Mongo, Docker, zip and file errors into a `MongoStoreError`.

    The message names `action` and the reason, with any of `secrets` masked.
    """
    try:
        yield
    except WRAPPED_ERRORS as error:
        reason = redact(f"{type(error).__name__}: {error}", secrets)
        raise MongoStoreError(f"{action} failed: {reason}") from None
