"""Annex A test table (docs/TECHNICAL_DESIGN.md §4). Each test has an explicit fixture and as_of."""
from datetime import date as D

from app.policy.precedence import (Candidate, Ref, RegisterDoc, StudentScope, batch_matches, programme_matches,
                                   resolve)

ST = StudentScope("B.Tech CSE", 2025)
REG_DOC = RegisterDoc("ACAD-REG-2024", 1, D(2024, 7, 1), scope_programmes="B.Tech")
CIRC_DOC = RegisterDoc("ACAD-2026-08", 2, D(2026, 8, 1), supersedes=Ref.parse_many("ACAD-REG-2024#7.2"))
FAQ_DOC = RegisterDoc("FAQ", 4, D(2026, 9, 15))
REG = Candidate("ATT-MIN-01", "ACAD-REG-2024", "7.2", 1, D(2024, 7, 1), scope_programmes="B.Tech", value="75")
CIRC = Candidate("ATT-CIRC", "ACAD-2026-08", "1", 2, D(2026, 8, 1), value="80")
FAQ = Candidate("ATT-FAQ", "FAQ", "3", 4, D(2026, 9, 15), value="65")
REGISTER = [REG_DOC, CIRC_DOC, FAQ_DOC]


def test_t1_guide_worked_example():
    r = resolve([REG, CIRC, FAQ], ST, D(2026, 10, 6), REGISTER)
    assert r.winner.value == "80"
    assert [(c.key, by) for c, by in r.superseded] == [("ATT-MIN-01", "ACAD-2026-08")]
    assert [(c.key, s) for c, s in r.losers] == [("ATT-FAQ", "step3_authority")]


def test_t2_before_the_circular_is_in_force():
    r = resolve([REG, CIRC, FAQ], ST, D(2026, 7, 15), REGISTER)
    assert r.winner.value == "75"
    assert [c.key for c in r.upcoming] == ["ATT-CIRC"]          # the FAQ would never win, so it is not "upcoming"


def test_t3_level3_supersession_claim_is_ignored():
    notice_doc = RegisterDoc("DEPT", 3, D(2026, 9, 1), supersedes=Ref.parse_many("ACAD-REG-2024#7.2"))
    notice = Candidate("DEPT-70", "DEPT", "1", 3, D(2026, 9, 1), value="70")
    r = resolve([REG, notice], ST, D(2026, 10, 6), [REG_DOC, notice_doc])
    assert r.winner.value == "75" and not r.superseded
    assert r.losers[0][1] == "step3_authority"


def test_t4_recency_at_same_authority():
    a = Candidate("A", "CIRC-A", "1", 2, D(2026, 1, 1), value="78")
    b = Candidate("B", "CIRC-B", "1", 2, D(2026, 5, 1), value="82")
    r = resolve([a, b], ST, D(2026, 10, 6), [RegisterDoc("CIRC-A", 2, D(2026, 1, 1)), RegisterDoc("CIRC-B", 2, D(2026, 5, 1))])
    assert r.winner.key == "B" and r.losers[0] == (a, "step4_recency")


def test_t5_unresolved_tie():
    a = Candidate("A", "CIRC-A", "1", 2, D(2026, 5, 1), value="80")
    b = Candidate("B", "CIRC-B", "1", 2, D(2026, 5, 1), value="85")
    r = resolve([a, b], ST, D(2026, 10, 6), [])
    assert r.unresolved


def test_t6_level5_is_informational_only():
    post = Candidate("POST", "SC-POST", None, 5, D(2026, 9, 20), value="0")
    r = resolve([REG, post], ST, D(2026, 10, 6), [REG_DOC])
    assert r.winner.key == "ATT-MIN-01" and [c.key for c in r.informational] == ["POST"]


def test_t7_out_of_scope_batch():
    c25 = Candidate("C25", "CIRC-25", "1", 2, D(2026, 8, 1), scope_batches="2026+", value="80")
    doc = RegisterDoc("CIRC-25", 2, D(2026, 8, 1), scope_batches="2026+", supersedes=Ref.parse_many("ACAD-REG-2024#7.2"))
    r = resolve([REG, c25], ST, D(2026, 10, 6), [REG_DOC, doc])
    assert r.winner.value == "75" and not r.superseded


def test_t8_expired_superseder_lapses():
    doc = RegisterDoc("CIRC-EXP", 2, D(2026, 1, 1), D(2026, 6, 30), supersedes=Ref.parse_many("ACAD-REG-2024#7.2"))
    exp = Candidate("EXP", "CIRC-EXP", "1", 2, D(2026, 1, 1), D(2026, 6, 30), value="85")
    r = resolve([REG, exp], ST, D(2026, 10, 6), [REG_DOC, doc])
    assert r.winner.value == "75" and not r.superseded


def test_t9_clause_coverage():
    ref = Ref.parse_many("ACAD-REG-2024#7.2")[0]
    assert [ref.covers("ACAD-REG-2024", s) for s in ["7.2", "7.2.1", "7.2(a)", "7.3", "7.20"]] == [True, True, True, False, False]
    assert Ref.parse_many("A;B#7.2, C | D#1") == (Ref("A"), Ref("B", "7.2"), Ref("C"), Ref("D", "1"))


def test_t11_programme_and_batch_scope():
    assert programme_matches("B.Tech", "B.Tech CSE") and not programme_matches("B.Tech", "B.Technology")
    assert programme_matches("B.Tech CSE, M.Tech", "M.Tech") and programme_matches("ALL", "anything")
    assert batch_matches("2023+", 2025) and not batch_matches("2023+", 2022)
    assert batch_matches("2022-2024", 2023) and batch_matches("2023;2025", 2025) and not batch_matches("2023;2025", 2024)


def test_t12_superseded_rule_without_replacement_rule_is_unavailable():
    new_doc = RegisterDoc("ACAD-2026-11", 2, D(2026, 10, 1), supersedes=Ref.parse_many("ACAD-2026-08"))
    r = resolve([REG, CIRC], ST, D(2026, 10, 6), REGISTER + [new_doc])
    assert r.winner is None                                     # no stale 80%, and the 75% does not revive
    assert {c.key for c, _ in r.superseded} == {"ATT-MIN-01", "ATT-CIRC"}


def test_t13_supersession_read_from_register_even_if_circular_not_retrieved():
    r = resolve([REG], ST, D(2026, 10, 6), REGISTER)
    assert r.winner is None and r.superseded[0][1] == "ACAD-2026-08"
