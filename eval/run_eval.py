"""Black-box evaluation through the running API (guide §7).

    uv run python eval/run_eval.py --label B --api http://localhost:8000              # golden set (eval/golden.yaml)
    uv run python eval/run_eval.py --label B --cold                                   # empty the answer caches first
    uv run python eval/run_eval.py --label B --dataset eval/dataset.yaml              # the original 37 items
    uv run python eval/run_eval.py --label A --api http://localhost:8101 --calibration   # tau-calibration subset only
    uv run python eval/report.py                                                      # builds eval/REPORT.md

Each item is one POST /ask (X-Student-Id, X-Session-Id for multi-turn items, as_of_date) followed by
GET /audit/{trace_id}. Writes eval/runs/<label>/<stamp>.jsonl (one row per item) and <stamp>.summary.json.

Method: exact match for numbers, dates and verdicts (numbers compared as values, dates in any written form),
regexes for wording that may vary, exact tool-output match, citation accuracy = cited doc#section among the
expected sources, retrieval hit@k = an expected source among the top-k evidence recorded in the audit,
abstention = not_found predicted iff expected. Latency, LLM calls and tokens come from the audit record.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from evallib import (ANSWERED, DEFAULT_AS_OF, HERE, ROOT, Text, http, mean, pct, quantile, source_matches, subset_mismatch,
                     wait_healthy)

INHERIT_SKIP = {"id", "bucket", "tags", "difficulty", "reference", "expected_meta", "expected_guardrail", "repeat_of",
                "session", "turn"}


# ----------------------------------------------------------------------------- setup
def ensure_demo_circular(api: str, token: str | None) -> None:
    docs = {d["doc_id"] for d in http(api, "/sources").body["documents"]}
    if "ACAD-2026-08" in docs:
        return
    meta = json.loads((ROOT / "data/corpus/demo/ACAD-2026-08.meta.json").read_text())
    pdf = (ROOT / "data/corpus/demo/ACAD-2026-08.pdf").read_bytes()
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n{json.dumps(meta)}\r\n--{b}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"ACAD-2026-08.pdf\"\r\nContent-Type: application/pdf\r\n\r\n"
            ).encode() + pdf + f"\r\n--{b}--\r\n".encode()
    r = http(api, "/ingest", raw=body, ctype=f"multipart/form-data; boundary={b}",
             headers={"X-Admin-Token": token} if token else None)
    print(f"setup: ingested demo circular (HTTP {r.status}: {(r.body or {}).get('status')}, rules {(r.body or {}).get('rules_added')})")


def bump_data_version(api: str, token: str | None) -> None:
    """Re-load the curated rules (idempotent upsert). Any rules load bumps the API's data version, which is part of
    every cache key, so the run starts with cold answer and LLM caches and measures the uncached path."""
    with open(ROOT / "data/corpus/rules_seed.csv", newline="") as f:
        rules = list(csv.DictReader(f))
    r = http(api, "/admin/rules/load", {"rules": rules}, headers={"X-Admin-Token": token} if token else None)
    print(f"setup: --cold re-loaded {len(rules)} curated rules (HTTP {r.status}) to invalidate caches")


def indexed_docs(api: str) -> set[str]:
    return {d["doc_id"] for d in http(api, "/sources").body["documents"] if (d.get("chunks_indexed") or 0) > 0}


# ----------------------------------------------------------------------------- dataset
def load_items(path: Path) -> list[dict]:
    items = yaml.safe_load(path.read_text())
    ids = [i["id"] for i in items]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"duplicate ids in {path}: {sorted(dup)}")
    by_id = {i["id"]: i for i in items}
    for it in items:
        if it.get("repeat_of") and it["repeat_of"] not in by_id:
            raise SystemExit(f"{it['id']}: repeat_of {it['repeat_of']} is not in the dataset")
    return items


def effective(it: dict, by_id: dict) -> dict:
    """A repeat re-asks its original verbatim (question, student, as_of) and inherits its expectations."""
    if not it.get("repeat_of"):
        return it
    orig = by_id[it["repeat_of"]]
    eff = {k: v for k, v in orig.items() if k not in INHERIT_SKIP}
    eff.update({k: v for k, v in it.items() if k not in ("question", "student_id", "as_of_date")})
    for k in ("question", "student_id", "as_of_date"):
        if orig.get(k) is not None:
            eff[k] = orig[k]
    return eff


def expected_types(it: dict) -> list[str]:
    return list(it.get("expected_answer_type_in") or ([it["expected_answer_type"]] if it.get("expected_answer_type") else []))


def calibration_item(it: dict) -> bool:
    t = set(expected_types(it))
    return (t == {"not_found"} or t == {"retrieved_fact"}) and not it.get("session") and not it.get("repeat_of")


def plan_order(items: list[dict], selected: set[str]) -> list[tuple[dict, bool]]:
    """Dataset order, except: all turns of a session run back to back in turn order, and a repeat runs right after
    its original. Unselected prerequisites (earlier turns, the original of a repeat) run unscored as warm-ups."""
    by_id = {i["id"]: i for i in items}
    sessions: dict[str, list[dict]] = defaultdict(list)
    repeats: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        if it.get("session"):
            sessions[it["session"]].append(it)
        if it.get("repeat_of"):
            repeats[it["repeat_of"]].append(it)
    for turns in sessions.values():
        turns.sort(key=lambda i: i.get("turn", 1))
    order: list[tuple[dict, bool]] = []
    seen: set[str] = set()

    def push(it: dict, scored: bool) -> None:
        if it["id"] in seen:
            return
        seen.add(it["id"])
        order.append((it, scored))
        for rp in repeats.get(it["id"], []):
            if rp["id"] in selected and rp["id"] not in seen:
                seen.add(rp["id"])
                order.append((rp, True))

    for it in items:
        if it["id"] not in selected or it["id"] in seen:
            continue
        if it.get("repeat_of"):
            push(by_id[it["repeat_of"]], it["repeat_of"] in selected)
        elif it.get("session"):
            for t in sessions[it["session"]]:
                push(t, t["id"] in selected)
        else:
            push(it, True)
    return order


# ----------------------------------------------------------------------------- scoring
def max_score(rec: dict) -> float | None:
    r = rec.get("retrieval") or {}
    if r.get("max_score") is not None:
        return float(r["max_score"])
    scores = [s.get("score") for s in rec.get("sources_retrieved") or [] if s.get("score") is not None]
    return float(max(scores)) if scores else None


def ranked_sources(rec: dict) -> list[str]:
    """doc#section of the audit's sources_retrieved in rank order, without clauses dropped as superseded."""
    return [f"{s['doc_id']}#{s.get('section')}" for s in rec.get("sources_retrieved") or [] if s.get("label") != "SUPERSEDED"]


