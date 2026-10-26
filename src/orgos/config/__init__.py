"""Typed settings for every store connection and agent limit.

One `Settings` class reads the environment and the shared `.env` file that
Docker Compose also reads. The `.env` holds parts (host, port, user,
password); connection strings are built here and nowhere else, so a password
is never written twice.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(".env")


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    # Postgres (SQL store)
    pg_host: str = Field("127.0.0.1", validation_alias="ORGOS_PG_HOST")
    pg_port: int = Field(5432, validation_alias="ORGOS_PG_PORT")
    pg_database: str = Field("orgos", validation_alias="ORGOS_PG_DATABASE")
    pg_superuser: str = Field("postgres", validation_alias="ORGOS_PG_SUPERUSER")
    pg_superuser_password: SecretStr = Field(validation_alias="POSTGRES_PASSWORD")
    pg_owner_user: str = Field("orgos_owner", validation_alias="ORGOS_PG_OWNER_USER")
    pg_owner_password: SecretStr = Field(validation_alias="ORGOS_PG_OWNER_PASSWORD")
    pg_reader_user: str = Field("orgos_reader", validation_alias="ORGOS_PG_READER_USER")
    pg_reader_password: SecretStr = Field(validation_alias="ORGOS_PG_READER_PASSWORD")

    # MongoDB (staging store)
    mongo_host: str = Field("127.0.0.1", validation_alias="ORGOS_MONGO_HOST")
    mongo_port: int = Field(27017, validation_alias="ORGOS_MONGO_PORT")
    mongo_user: str = Field("orgos", validation_alias="MONGO_INITDB_ROOT_USERNAME")
    mongo_password: SecretStr = Field(validation_alias="MONGO_INITDB_ROOT_PASSWORD")

    # Neo4j (graph store)
    neo4j_host: str = Field("127.0.0.1", validation_alias="ORGOS_NEO4J_HOST")
    neo4j_bolt_port: int = Field(7687, validation_alias="ORGOS_NEO4J_BOLT_PORT")
    neo4j_auth: SecretStr = Field(validation_alias="NEO4J_AUTH")

    # Query agent. Values left as None are set by the query agent spec.
    model: str | None = Field(None, validation_alias="ORGOS_MODEL")
    agent_request_limit: int | None = Field(None, validation_alias="ORGOS_AGENT_REQUEST_LIMIT")
    agent_token_limit: int | None = Field(None, validation_alias="ORGOS_AGENT_TOKEN_LIMIT")
    agent_tool_call_limit: int | None = Field(None, validation_alias="ORGOS_AGENT_TOOL_CALL_LIMIT")
    agent_max_rows: int = Field(200, gt=0, validation_alias="ORGOS_AGENT_MAX_ROWS")
    agent_temperature: float | None = Field(None, validation_alias="ORGOS_AGENT_TEMPERATURE")
    agent_max_tokens: int | None = Field(None, validation_alias="ORGOS_AGENT_MAX_TOKENS")

    # The single source for the query timeout in both stores.
    query_timeout_s: int = Field(30, gt=0, validation_alias="ORGOS_QUERY_TIMEOUT_S")

    # Provider keys
    anthropic_api_key: SecretStr | None = Field(None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")

    @field_validator("neo4j_auth")
    @classmethod
    def _neo4j_auth_shape(cls, value: SecretStr) -> SecretStr:
        user, sep, password = value.get_secret_value().partition("/")
        if not sep or not user or not password:
            raise ValueError("must look like neo4j/<password>")
        return value

    @property
    def neo4j_user(self) -> str:
        return self.neo4j_auth.get_secret_value().partition("/")[0]

    @property
    def neo4j_password(self) -> SecretStr:
        return SecretStr(self.neo4j_auth.get_secret_value().partition("/")[2])

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_bolt_port}"

    @property
    def mongo_uri(self) -> str:
        password = quote_plus(self.mongo_password.get_secret_value())
        return (
            f"mongodb://{quote_plus(self.mongo_user)}:{password}"
            f"@{self.mongo_host}:{self.mongo_port}/?authSource=admin"
        )

    def pg_conninfo(self, role: str, *, dbname: str | None = None) -> str:
        """Connection string for `superuser`, `owner`, or `reader`."""
        users = {
            "superuser": (self.pg_superuser, self.pg_superuser_password),
            "owner": (self.pg_owner_user, self.pg_owner_password),
            "reader": (self.pg_reader_user, self.pg_reader_password),
        }
        user, password = users[role]
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname=dbname or self.pg_database,
            user=user,
            password=password.get_secret_value(),
        )

    def dbt_env(self) -> dict[str, str]:
        """Environment variables `dbt/profiles.yml` reads."""
        return {
            "ORGOS_PG_HOST": self.pg_host,
            "ORGOS_PG_PORT": str(self.pg_port),
            "ORGOS_PG_DATABASE": self.pg_database,
            "ORGOS_PG_OWNER_USER": self.pg_owner_user,
            "ORGOS_PG_OWNER_PASSWORD": self.pg_owner_password.get_secret_value(),
            "ORGOS_PG_READER_USER": self.pg_reader_user,
        }


def load_settings(**overrides: object) -> Settings:
    """Load settings, turning validation errors into one clear message."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            name = str(error["loc"][0]) if error["loc"] else "settings"
            reason = "is not set" if error["type"] == "missing" else error["msg"]
            problems.append(f"  {name} {reason}")
        raise ConfigError(
            "Invalid orgOS configuration (check your .env, see .env.example):\n"
            + "\n".join(problems)
        ) from None
