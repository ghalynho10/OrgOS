"""The comparison rule: how a store's answer and sources are judged against a known answer.

Fixed here as code before any known answer or store result exists (spec 0005,
AC-37 to AC-40, AC-44), so "correct" cannot be redefined after results. Feature
12's runner passes each store's raw values through `store_answer`, the one
converter both stores share, then calls `compare`. Pure: no store, no file.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

from orgos.eval import EvalError

# A known fraction matches a store value v when |v - num/den| <= TOLERANCE * |num/den| (AC-38).
TOLERANCE = Fraction(1, 10**9)

JsonNumber = dict[str, int]
StoreNumber = JsonNumber | bool | None


@dataclass(frozen=True)
class Verdict:
    """The three columns of one question and store: answer match, source match, and both."""

    answer_match: bool
    source_match: bool
    correct: bool


# The converter (AC-44)


def to_number(raw: object) -> StoreNumber:
    """A store's raw number in the answer form, keeping its exact value.

    An `int` becomes `{"int": n}`; a `float` (a Neo4j float, at its exact binary
    value) or a `Decimal` (Postgres `numeric`) becomes `{"int": n}` when whole,
    else `{"num": a, "den": b}` in lowest terms. A `bool` is never a number and
    is kept as a `bool`, so it matches no number; `None` stays `None`.
    """
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return {"int": raw}
    if isinstance(raw, float | Decimal):
        if isinstance(raw, float) and not math.isfinite(raw):
            raise EvalError(f"a store value {raw!r} is not a finite number")
        if isinstance(raw, Decimal) and not raw.is_finite():
            raise EvalError(f"a store value {raw!r} is not a finite number")
        exact = Fraction(raw)
        if exact.denominator == 1:
            return {"int": exact.numerator}
        return {"num": exact.numerator, "den": exact.denominator}
    raise EvalError(f"a store value of type {type(raw).__name__} is not a number")


def _ids(raw: Iterable[object]) -> list[str]:
    return sorted(str(item) for item in raw)


def to_steps(raw: Iterable[Mapping[str, Iterable[object]]]) -> list[dict[str, list[str]]]:
    """Source steps in the answer form: each kind mapped to its sorted IDs."""
    return [{str(kind): _ids(ids) for kind, ids in step.items()} for step in raw]


def store_answer(
    shape: str, value: Any, sources: Iterable[Mapping[str, Iterable[object]]] = ()
) -> dict[str, Any]:
    """A store's raw answer in the same JSON form as `answers.json`, for either store.

    `value` by shape: `number` a raw number; `map` a mapping or pairs of id and
    raw number; `ranking` pairs of id and raw number in the store's order (each
    becomes its own group, so the store's grouping is ignored); `set` IDs;
    `path` pairs of the path's IDs and its source steps; `boolean` the raw value.
    """
    if shape == "number":
        converted: Any = to_number(value)
    elif shape == "map":
        pairs = value.items() if isinstance(value, Mapping) else value
        converted = sorted(
            ({"id": str(key), "value": to_number(raw)} for key, raw in pairs),
            key=lambda item: item["id"],
        )
    elif shape == "ranking":
        converted = [{"value": to_number(raw), "ids": [str(key)]} for key, raw in value]
    elif shape == "set":
        converted = _ids(value)
    elif shape == "path":
        converted = sorted(
            ({"path": [str(i) for i in path], "sources": to_steps(steps)} for path, steps in value),
            key=lambda item: " ".join(item["path"]),
        )
    elif shape == "boolean":
        converted = value
    else:
        raise EvalError(f"unknown answer shape {shape}")
    return {"shape": shape, "sources": to_steps(sources), "value": converted}


# The comparison (AC-37 to AC-40)


def _exact(number: object) -> Fraction | None:
    # The exact value of a number in the answer form; None for anything else.
    if not isinstance(number, Mapping):
        return None
    if set(number) == {"int"} and type(number["int"]) is int:
        return Fraction(number["int"])
    if set(number) == {"num", "den"} and type(number["num"]) is type(number["den"]) is int:
        return Fraction(number["num"], number["den"]) if number["den"] != 0 else None
    return None


def _empty(value: object) -> bool:
    return value is None or (isinstance(value, list | tuple | dict) and len(value) == 0)


def number_match(known: Mapping[str, int], store: object) -> bool:
    """AC-38: an int matches its exact value; a fraction within 1e-9 relative; 0/0 only empty."""
    if "int" in known:
        return _exact(store) == known["int"]
    if known["den"] == 0:
        return _empty(store)
    value = _exact(store)
    if value is None:
        return False
    target = Fraction(known["num"], known["den"])
    return abs(value - target) <= TOLERANCE * abs(target)


def _items(value: object) -> list[Mapping[str, Any]] | None:
    if not isinstance(value, list | tuple) or not all(isinstance(i, Mapping) for i in value):
        return None
    return list(value)


def _map_match(known: Sequence[Mapping[str, Any]], store: object) -> bool:
    items = _items(store)
    if items is None or any("id" not in item for item in items):
        return False
    store_values = {item["id"]: item.get("value") for item in items}
    if len(store_values) != len(items):
        return False
    known_values = {item["id"]: item["value"] for item in known}
    return set(store_values) == set(known_values) and all(
        number_match(value, store_values[key]) for key, value in known_values.items()
    )


def _ranking_match(known: Sequence[Mapping[str, Any]], store: object) -> bool:
    groups = _items(store)
    if groups is None or any(not isinstance(g.get("ids"), list | tuple) for g in groups):
        return False
    known_group = {key: index for index, group in enumerate(known) for key in group["ids"]}
    placed = [
        (position, key, g.get("value")) for position, g in enumerate(groups) for key in g["ids"]
    ]
    keys = [key for _, key, _ in placed]
    if len(set(keys)) != len(keys) or set(keys) != set(known_group):
        return False
    if not all(number_match(known[known_group[key]]["value"], value) for _, key, value in placed):
        return False
    # No item of a later known group may come before an item of an earlier one;
    # items the store places together count as neither before nor after.
    latest_before = -1
    for position in range(len(groups)):
        here = [known_group[key] for at, key, _ in placed if at == position]
        if here and min(here) < latest_before:
            return False
        latest_before = max([latest_before, *here])
    return True


def _path_key(item: Mapping[str, Any]) -> tuple[str, ...] | None:
    path = item.get("path")
    return tuple(str(i) for i in path) if isinstance(path, list | tuple) else None


def _path_match(known: Sequence[Mapping[str, Any]], store: object) -> bool:
    items = _items(store)
    if items is None:
        return False
    return Counter(_path_key(item) for item in items) == Counter(_path_key(i) for i in known)


def answer_match(known: Mapping[str, Any], store: Mapping[str, Any]) -> bool:
    """AC-38, AC-39: the store's value matches the known value under the shape's rule."""
    shape, expected, value = known["shape"], known["value"], store.get("value")
    if store.get("shape") != shape:
        return False
    if shape == "number":
        return number_match(expected, value)
    if shape == "boolean":
        return isinstance(value, bool) and value == expected
    if shape == "set":
        return isinstance(value, list | tuple) and set(value) == set(expected)
    if shape == "map":
        return _map_match(expected, value)
    if shape == "ranking":
        return _ranking_match(expected, value)
    if shape == "path":
        return _path_match(expected, value)
    raise EvalError(f"unknown answer shape {shape}")


def _steps(raw: object) -> tuple[tuple[tuple[str, frozenset[str]], ...], ...] | None:
    # Steps as hashable kind to ID set maps, step order kept.
    if not isinstance(raw, list | tuple):
        return None
    steps = []
    for step in raw:
        if not isinstance(step, Mapping):
            return None
        if not all(isinstance(ids, list | tuple) for ids in step.values()):
            return None
        steps.append(tuple(sorted((str(k), frozenset(map(str, ids))) for k, ids in step.items())))
    return tuple(steps)


def source_match(known: Mapping[str, Any], store: Mapping[str, Any]) -> bool:
    """AC-40: the same number of steps, each step's kind to ID set map equal.

    For a `path` answer, each path's own steps must match too.
    """
    if _steps(store.get("sources")) != _steps(known["sources"]):
        return False
    if known["shape"] != "path":
        return True
    items = _items(store.get("value"))
    if items is None:
        return False
    found = Counter((_path_key(item), _steps(item.get("sources"))) for item in items)
    expected = Counter((_path_key(item), _steps(item["sources"])) for item in known["value"])
    return found == expected


def compare(known: Mapping[str, Any], store: Mapping[str, Any]) -> Verdict:
    """Judge a store's answer (from `store_answer`) against a known answer from `answers.json`."""
    answer = answer_match(known, store)
    sources = source_match(known, store)
    return Verdict(answer_match=answer, source_match=sources, correct=answer and sources)
