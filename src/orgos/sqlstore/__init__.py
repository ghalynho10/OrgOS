"""The SQL store (Postgres): bootstrap, roles, and safe query access."""


class SqlStoreError(Exception):
    """Raised when Postgres cannot be reached or refuses the login."""
