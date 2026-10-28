"""Fast checks that the repo's config files agree with each other and the spec."""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from orgos.config import Settings, load_settings
from orgos.mongostore.receipt import RECEIPT_KEYS
from tests.integration.conftest import MONGO_IMAGE, NEO4J_IMAGE, POSTGRES_IMAGE

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
EXAMPLE_NAMES = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", ENV_EXAMPLE, re.MULTILINE))
SECRET_NAMES = [
    str(field.validation_alias)
    for field in Settings.model_fields.values()
    if "SecretStr" in str(field.annotation)
]


# covers: SC-4 (ports bound to 127.0.0.1 only)
@pytest.mark.parametrize("service", sorted(SERVICES))
def test_every_port_binds_to_localhost_only(service: str) -> None:
    for port in SERVICES[service].get("ports", []):
        assert str(port).startswith("127.0.0.1:"), port


# covers: DW-4, SC-4 (one command brings all three up healthy)
@pytest.mark.parametrize("service", sorted(SERVICES))
def test_every_service_joins_the_all_profile_with_a_healthcheck(service: str) -> None:
    config = SERVICES[service]
    assert "all" in config["profiles"]
    assert config["healthcheck"]["test"]
    assert config["mem_limit"]


def test_compose_runs_the_three_stores() -> None:
    assert set(SERVICES) == {"mongo", "postgres", "neo4j"}


# covers: SC-6 (Neo4j is read only unless a load clears it)
def test_neo4j_defaults_to_read_only() -> None:
    setting = SERVICES["neo4j"]["environment"]["NEO4J_server_databases_read__only"]
    assert setting.endswith("-neo4j}")
    assert EXAMPLE_NAMES["ORGOS_NEO4J_READ_ONLY_DATABASES"] == "neo4j"


def test_integration_images_match_compose() -> None:
    assert SERVICES["postgres"]["image"] == POSTGRES_IMAGE
    assert SERVICES["neo4j"]["image"] == NEO4J_IMAGE
    assert SERVICES["mongo"]["image"] == MONGO_IMAGE


# The spec fixes these names (database `orgos`, roles `orgos_owner` and
# `orgos_reader`), so `.env.example` leaves their overrides out.
FIXED_NAMES = {
    "ORGOS_PG_DATABASE",
    "ORGOS_PG_SUPERUSER",
    "ORGOS_PG_OWNER_USER",
    "ORGOS_PG_READER_USER",
}


def test_env_example_lists_every_setting_but_the_fixed_names() -> None:
    for field in Settings.model_fields.values():
        name = str(field.validation_alias)
        assert name in EXAMPLE_NAMES or name in FIXED_NAMES, name


def test_env_example_lists_every_compose_variable() -> None:
    compose_text = (ROOT / "docker-compose.yml").read_text()
    for name in set(re.findall(r"\$\{([A-Z0-9_]+)", compose_text)):
        assert name in EXAMPLE_NAMES, name


def test_env_example_holds_no_secret_values() -> None:
    assert SECRET_NAMES
    for name in SECRET_NAMES:
        assert EXAMPLE_NAMES[name] == "", name


def test_env_example_loads_once_passwords_are_filled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    filled = {name: "a-password" for name in SECRET_NAMES} | {"NEO4J_AUTH": "neo4j/a-password"}
    lines = [
        f"{name}={filled[name]}" if name in filled and not value else f"{name}={value}"
        for name, value in EXAMPLE_NAMES.items()
    ]
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines) + "\n")
    settings = load_settings(_env_file=env_file)
    assert settings.model is None
    assert settings.query_timeout_s == 30


def test_dbt_env_provides_every_variable_dbt_reads() -> None:
    settings = load_settings(
        _env_file=None,
        POSTGRES_PASSWORD="x",
        ORGOS_PG_OWNER_PASSWORD="x",
        ORGOS_PG_READER_PASSWORD="x",
        MONGO_INITDB_ROOT_PASSWORD="x",
        ORGOS_MONGO_READER_PASSWORD="x",
        NEO4J_AUTH="neo4j/x",
    )
    dbt_text = (ROOT / "dbt/profiles.yml").read_text() + (ROOT / "dbt/dbt_project.yml").read_text()
    for name in set(re.findall(r"env_var\('([A-Z0-9_]+)'", dbt_text)):
        assert name in settings.dbt_env(), name


