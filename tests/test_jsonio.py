"""Tests for helios.jsonio: strict loads and dumps (decision, hel-cwf)."""

from __future__ import annotations

import pytest

from helios import jsonio


@pytest.mark.parametrize(
    "text",
    [
        "1e999",
        "-1e999",
        '{"a": 1e999}',
        '{"a": -1e999}',
        "[1e999]",
        "[-1e999]",
        '{"a": [1, 1e999, 3]}',
        '{"a": {"b": -1e999}}',
    ],
)
def test_loads_rejects_overflowing_number(text: str) -> None:
    with pytest.raises(ValueError):
        jsonio.loads(text)


@pytest.mark.parametrize(
    "text",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": -Infinity}',
        "[NaN]",
        "[Infinity]",
        "[-Infinity]",
    ],
)
def test_loads_rejects_constants(text: str) -> None:
    with pytest.raises(ValueError):
        jsonio.loads(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1e308", 1e308),
        ("-1e308", -1e308),
        ("5e-324", 5e-324),
    ],
)
def test_loads_accepts_extreme_finite_floats(text: str, expected: float) -> None:
    assert jsonio.loads(text) == expected


def test_loads_accepts_integers_of_any_size() -> None:
    digits = "1" + "0" * 400
    assert jsonio.loads(digits) == int(digits)
    assert jsonio.loads("-" + digits) == -int(digits)
    nested = jsonio.loads(f'{{"n": {digits}}}')
    assert nested == {"n": int(digits)}


def test_loads_accepts_bytes() -> None:
    assert jsonio.loads(b'{"a": 1}') == {"a": 1}


def test_dumps_refuses_nan_and_infinity() -> None:
    with pytest.raises(ValueError):
        jsonio.dumps(float("nan"))
    with pytest.raises(ValueError):
        jsonio.dumps(float("inf"))
    with pytest.raises(ValueError):
        jsonio.dumps(float("-inf"))
    with pytest.raises(ValueError):
        jsonio.dumps({"a": [1, float("inf")]})


def test_dumps_refuses_explicit_allow_nan_true() -> None:
    with pytest.raises((TypeError, ValueError)):
        jsonio.dumps({"a": 1}, allow_nan=True)


def test_dumps_accepts_explicit_allow_nan_false() -> None:
    assert jsonio.dumps({"a": 1}, allow_nan=False, sort_keys=True) == '{"a": 1}'


def test_round_trip() -> None:
    obj = {"b": [1, 2, 3], "a": "text", "c": 1e308}
    text = jsonio.dumps(obj, sort_keys=True)
    assert jsonio.loads(text) == obj
