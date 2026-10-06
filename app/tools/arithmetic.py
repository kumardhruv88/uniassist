"""Exact attendance arithmetic. Never compare floats or rounded values against a threshold.

Traps this avoids (both verified):
  * round(95/119*100) == 80, so rounding before comparing wrongly passes an 80% rule.
  * ceil((0.8*40 - 31) / (1 - 0.8)) == 6 in floats; the exact answer is 5 (36/45 = 80%).
"""
from __future__ import annotations

import math
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction


def pct(attended: int, held: int) -> Fraction:
    return Fraction(attended, held) * 100


def as_fraction(value: str | int | float) -> Fraction:
    return Fraction(str(value).strip().rstrip("%"))


def floor_2dp(x: Fraction) -> Decimal:
    """Display only. Rounds DOWN, so a value just below a threshold never shows as the threshold."""
    return (Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def fmt_pct(x: Fraction) -> str:
    return f"{floor_2dp(x)}"


def classes_needed(attended: int, held: int, threshold_pct: Fraction) -> int:
    """Smallest n with (A + n) / (H + n) >= t, assuming every future class is attended."""
    t = threshold_pct / 100
    if pct(attended, held) >= threshold_pct:
        return 0
    if t >= 1:
        raise ValueError("threshold of 100% cannot be reached once a class is missed")
    return math.ceil((t * held - attended) / (1 - t))


def max_absences(attended: int, held: int, future: int, threshold_pct: Fraction) -> int:
    """Largest m with (A + F - m) / (H + F) >= t; 0 if already unreachable."""
    t = threshold_pct / 100
    return max(0, math.floor(attended + future - t * (held + future)))


def compare(value: Fraction, operator: str, threshold: Fraction) -> bool:
    return {">=": value >= threshold, "<=": value <= threshold, ">": value > threshold,
            "<": value < threshold, "=": value == threshold}[operator]
