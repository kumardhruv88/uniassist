import math
from fractions import Fraction

from app.tools.arithmetic import classes_needed, floor_2dp, max_absences, pct


def test_exact_threshold_passes():
    assert pct(30, 40) >= 75 and pct(32, 40) >= 80


def test_one_class_below_fails():
    assert pct(29, 40) < 75 and pct(31, 40) < 80


def test_rounding_trap_never_displays_threshold():
    assert round(95 / 119 * 100) == 80                     # the trap
    assert str(floor_2dp(pct(95, 119))) == "79.83" and pct(95, 119) < 80
    assert str(floor_2dp(pct(47, 59))) == "79.66"


def test_float_shortfall_trap():
    assert math.ceil((0.8 * 40 - 31) / (1 - 0.8)) == 6      # float answer is wrong
    assert classes_needed(31, 40, Fraction(80)) == 5         # exact: 36/45 = 80%
    assert pct(36, 45) == 80


def test_max_absences():
    assert max_absences(32, 40, 10, Fraction(80)) == 2      # (32 + 10 - 2) / 50 = 80%
    assert max_absences(20, 40, 10, Fraction(80)) == 0