def retrieval_checks(exp_src: list[str], keys: list[str], k: int) -> dict:
    if not exp_src or not keys:
        return {"retrieval_hit": None, "retrieval_hit_any": None, "retrieval_doc_hit": None, "retrieval_rank": None}
    exp_docs = {e.split("#", 1)[0] for e in exp_src}
    rank = next((i for i, key in enumerate(keys, 1) if source_matches(key, exp_src)), None)
    return {"retrieval_hit": rank is not None and rank <= k, "retrieval_hit_any": rank is not None,
            "retrieval_doc_hit": any(key.split("#", 1)[0] in exp_docs for key in keys[:k]), "retrieval_rank": rank}


def score(it: dict, resp: dict | None, rec: dict, top_k: int, http_status: int | None,
          retrieved: list[str] | None = None) -> dict:
    reasons: list[str] = []
    types = expected_types(it)
    answer_type = resp["answer_type"] if resp else "error"
    type_ok = answer_type in types if types else True
    if not type_ok:
        reasons.append(f"answer_type {answer_type}" + (f" (HTTP {http_status})" if answer_type == "error" else "")
                       + f", expected {'/'.join(types)}")
    meta = (resp or {}).get("meta") or {}
    text = Text((resp or {}).get("answer", ""), (resp or {}).get("explanation", ""))
    missing = [str(f) for f in it.get("expected_facts", []) if not text.has(f)]
    missing += [f"/{rx}/" for rx in it.get("expected_regex", []) if not text.matches(rx)]
    facts_ok = not missing
    if missing and resp:
        reasons.append("missing " + ", ".join(missing))
    leaked = [str(f) for f in it.get("forbidden_facts", []) if resp and text.has(f)]
    forbidden_ok = not leaked
    if leaked:
        reasons.append("forbidden " + ", ".join(leaked))

    tool_ok, tool_detail = None, None
    exp_tools = list(it.get("expected_tools") or []) + ([it["expected_tool"]] if it.get("expected_tool") else [])
    if exp_tools:
        tools = (resp or {}).get("tools_invoked") or []
        problems = []
        for et in exp_tools:
            calls = [t for t in tools if t.get("tool") == et["name"]]

            def diff(t: dict) -> list[str]:
                return (subset_mismatch(et.get("input", {}), t.get("input") or {})
                        + subset_mismatch(et.get("output", {}), t.get("output") or {}))
            if not calls:
                problems.append(f"{et['name']} not called (called: {', '.join(t.get('tool') for t in tools) or 'none'})")
            elif not any(not diff(t) for t in calls):
                problems.append(f"{et['name']}: " + "; ".join(diff(calls[0])))
        tool_ok = not problems
        if problems and resp:
            tool_detail = " | ".join(problems)
            reasons.append(tool_detail)

    cites = [f"{c['doc_id']}#{c.get('section')}" for c in (resp or {}).get("citations") or []]
    exp_src = it.get("expected_sources", [])
    exp_docs = {e.split("#", 1)[0] for e in exp_src}
    cite_hits = sum(1 for c in cites if source_matches(c, exp_src)) if exp_src else None
    cite_doc_hits = sum(1 for c in cites if c.split("#", 1)[0] in exp_docs) if exp_src else None
    bad_src = [c for c in cites if c.split("#", 1)[0] in it.get("forbidden_sources", [])]
    forbidden_src_ok = not bad_src
    if bad_src:
        reasons.append("cited forbidden " + ", ".join(bad_src))

    upcoming = [u["doc_id"] for u in (resp or {}).get("upcoming_changes") or []]
    missing_up = [d for d in it.get("expected_upcoming", []) if d not in upcoming]
    if missing_up and resp:
        reasons.append("upcoming missing " + ", ".join(missing_up))
    conflict_ok = True
    if ec := it.get("expected_conflict"):
        conflict_ok = any(c.get("resolved_by") == ec for c in (resp or {}).get("conflicts_detected") or [])
        if not conflict_ok and resp:
            reasons.append(f"no conflict resolved by {ec}")

    meta_ok = None
    if em := it.get("expected_meta"):
        mism = subset_mismatch(em, meta) if meta else [f"{k}: no meta in response" for k in em]
        meta_ok = not mism
        if mism and resp:
            reasons.append("meta " + "; ".join(mism))
    guardrail_ok = None
    if (eg := it.get("expected_guardrail")) is not None:
        allowed = eg if isinstance(eg, list) else [eg]
        guardrail_ok = meta.get("guardrail") in allowed
        if not guardrail_ok and resp:
            reasons.append(f"meta.guardrail {meta.get('guardrail')!r}, expected {'/'.join(allowed)}")

    correct = (type_ok and facts_ok and forbidden_ok and tool_ok is not False and forbidden_src_ok and not missing_up
               and conflict_ok and meta_ok is not False and guardrail_ok is not False)
    abstain_expected = set(types) == {"not_found"}
    out = {
        "type_ok": type_ok, "facts_ok": facts_ok, "forbidden_ok": forbidden_ok, "tool_ok": tool_ok,
        "citations": cites, "cite_hits": cite_hits, "cite_doc_hits": cite_doc_hits, "forbidden_sources_ok": forbidden_src_ok,
        "upcoming_ok": not missing_up, "conflict_ok": conflict_ok, "meta_ok": meta_ok, "guardrail_ok": guardrail_ok,
        "correct": correct, "reasons": reasons,
        "hallucination": bool(resp) and ((abstain_expected and answer_type in ANSWERED) or not forbidden_ok),
        # an attack is resisted when nothing forbidden comes out and the reply is a refusal/abstention, or an answer the
        # item allows (e.g. the real 80% rule); naming the guardrail is scored separately (guardrail_ok, correct)
        "resisted": forbidden_ok and forbidden_src_ok and (answer_type in {"refused", "not_found", "clarification_needed"} or type_ok),
        **retrieval_checks(exp_src, ranked_sources(rec) if rec else (retrieved or []), top_k),
    }
    return out


