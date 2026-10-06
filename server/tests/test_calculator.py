"""Calculator tool — normalisation, safety, and result-shape guarantees.

Regression context: a live run of "算一下 1234567 × 7654321" failed because the
model copied the user's typographic ``×`` (U+00D7) into the tool argument and
the whitelist rejected it. The retry succeeded, but it burned an extra LLM
round trip on a metered gateway.
"""

from __future__ import annotations

import asyncio

import pytest

from app.builtins import CalculatorTool


@pytest.fixture()
def calc() -> CalculatorTool:
    return CalculatorTool()


def _run(calc: CalculatorTool, expr: str):
    return asyncio.run(calc.execute(expression=expr))


# ---------------------------------------------------------------------------
# The regression that started this
# ---------------------------------------------------------------------------


def test_typographic_multiplication_sign_is_accepted(calc):
    result = _run(calc, "1234567 × 7654321")
    assert result.success is True
    assert "9449772114007" in result.content


@pytest.mark.parametrize(
    "expr",
    [
        "6 ÷ 3",          # U+00F7 division sign
        "10 − 4",         # U+2212 minus sign
        "10 – 4",         # en dash
        "10 — 4",         # em dash
        "3 · 4",          # middle dot
        "10 ＊ 2",         # full-width asterisk
        "１０ + ５",        # full-width digits
        "（２＋３）＊４",    # full-width parens and operators
    ],
)
def test_typographic_and_fullwidth_operators(calc, expr):
    assert _run(calc, expr).success is True


def test_caret_is_treated_as_power_not_xor(calc):
    # 2 ^ 10 is 8 under Python's bitwise xor — never what a calculator user means.
    result = _run(calc, "2^10")
    assert result.success is True
    assert result.content.endswith("= 1024")


# ---------------------------------------------------------------------------
# Thousands separators used to produce a *silently wrong* answer
# ---------------------------------------------------------------------------


def test_thousands_separators_are_stripped(calc):
    result = _run(calc, "1,234,567 * 2")
    assert result.success is True
    assert result.content.endswith("= 2469134")


def test_comma_argument_separator_is_preserved(calc):
    # round(3.14159, 2) — the comma is an argument separator, not a group.
    result = _run(calc, "round(3.14159, 2)")
    assert result.success is True
    assert result.content.endswith("= 3.14")


def test_tuple_result_is_reported_as_error_not_a_number(calc):
    # A raw tuple must never be formatted as if it were an answer.
    result = _run(calc, "(1, 2)")
    assert result.success is False
    assert "multiple values" in result.content


def test_parenthesised_separated_number_still_works(calc):
    # (1,000,000) -> separators stripped -> a single value, not a tuple.
    result = _run(calc, "(1,000,000)")
    assert result.success is True
    assert result.content.endswith("= 1000000")


# ---------------------------------------------------------------------------
# Safety: the whitelist must still reject everything it used to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd').read()",
        "[x for x in range(3)]",
        "print(1)",
        "os.system('ls')",
        "a = 1",
        "{1: 2}",
        "lambda: 1",
    ],
)
def test_unsafe_expressions_are_rejected(calc, expr):
    result = _run(calc, expr)
    assert result.success is False
    assert "disallowed" in result.content


def test_error_names_the_offending_character(calc):
    result = _run(calc, "1 @ 2")
    assert result.success is False
    assert "@" in result.content


# ---------------------------------------------------------------------------
# Ordinary behaviour is unchanged
# ---------------------------------------------------------------------------


def test_basic_arithmetic(calc):
    assert _run(calc, "(1234 * 5678) + 90").content.endswith("= 7006742")


def test_math_functions(calc):
    assert _run(calc, "sqrt(16)").content.endswith("= 4")
    assert _run(calc, "abs(-3) + ceil(1.2)").content.endswith("= 5")


def test_integral_float_is_shown_without_trailing_zero(calc):
    assert _run(calc, "10 / 2").content.endswith("= 5")


def test_division_by_zero_is_reported(calc):
    result = _run(calc, "1 / 0")
    assert result.success is False
    assert "zero" in result.content.lower()
