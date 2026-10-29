"""Frozen models for a question, an amendment and an answer, and the TOML reader.

The models check one table at a time: keys, TOML types and allowed values
(spec 0005, AC-7, AC-11, AC-42). Rules across fields or across the set (counts,
the tracer, usable fields) live in `orgos.eval.checker`, which lists every
broken rule together. Scalars are strict, so `hops = "1"` or `tracer = 1` fail
where pydantic would otherwise coerce them.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from orgos.eval import EvalError

QUESTION_COUNT = 20
QUESTION_IDS = tuple(f"Q{n:02d}" for n in range(1, QUESTION_COUNT + 1))

QuestionType = Literal["aggregate", "path_connection", "mixed"]
HopMode = Literal["fixed", "variable"]
Shape = Literal["number", "map", "ranking", "set", "path", "boolean"]
Unit = Literal["seconds", "count", "ratio", "none"]
Reason = Literal["tie_at_cut", "empty_answer", "unresolved_term", "unfilled_field"]
SourceKind = Literal["issue", "project", "person", "time_entry", "issue_link", "parent"]

# Kinds an answer value may name as `kind:id`; `parent` is a source step kind only.
ID_KINDS = ("issue", "project", "person", "time_entry", "issue_link")


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty")
    return value


def _question_id(value: str) -> str:
    if value not in QUESTION_IDS:
        raise ValueError(f"must be one of Q01 to Q{QUESTION_COUNT}")
    return value


def _iso_date(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be an ISO date (YYYY-MM-DD)") from None
    return value


def _typed_id(value: str) -> str:
    kind, sep, key = value.partition(":")
    if not sep or kind not in ID_KINDS or not key:
        raise ValueError(f"must look like <kind>:<id> with kind one of {', '.join(ID_KINDS)}")
    return value


Text = Annotated[StrictStr, AfterValidator(_non_blank)]
QuestionId = Annotated[StrictStr, AfterValidator(_question_id)]
TypedId = Annotated[StrictStr, AfterValidator(_typed_id)]


class Question(BaseModel):
    """One `[[question]]` table of `eval/questions.toml`, as you wrote it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: QuestionId
    text: Text
    type: QuestionType
    hops: Annotated[StrictInt, Field(ge=0, le=2)]
    hop_mode: HopMode
    max_hops: Annotated[StrictInt, Field(ge=2)] | None = None
    shape: Shape
    unit: Unit
    fields: Annotated[tuple[Text, ...], Field(min_length=1)]
    reading: Text
    data: Literal["real"]
    tracer: StrictBool = False


class Amendment(BaseModel):
    """One `[[amendment]]` table of `eval/amendments.toml` (spec 0005, AC-42)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: QuestionId
    reason: Reason
    before: Question
    after: Question
    code_output: StrictStr
    reading_chosen: StrictStr
    date: Annotated[StrictStr, AfterValidator(_iso_date)]


class IntNumber(BaseModel):
    """A count or a sum: `{"int": n}`."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    int_: StrictInt = Field(alias="int")


class Fraction(BaseModel):
    """An average or a ratio, the two integers exactly as computed: `{"num": a, "den": b}`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    num: StrictInt
    den: StrictInt


Number = IntNumber | Fraction
# A source step: each kind it lists, with its sorted bare IDs (AC-32).
Step = Mapping[SourceKind, tuple[StrictStr, ...]]


class _Item(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MapItem(_Item):
    """One entity of a `map` answer with its value."""

    id: TypedId
    value: Number


class TieGroup(_Item):
    """One tie group of a `ranking`: the exact value and every ID that has it, sorted."""

    value: Number
    ids: tuple[TypedId, ...]


class PathItem(_Item):
    """One path of a `path` answer, with its own source steps, one per hop."""

    path: tuple[TypedId, ...]
    sources: tuple[Step, ...]


class _Answer(_Item):
    sources: tuple[Step, ...]


class NumberAnswer(_Answer):
    """A `number` answer."""

    shape: Literal["number"]
    value: Number


class MapAnswer(_Answer):
    """A `map` answer, items sorted by id."""

    shape: Literal["map"]
    value: tuple[MapItem, ...]


class RankingAnswer(_Answer):
    """A `ranking` answer, tie groups in the question's direction."""

    shape: Literal["ranking"]
    value: tuple[TieGroup, ...]


class SetAnswer(_Answer):
    """A `set` answer, IDs sorted."""

    shape: Literal["set"]
    value: tuple[TypedId, ...]


class PathAnswer(_Answer):
    """A `path` answer; each path carries its sources, so the top level `sources` is empty."""

    shape: Literal["path"]
    sources: tuple[Step, ...] = Field(max_length=0)
    value: tuple[PathItem, ...]


class BooleanAnswer(_Answer):
    """A `boolean` answer."""

    shape: Literal["boolean"]
    value: StrictBool


Answer = Annotated[
    NumberAnswer | MapAnswer | RankingAnswer | SetAnswer | PathAnswer | BooleanAnswer,
    Field(discriminator="shape"),
]


class AnswerFile(_Item):
    """`eval/answers.json`: every known answer plus the hashes of the files it was computed from."""

    amendments_sha256: StrictStr | None
    answers: Mapping[QuestionId, Answer]
    questions_sha256: StrictStr


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, turning a missing or malformed file into one `EvalError`."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise EvalError(f"{path} not found") from None
    except OSError as error:
        raise EvalError(f"{path} cannot be read: {error.strerror}") from None
    except tomllib.TOMLDecodeError as error:
        raise EvalError(f"{path} is not valid TOML: {error}") from None