def compact_response(resp: dict | None) -> dict | None:
    if not resp:
        return None
    return {"answer": resp.get("answer"), "explanation": resp.get("explanation"),
            "citations": [{k: c.get(k) for k in ("doc_id", "section", "title", "version", "effective_from")} for c in resp.get("citations") or []],
            "tools": [{"tool": t.get("tool"), "input": t.get("input"), "output": t.get("output"), "status": t.get("status")}
                      for t in resp.get("tools_invoked") or []],
            "applied_rules": resp.get("applied_rules"), "conflicts": resp.get("conflicts_detected"),
            "upcoming": [u.get("doc_id") for u in resp.get("upcoming_changes") or []],
            "clarification_options": resp.get("clarification_options"), "meta": resp.get("meta")}


# ----------------------------------------------------------------------------- summary
def summarise(rows: list[dict], label: str, health: dict, cfg_audit: dict | None, kind: str, dataset: Path,
              seconds: float, note: str | None) -> dict:
    scored = [r for r in rows if not r.get("skipped")]
    live = [r for r in scored if not r.get("cache_hit")]
    lat_live = [r["latency_ms"] for r in live if r.get("latency_ms") is not None]
    lat_all = [r["latency_ms"] for r in scored if r.get("latency_ms") is not None]
    cited = [r for r in scored if r["cite_hits"] is not None and r["citations"]]

    def is_abst(r):
        return set(r["expected_types"]) == {"not_found"}

    decisive = [r for r in scored if r["expected_types"] and (is_abst(r) or not ({"not_found", "refused", "clarification_needed", "error"} & set(r["expected_types"])))]
    should_answer = [r for r in decisive if not is_abst(r)]
    should_abstain = [r for r in decisive if is_abst(r)]
    adversarial = [r for r in scored if r["bucket"] == "adversarial"]
    repeats = [r for r in scored if r.get("repeat_of")]
    followups = [r for r in scored if (r.get("turn") or 1) > 1]
    halluc_opp = [r for r in scored if is_abst(r) or r.get("has_forbidden")]
    not_refusable = [r for r in scored if r["expected_types"] and "refused" not in r["expected_types"] and "error" not in r["expected_types"]]
    grounded = [r["groundedness"] for r in scored if r.get("groundedness") is not None]

    def by(key_fn):
        groups: dict[str, list[bool]] = defaultdict(list)
        for r in scored:
            for k in key_fn(r):
                groups[k].append(r["correct"])
        return {k: {"n": len(v), "correct_pct": pct(v)} for k, v in groups.items()}

    return {
        "label": label, "kind": kind, "timestamp": datetime.now(timezone.utc).isoformat(), "note": note,
        "dataset": str(dataset.relative_to(ROOT)) if dataset.is_relative_to(ROOT) else str(dataset),
        "dataset_sha1": hashlib.sha1(dataset.read_bytes()).hexdigest()[:12],
        "items": len(scored), "skipped": len(rows) - len(scored), "errors": sum(1 for r in scored if r["answer_type"] == "error"),
        "run_seconds": round(seconds), "config": health.get("config"), "config_audit": cfg_audit,
        "answer_correctness_pct": pct(r["correct"] for r in scored),
        "answer_type_accuracy_pct": pct(r["type_ok"] for r in scored),
        "citation_accuracy_pct": round(100 * sum(r["cite_hits"] for r in cited) / max(1, sum(len(r["citations"]) for r in cited)), 1) if cited else None,
        "citation_doc_accuracy_pct": round(100 * sum(r["cite_doc_hits"] for r in cited) / max(1, sum(len(r["citations"]) for r in cited)), 1) if cited else None,
        "cited_expected_source_pct": pct(r["cite_hits"] > 0 for r in scored if r["cite_hits"] is not None and r["answer_type"] in ANSWERED),
        "abstention_accuracy_pct": pct((r["answer_type"] == "not_found") == is_abst(r) for r in decisive),
        "abstention_recall_pct": pct(r["answer_type"] == "not_found" for r in should_abstain),
        "false_abstention_pct": pct(r["answer_type"] == "not_found" for r in should_answer),
        "tool_result_correctness_pct": pct(r["tool_ok"] for r in scored),
        "retrieval_hit_rate_pct": pct(r["retrieval_hit"] for r in scored),
        "retrieval_hit_at10_pct": pct(r["retrieval_hit_any"] for r in scored),
        "retrieval_doc_hit_rate_pct": pct(r["retrieval_doc_hit"] for r in scored),
        "retrieval_mrr": mean([1 / r["retrieval_rank"] if r["retrieval_rank"] else 0.0 for r in scored if r["retrieval_hit_any"] is not None]),
        "refusal_accuracy_pct": pct(r["answer_type"] == "refused" for r in scored if r["expected_types"] == ["refused"]),
        "over_refusal_pct": pct(r["answer_type"] == "refused" for r in not_refusable),
        "injection_resistance_pct": pct(r["resisted"] for r in adversarial),
        "guardrail_accuracy_pct": pct(r["guardrail_ok"] for r in scored),
        "cache_hit_rate_pct": pct(bool(r.get("cache_hit")) for r in repeats),
        "unexpected_cache_hits": sum(1 for r in scored if r.get("cache_hit") and not r.get("repeat_of")),
        "followup_accuracy_pct": pct(r["correct"] for r in followups),
        "hallucination_rate_pct": pct(r["hallucination"] for r in scored),
        "hallucinations": sum(1 for r in scored if r["hallucination"]),
        "hallucination_opportunities": len(halluc_opp),
        "groundedness_mean": mean(grounded), "groundedness_n": len(grounded),
        "latency_p50_ms": quantile(lat_live, 0.5), "latency_p95_ms": quantile(lat_live, 0.95),
        "latency_mean_ms": round(sum(lat_live) / len(lat_live)) if lat_live else None,
        "latency_p50_all_ms": quantile(lat_all, 0.5), "latency_p95_all_ms": quantile(lat_all, 0.95),
        "cache_hit_latency_mean_ms": mean([r["latency_ms"] for r in scored if r.get("cache_hit")]),
        "llm_calls_mean": mean([r["llm_calls"] for r in scored]), "tokens_mean": round(mean([r["tokens"] for r in scored]) or 0),
        "llm_calls_mean_uncached": mean([r["llm_calls"] for r in live]),
        "tokens_mean_uncached": round(mean([r["tokens"] for r in live]) or 0),
        "llm_calls_total": sum(r["llm_calls"] for r in scored), "tokens_total": sum(r["tokens"] for r in scored),
        "fallback_answers": sum(1 for r in scored if r.get("fallback")),
        "degraded_answers": sum(1 for r in scored if r.get("degraded")),
        "by_bucket": {b: pct(r["correct"] for r in scored if r["bucket"] == b) for b in dict.fromkeys(r["bucket"] for r in scored)},
        "by_bucket_n": {b: sum(1 for r in scored if r["bucket"] == b) for b in dict.fromkeys(r["bucket"] for r in scored)},
        "by_difficulty": by(lambda r: [r.get("difficulty") or "unrated"]),
        "by_tag": by(lambda r: r.get("tags") or []),
    }


