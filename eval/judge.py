"""LLM-as-judge over a finished evaluation run, calibrated against the exact-match grades (guide §7: state the method).

    uv run python eval/judge.py --label B                  # latest full run of B
    uv run python eval/judge.py --run eval/runs/B/<stamp>.jsonl
    uv run python eval/judge.py --label B --sanity 0 --repeat 0   # grading only

For every scored item the judge (Ollama llama3.1:8b, temperature 0, prompt in eval/judge_prompt.md, read verbatim)
returns {score: 0|1|2, reason}. Outputs next to the run: <stamp>.judge.jsonl and <stamp>.judge.summary.json with
percent agreement and Cohen's kappa against the exact-match `correct` flag, plus two checks of the judge itself:
probes (the reference answer should score 2, corrupted answers 0) and determinism (a subset judged twice).
Run it only when no evaluation is using Ollama.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

from evallib import ANSWERED, HERE, cohen_kappa, latest_run, mean

NOT_FOUND = "I could not find this information in the authorised university sources."
OUTCOME = {"retrieved_fact": "an answer taken from the university documents",
           "calculated": "a verdict computed from the student's own records",
           "not_found": "say the information is not in the authorised university sources (no answer)",
           "refused": "refuse the request",
           "clarification_needed": "ask a clarifying question (which course or option)",
           "conflict_flagged": "report that the sources conflict",
           "error": "the request is rejected"}
SCHEMA = {"type": "object", "properties": {"score": {"type": "integer", "enum": [0, 1, 2]}, "reason": {"type": "string"}},
          "required": ["score", "reason"]}


def load_prompt() -> tuple[str, str, str]:
    text = (HERE / "judge_prompt.md").read_text()
    blocks = re.findall(r"```text\n(.*?)\n```", text, re.S)
    if len(blocks) < 2:
        raise SystemExit("eval/judge_prompt.md must contain two ```text blocks (system prompt, user template)")
    return blocks[0], blocks[1], hashlib.sha1((blocks[0] + blocks[1]).encode()).hexdigest()[:12]


def call(ollama: str, model: str, system: str, user: str, timeout: float = 180) -> tuple[dict | None, int, int]:
    body = {"model": model, "stream": False, "format": SCHEMA, "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0, "seed": 7, "num_ctx": 4096}}
    for attempt in range(3):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(ollama.rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            data = json.loads(out["message"]["content"])
            if data.get("score") in (0, 1, 2):
                return data, int((time.perf_counter() - t0) * 1000), out.get("prompt_eval_count", 0) + out.get("eval_count", 0)
        except Exception as e:  # noqa: BLE001
            print(f"    judge call failed ({e.__class__.__name__}: {str(e)[:80]}); attempt {attempt + 1}")
            time.sleep(2)
    return None, 0, 0


def render(template: str, row: dict, answer_type: str, answer: str, explanation: str) -> str:
    exp = row.get("expected_types") or []
    outcome = " or ".join(OUTCOME.get(t, t) for t in exp) or "see the reference"
    asker = f"student {row['student_id']} (signed in)" if row.get("student_id") else "an anonymous user (not signed in)"
    if row.get("session") and (row.get("turn") or 1) > 1:
        asker += f"; this is turn {row['turn']} of a conversation, so it may refer to the previous question"
    facts = ", ".join(str(f) for f in row.get("expected_facts") or []) or "(see the reference answer)"
    forbidden = ", ".join(str(f) for f in row.get("forbidden_facts") or []) or "(nothing specific)"
    return template.format(question=row["question"], as_of=row.get("as_of_date"), asker=asker, expected_outcome=outcome,
                           reference=row.get("reference") or "(none)", facts=facts, forbidden=forbidden,
                           answer_type=answer_type, answer=answer or "", explanation=explanation or "")


# ----------------------------------------------------------------------------- probes
def perturb_numbers(text: str) -> str:
    def swap(m: re.Match) -> str:
        s = m.group(0)
        if "." in s:
            return f"{float(s) - 1.25:.2f}"
        n = int(s)
        return str(n + 3 if n < 20 else (n - 10 if n < 1000 else int(n * 0.8)))
    return re.sub(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", swap, re.sub(r"(?<=\d),(?=\d{3}\b)", "", text or ""))


def probes(rows: list[dict], n: int) -> list[dict]:
    """Reference-as-answer (expect 2) and a type-appropriate corruption of a correct answer (expect 0)."""
    pool = [r for r in rows if r.get("correct") and r.get("reference") and not r.get("skipped")]
    step = max(1, len(pool) // max(1, n))
    picked = pool[::step][:n]
    out = []
    for r in picked:
        exp = r.get("expected_types") or []
        main = exp[0] if exp else r["answer_type"]
        out.append({"id": r["id"], "probe": "reference_as_answer", "expect": 2, "answer_type": main,
                    "answer": r["reference"], "explanation": ""})
        if main in ANSWERED:
            if re.search(r"\d", r.get("answer") or ""):
                out.append({"id": r["id"], "probe": "numbers_perturbed", "expect": 0, "answer_type": r["answer_type"],
                            "answer": perturb_numbers(r["answer"]), "explanation": perturb_numbers(r.get("explanation") or "")})
            else:
                out.append({"id": r["id"], "probe": "abstained_instead", "expect": 0, "answer_type": "not_found",
                            "answer": NOT_FOUND, "explanation": "No authorised document in force answers this question."})
        elif main == "refused":
            out.append({"id": r["id"], "probe": "complied_instead", "expect": 0, "answer_type": "retrieved_fact",
                        "answer": "Sure. Here is what you asked for: the student's CGPA is 9.10 and attendance is 92.50%.",
                        "explanation": "Taken from the student records."})
        elif main == "not_found":
            out.append({"id": r["id"], "probe": "fabricated_instead", "expect": 0, "answer_type": "retrieved_fact",
                        "answer": "Yes. It is INR 12,000 per semester, payable at the start of term (FEES-2026 §4).",
                        "explanation": "Stated in the fee circular."})
        elif main == "clarification_needed":
            out.append({"id": r["id"], "probe": "guessed_instead", "expect": 0, "answer_type": "calculated",
                        "answer": "Your attendance is 64.00%, below the required 80%.", "explanation": "Computed from your records."})
    return out


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="B")
    ap.add_argument("--run", default=None, help="path to a run's .jsonl (default: latest full run of --label)")
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--sanity", type=int, default=24, help="items used for probes (0 = skip)")
    ap.add_argument("--repeat", type=int, default=15, help="items judged twice for determinism (0 = skip)")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    if a.run:
        run_path = Path(a.run)
        label = run_path.parent.name
    else:
        run = latest_run(a.label)
        if not run:
            raise SystemExit(f"no full run for label {a.label}")
        run_path, label = Path(run["_rows"]), a.label
    stamp = run_path.name[: -len(".jsonl")]
    rows = [json.loads(l) for l in run_path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if not r.get("skipped")][: a.limit]
    system, template, prompt_sha = load_prompt()
    print(f"judging {len(rows)} answers of {label}/{stamp} with {a.model} (prompt {prompt_sha})")

    t0 = time.time()
    out, tokens = [], 0
    for r in rows:
        data, ms, tok = call(a.ollama, a.model, system, render(template, r, r["answer_type"], r.get("answer"), r.get("explanation")))
        tokens += tok
        score = data["score"] if data else None
        jc = None if score is None else score == 2
        out.append({"id": r["id"], "bucket": r["bucket"], "trace_id": r.get("trace_id"), "score": score,
                    "reason": (data or {}).get("reason"), "judge_correct": jc, "exact_correct": r["correct"],
                    "agree": None if jc is None else jc == r["correct"], "ms": ms})
        mark = "=" if jc == r["correct"] else "≠"
        print(f"{r['id']:6} exact={'OK ' if r['correct'] else 'BAD'} judge={score} {mark} {((data or {}).get('reason') or '')[:100]}", flush=True)

    graded = [o for o in out if o["score"] is not None]
    ex = [o["exact_correct"] for o in graded]
    strict = [o["score"] == 2 for o in graded]
    lenient = [o["score"] >= 1 for o in graded]
    summary = {
        "label": label, "run_stamp": stamp, "model": a.model, "prompt_sha1": prompt_sha, "n": len(graded),
        "failed_calls": len(out) - len(graded), "mean_score": mean([o["score"] for o in graded]),
        "score_distribution": {s: sum(1 for o in graded if o["score"] == s) for s in (0, 1, 2)},
        "judge_correct_pct": round(100 * sum(strict) / len(strict), 1) if strict else None,
        "exact_correct_pct": round(100 * sum(ex) / len(ex), 1) if ex else None,
        "agreement_pct": round(100 * sum(x == y for x, y in zip(ex, strict)) / len(ex), 1) if ex else None,
        "kappa": cohen_kappa(ex, strict),
        "agreement_lenient_pct": round(100 * sum(x == y for x, y in zip(ex, lenient)) / len(ex), 1) if ex else None,
        "kappa_lenient": cohen_kappa(ex, lenient),
        "confusion": {"exact_ok_judge_ok": sum(1 for x, y in zip(ex, strict) if x and y),
                      "exact_ok_judge_not": sum(1 for x, y in zip(ex, strict) if x and not y),
                      "exact_bad_judge_ok": sum(1 for x, y in zip(ex, strict) if not x and y),
                      "exact_bad_judge_not": sum(1 for x, y in zip(ex, strict) if not x and not y)},
        "disagreements": [{"id": o["id"], "exact": o["exact_correct"], "score": o["score"], "reason": o["reason"],
                           "trace_id": o["trace_id"]} for o in graded if not o["agree"]],
    }

    if a.sanity:
        results = []
        by_id = {r["id"]: r for r in rows}
        for p in probes(rows, a.sanity):
            data, _, tok = call(a.ollama, a.model, system, render(template, by_id[p["id"]], p["answer_type"], p["answer"], p["explanation"]))
            tokens += tok
            got = data["score"] if data else None
            results.append({**p, "score": got, "ok": got == p["expect"], "reason": (data or {}).get("reason")})
            print(f"probe {p['id']:6} {p['probe']:20} expect {p['expect']} got {got}", flush=True)
        ref = [x for x in results if x["probe"] == "reference_as_answer" and x["score"] is not None]
        bad = [x for x in results if x["expect"] == 0 and x["score"] is not None]
        summary["probes"] = {
            "reference_as_answer": {"n": len(ref), "scored_2_pct": round(100 * sum(x["score"] == 2 for x in ref) / len(ref), 1) if ref else None},
            "corrupted": {"n": len(bad), "scored_0_pct": round(100 * sum(x["score"] == 0 for x in bad) / len(bad), 1) if bad else None,
                          "by_probe": {k: {"n": sum(1 for x in bad if x["probe"] == k),
                                           "scored_0": sum(1 for x in bad if x["probe"] == k and x["score"] == 0)}
                                       for k in dict.fromkeys(x["probe"] for x in bad)}},
            "misses": [{k: x[k] for k in ("id", "probe", "expect", "score", "reason")} for x in results if not x["ok"]],
        }

    if a.repeat:
        again = []
        for o, r in list(zip(out, rows))[: a.repeat]:
            data, _, tok = call(a.ollama, a.model, system, render(template, r, r["answer_type"], r.get("answer"), r.get("explanation")))
            tokens += tok
            again.append((o["score"], data["score"] if data else None))
        same = [x == y for x, y in again if x is not None and y is not None]
        summary["determinism"] = {"n": len(same), "identical_pct": round(100 * sum(same) / len(same), 1) if same else None}

    summary["seconds"] = round(time.time() - t0)
    summary["tokens"] = tokens
    (run_path.parent / f"{stamp}.judge.jsonl").write_text("\n".join(json.dumps(o) for o in out) + "\n")
    (run_path.parent / f"{stamp}.judge.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("disagreements",)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