def _readme_pins() -> dict[str, str]:
    readme = (ROOT / "README.md").read_text()
    table = readme.split("## Pinned versions", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|", table, re.MULTILINE)
    return {name: pinned for name, pinned in rows if name not in ("Component", "---")}


# covers: SC-7 (exact tags pinned in the README)
def test_readme_pins_every_compose_image() -> None:
    pinned = " ".join(_readme_pins().values())
    for service in SERVICES.values():
        assert f"`{service['image']}`" in pinned, service["image"]


# covers: SC-7 (exact versions pinned in the README)
def test_readme_package_versions_match_the_lock() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {package["name"]: package["version"] for package in lock["package"]}
    packages = {
        name.removesuffix(" (driver)"): pinned.split()[0]
        for name, pinned in _readme_pins().items()
        if name != "Python" and not name.endswith(" image")
    }
    assert packages
    for name, version in packages.items():
        assert locked[name] == version, name


README_DATA = (ROOT / "README.md").read_text().split("\n## Data\n", 1)[1].split("\n### ", 1)[0]
MANIFEST = dict(re.findall(r"^\| ([^|]+?) \| (.+?) \|$", README_DATA, re.MULTILINE))
SCRIPT_PINS = {
    name: value.strip("'")
    for name, value in re.findall(
        r"^([A-Z0-9_]+)=(.*)$", (ROOT / "scripts/fetch_data.sh").read_text(), re.MULTILINE
    )
}


# covers: AC-39 (the manifest documents the script's pins, the only copy in code)
@pytest.mark.parametrize(
    ("item", "pin"),
    [
        ("Source", "RECORD"),
        ("Version", "VERSION"),
        ("URL", "URL"),
        ("Local file", "NAME"),
        ("Size", "SIZE"),
        ("md5 of the zip", "MD5"),
    ],
)
def test_readme_manifest_matches_the_fetch_script_pins(item: str, pin: str) -> None:
    assert SCRIPT_PINS[pin] in MANIFEST[item]


# covers: AC-39 (both DOIs, the licenses with where each was read, the receipt)
def test_readme_manifest_names_the_dois_licenses_and_receipt() -> None:
    record = SCRIPT_PINS["RECORD"]
    assert f"`10.5281/zenodo.{record}` (this version)" in MANIFEST["DOI"]
    assert "`10.5281/zenodo.5882881` (all versions)" in MANIFEST["DOI"]
    assert "Data: CC BY 4.0, read from " in MANIFEST["License"]
    assert "Code in the zip: MIT, read from " in MANIFEST["License"]
    receipt = MANIFEST["Receipt"]
    assert f"`data/{SCRIPT_PINS['RECEIPT']}`" in receipt
    for key in RECEIPT_KEYS:
        assert f"`{key}`" in receipt, key


# covers: AC-39 (the attribution citation)
def test_readme_quotes_the_attribution_citation() -> None:
    attribution = README_DATA.split("**Attribution.**", 1)[1]
    quoted = [line for line in attribution.splitlines() if line.startswith("> ")]
    assert len(quoted) == 1
    assert "An Alternative Issue Tracking Dataset of Public Jira Repositories" in quoted[0]
    assert "(MSR) 2022" in quoted[0]


# covers: DW-2 (data/ and .env never enter git)
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("path", ["data/2025-06-23 ThePublicJiraDataset.zip", "data/dlt/x", ".env"])
def test_data_and_env_are_git_ignored(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    assert result.returncode == 0, f"{path} is not ignored"
    tracked = subprocess.run(
        ["git", "ls-files", "data", ".env"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert tracked.stdout == ""