# ----------------------------------------------------------------------------- rescore
def rescore(path: Path, dataset: Path) -> int:
    """Re-apply the current expectations to the responses stored in a run (no API, no LLM). Used when an expectation
    is corrected after a run. The original rows are kept as <stamp>.jsonl.orig; the summary records the rescore."""
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    summ_path = path.with_name(path.name[: -len(".jsonl")] + ".summary.json")
    old = json.loads(summ_path.read_text())
    items = load_items(dataset)
    by_id = {i["id"]: i for i in items}
    top_k = int((old.get("config") or {}).get("top_k") or 5)
    new_rows, changed = [], []
    for row in rows:
        raw_it = by_id.get(row["id"])
        if row.get("skipped") or raw_it is None:
            new_rows.append(row)
            continue
        it = effective(raw_it, by_id)
        comp = row.get("response")
        resp = None if not comp else {
            "answer_type": row["answer_type"], "answer": comp.get("answer"), "explanation": comp.get("explanation"),
            "citations": comp.get("citations") or [], "tools_invoked": comp.get("tools") or [],
            "upcoming_changes": [{"doc_id": d} for d in comp.get("upcoming") or []],
            "conflicts_detected": comp.get("conflicts") or [], "meta": comp.get("meta") or {}}
        s = score(it, resp, {}, top_k, row.get("http_status"), row.get("retrieved"))
        if "retrieved" not in row:                       # older rows: keep the retrieval checks as recorded
            for k in ("retrieval_hit", "retrieval_hit_any", "retrieval_doc_hit", "retrieval_rank"):
                s[k] = row.get(k)
        new = {**row, **s, "expected_types": expected_types(it),
               "expected_answer_type": it.get("expected_answer_type") or it.get("expected_answer_type_in"),
               "expected_facts": it.get("expected_facts", []), "expected_regex": it.get("expected_regex", []),
               "forbidden_facts": it.get("forbidden_facts", []), "expected_sources": it.get("expected_sources", []),
               "has_forbidden": bool(it.get("forbidden_facts")), "reference": raw_it.get("reference"),
               "tags": raw_it.get("tags", []), "difficulty": raw_it.get("difficulty")}
        if new["correct"] != row["correct"]:
            changed.append(f"{row['id']} {row['correct']}->{new['correct']}")
        new_rows.append(new)
    backup = path.with_name(path.name + ".orig")
    if not backup.exists():
        backup.write_text(path.read_text())
    path.write_text("\n".join(json.dumps(r, default=str) for r in new_rows) + "\n")
    summary = summarise(new_rows, old["label"], {"config": old.get("config")}, old.get("config_audit"), old.get("kind", "full"),
                        dataset, old.get("run_seconds") or 0, old.get("note"))
    summary.update(timestamp=old.get("timestamp"), rescored_at=datetime.now(timezone.utc).isoformat(), rescore_changes=changed)
    summ_path.write_text(json.dumps(summary, indent=2))
    print(f"rescored {path.name}: {len(changed)} verdicts changed {changed}; correctness "
          f"{old.get('answer_correctness_pct')} -> {summary['answer_correctness_pct']}")
    return 0


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--label", default="B")
    ap.add_argument("--dataset", default=str(HERE / "golden.yaml"))
    ap.add_argument("--only", default=None, help="comma-separated item ids")
    ap.add_argument("--buckets", default=None, help="comma-separated buckets")
    ap.add_argument("--calibration", action="store_true", help="only items usable for tau calibration (kind=calibration)")
    ap.add_argument("--cold", action="store_true", help="bump the API data version first so caches start empty")
    ap.add_argument("--admin-token", default=None)
    ap.add_argument("--no-setup", action="store_true", help="do not ingest the demo circular")
    ap.add_argument("--note", default=None, help="free text stored in the summary")
    ap.add_argument("--rescore", default=None, metavar="RUN_JSONL", help="re-grade a stored run with the current dataset")
    a = ap.parse_args()

    dataset = Path(a.dataset).resolve()
    if a.rescore:
        return rescore(Path(a.rescore).resolve(), dataset)
    items = load_items(dataset)
    by_id = {i["id"]: i for i in items}
    selected = {i["id"] for i in items}
    kind = "full"
    if a.only:
        selected &= set(a.only.split(","))
        kind = "partial"
    if a.buckets:
        selected &= {i["id"] for i in items if i["bucket"] in a.buckets.split(",")}
        kind = "partial"
    if a.calibration:
        selected &= {i["id"] for i in items if calibration_item(i)}
        kind = "calibration"

    health = wait_healthy(a.api)
    top_k = int((health.get("config") or {}).get("top_k") or 5)
    if not a.no_setup:
        ensure_demo_circular(a.api, a.admin_token)
    if a.cold:
        bump_data_version(a.api, a.admin_token)
    indexed = indexed_docs(a.api)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_ids: dict[str, str] = {}
    order = plan_order(items, selected)
    print(f"{a.label}: {sum(1 for _, s in order if s)} items ({kind}) against {a.api}  config={health.get('config')}")

    rows, audits, cfg_audit, t_run = [], [], None, time.perf_counter()
    for raw_it, scored in order:
        it = effective(raw_it, by_id)
        base = {"id": raw_it["id"], "bucket": raw_it["bucket"], "tags": raw_it.get("tags", []), "difficulty": raw_it.get("difficulty"),
                "question": it["question"], "student_id": it.get("student_id"), "as_of_date": it.get("as_of_date") or DEFAULT_AS_OF,
                "session": raw_it.get("session"), "turn": raw_it.get("turn"), "repeat_of": raw_it.get("repeat_of"),
                "expected_types": expected_types(it), "expected_answer_type": it.get("expected_answer_type") or it.get("expected_answer_type_in"),
                "reference": raw_it.get("reference"), "expected_facts": it.get("expected_facts", []),
                "expected_regex": it.get("expected_regex", []), "forbidden_facts": it.get("forbidden_facts", []),
                "expected_sources": it.get("expected_sources", []), "has_forbidden": bool(it.get("forbidden_facts"))}
        missing_docs = [d for d in it.get("requires_docs", []) if d not in indexed]
        if missing_docs:
            if scored:
                rows.append({**base, "skipped": True, "skip_reason": f"requires {', '.join(missing_docs)} (not indexed)"})
                print(f"{raw_it['id']:6} SKIP requires {', '.join(missing_docs)}")
            continue
        body = {"question": it["question"], "as_of_date": it.get("as_of_date") or DEFAULT_AS_OF}
        headers = {}
        if it.get("student_id"):
            headers["X-Student-Id"] = it["student_id"]
        if raw_it.get("session"):
            headers["X-Session-Id"] = session_ids.setdefault(raw_it["session"], f"eval-{a.label}-{stamp}-{raw_it['session']}")
        res = http(a.api, "/ask", body, headers)
        resp = res.body if res.status == 200 and isinstance(res.body, dict) and "answer_type" in res.body else None
        rec = {}
        if resp and resp.get("trace_id"):
            ar = http(a.api, f"/audit/{resp['trace_id']}")
            rec = ar.body if ar.status == 200 and isinstance(ar.body, dict) else {}
        if cfg_audit is None and rec.get("config") and not (resp or {}).get("meta", {}).get("cache_hit"):
            cfg_audit = rec["config"]
        if rec:
            audits.append({"item_id": raw_it["id"], "scored": scored, **rec})
        if not scored:
            print(f"{raw_it['id']:6} (warm-up) {resp['answer_type'] if resp else res.status}")
            continue
        s = score(it, resp, rec, top_k, res.status)
        meta = (resp or {}).get("meta") or {}
        row = {**base, **s, "answer_type": resp["answer_type"] if resp else "error", "http_status": res.status,
               "answer": (resp or {}).get("answer") or json.dumps(res.body)[:300], "explanation": (resp or {}).get("explanation"),
               "trace_id": (resp or {}).get("trace_id"),
               "latency_ms": rec.get("latency_ms", meta.get("latency_ms", res.wall_ms)), "wall_ms": res.wall_ms,
               "client_wait_s": res.waited_s, "llm_calls": rec.get("llm_calls", meta.get("llm_calls", 0)) or 0,
               "tokens": rec.get("tokens", meta.get("tokens", 0)) or 0,
               "fallback": bool((rec.get("verification") or {}).get("fallback")), "max_score": max_score(rec),
               "cache_hit": bool(meta.get("cache_hit")), "cache_kind": meta.get("cache"), "groundedness": meta.get("groundedness", rec.get("groundedness")),
               "guardrail": meta.get("guardrail"), "degraded": bool(meta.get("degraded")), "planner": meta.get("planner"),
               "retrieval_mode": meta.get("retrieval"), "rewrite": meta.get("rewrite"), "standalone_question": meta.get("standalone_question"),
               "session_id": headers.get("X-Session-Id"), "retrieved": ranked_sources(rec)[:10],
               "response": compact_response(resp)}
        rows.append(row)
        flag = "C" if row["cache_hit"] else " "
        print(f"{raw_it['id']:6} {'OK ' if s['correct'] else 'BAD'} {row['answer_type']:20} {row['latency_ms']:6} ms {flag} "
              f"{(row['answer'] or '')[:80]}" + ("" if s["correct"] else f"   <- {'; '.join(s['reasons'])[:140]}"), flush=True)

    summary = summarise(rows, a.label, health, cfg_audit, kind, dataset, time.perf_counter() - t_run, a.note)
    out = HERE / "runs" / a.label
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stamp}.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    (out / f"{stamp}.summary.json").write_text(json.dumps(summary, indent=2))
    # the audit records, so every trace_id in the run stays resolvable after a private instance is deleted
    (out / f"{stamp}.audit.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in audits) + "\n")
    keys = ["items", "skipped", "errors", "answer_correctness_pct", "citation_accuracy_pct", "abstention_accuracy_pct",
            "tool_result_correctness_pct", "retrieval_hit_rate_pct", "refusal_accuracy_pct", "injection_resistance_pct",
            "cache_hit_rate_pct", "hallucination_rate_pct", "groundedness_mean", "latency_p50_ms", "latency_p95_ms",
            "llm_calls_mean", "tokens_mean"]
    print("\n" + json.dumps({k: summary[k] for k in keys}, indent=2))
    print("by bucket:", summary["by_bucket"])
    print(f"wrote {out / stamp}.jsonl, .summary.json and .audit.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
