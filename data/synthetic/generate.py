"""Generate synthetic students with a local LLM (Ollama), enforce the schema, overlay edge cases, write Annex C CSVs.

    uv run python data/synthetic/generate.py            # uses spec.yaml (model llama3.1:8b)
    uv run python data/synthetic/generate.py --dry-run  # print the first prompt only

Discipline (guide §4.2): JSON-schema constrained output -> Pydantic validation -> one retry per batch with the
violations fed back -> deterministic repair of anything still wrong (logged) -> exact edge-case overlay (logged)
-> CSVs in the fixed Annex C schema -> validate.py. Every rendered prompt and raw output is logged verbatim.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, Field, ValidationError, create_model

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def load_rules() -> dict[str, str]:
    with open(ROOT / "data/corpus/rules_seed.csv", newline="") as f:
        return {r["parameter"]: r["value"] for r in csv.DictReader(f)}


def batch_model(ids: list[str], att_codes: list[str], res_codes: list[str]) -> type[BaseModel]:
    """A Pydantic model whose JSON schema pins student_id and course_code to the allowed values."""
    Sid = Literal[tuple(ids)]  # type: ignore[valid-type]
    Att = create_model("Att", course_code=(Literal[tuple(att_codes)], ...), classes_attended=(int, Field(ge=0)))  # type: ignore
    fields = {"student_id": (Sid, ...), "full_name": (str, ...), "cgpa": (float, Field(ge=0, le=10)),
              "attendance": (list[Att], ...)}
    if res_codes:
        Res = create_model("Res", course_code=(Literal[tuple(res_codes)], ...), internal_marks=(int, ...),  # type: ignore
                           external_marks=(int, ...), result=(Literal["PASS", "FAIL"], ...))
        fields["results"] = (list[Res], ...)
    Student = create_model("Student", **fields)  # type: ignore
    return create_model("Batch", students=(list[Student], ...))  # type: ignore


def problems_in(batch: dict, ids: list[str], held: dict[str, int], att_codes: list[str], res_codes: list[str],
                pass_mark: int) -> list[str]:
    out = []
    got = {s["student_id"]: s for s in batch.get("students", [])}
    for sid in ids:
        s = got.get(sid)
        if not s:
            out.append(f"{sid}: missing student")
            continue
        a = {x["course_code"]: x for x in s.get("attendance", [])}
        for c in att_codes:
            if c not in a:
                out.append(f"{sid}: missing attendance for {c}")
            elif not 0 <= a[c]["classes_attended"] <= held[c]:
                out.append(f"{sid}: {c} classes_attended {a[c]['classes_attended']} must be between 0 and {held[c]}")
        r = {x["course_code"]: x for x in s.get("results", [])}
        for c in res_codes:
            x = r.get(c)
            if not x:
                out.append(f"{sid}: missing result for {c}")
                continue
            if not 0 <= x["internal_marks"] <= 40 or not 0 <= x["external_marks"] <= 60:
                out.append(f"{sid}: {c} marks out of range (internal 0-40, external 0-60)")
            total = x["internal_marks"] + x["external_marks"]
            if (total >= pass_mark) != (x["result"] == "PASS"):
                out.append(f"{sid}: {c} total {total} with pass mark {pass_mark} must be {'PASS' if total >= pass_mark else 'FAIL'}")
        if not 5.0 <= float(s.get("cgpa", 0)) <= 9.5:
            out.append(f"{sid}: cgpa {s.get('cgpa')} must be between 5.00 and 9.50")
    return out


def call_ollama(base: str, model: str, system: str, user: str, schema: dict, temperature: float, seed: int) -> tuple[dict, dict]:
    payload = {"model": model, "stream": False, "format": schema, "keep_alive": "30m",
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "options": {"temperature": temperature, "seed": seed, "num_ctx": 8192}}
    t0 = time.perf_counter()
    r = httpx.post(f"{base}/api/chat", json=payload, timeout=600)
    r.raise_for_status()
    body = r.json()
    meta = {"ms": int((time.perf_counter() - t0) * 1000), "prompt_tokens": body.get("prompt_eval_count"),
            "completion_tokens": body.get("eval_count")}
    return json.loads(body["message"]["content"]), meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    spec = yaml.safe_load((HERE / "spec.yaml").read_text())
    edge = yaml.safe_load((HERE / "edge_cases.yaml").read_text())
    rules = load_rules()
    pass_mark = int(float(rules["pass_min_total_pct"]))      # thresholds come from the rule registry, never constants
    model = args.model or spec["model"]
    system = (HERE / "prompts/system.md").read_text()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log_path = HERE / "generation_log.jsonl"
    log = open(log_path, "w") if not args.dry_run else None
    rng = random.Random(spec["seed"])

    courses, students, attendance, results = [], [], [], []
    repairs: list[str] = []
    for prog, cs in spec["programmes"].items():
        for c in cs:
            courses.append({"course_code": c["course_code"], "course_name": c["course_name"], "programme": prog,
                            "semester": c["semester"], "credits": c["credits"]})

    calls = 0
    for g in spec["groups"]:
        prog, year = g["programme"], g["batch_year"]
        b = spec["batches"][year]
        cur = b["current_semester"]
        cat = spec["programmes"][prog]
        held = {c["course_code"]: c["classes_held"] for c in cat}
        att_codes = [c["course_code"] for c in cat if c["semester"] <= cur]
        res_codes = [c["course_code"] for c in cat if c["semester"] < cur]
        ids_all = [f"S{g['first_id'] + i}" for i in range(g["count"])]
        for k in range(0, len(ids_all), spec["batch_size"]):
            ids = ids_all[k:k + spec["batch_size"]]
            request = {"programme": prog, "batch_year": year, "current_semester": cur, "pass_mark_pct": pass_mark,
                       "student_ids": ids,
                       "attendance_courses": [{"course_code": c["course_code"], "course_name": c["course_name"],
                                               "classes_held": c["classes_held"]} for c in cat if c["course_code"] in att_codes],
                       "result_courses": [{"course_code": c["course_code"], "course_name": c["course_name"]}
                                          for c in cat if c["course_code"] in res_codes]}
            user = "Generate records for this request:\n" + json.dumps(request, indent=2)
            Model = batch_model(ids, att_codes, res_codes)
            schema = Model.model_json_schema()
            if args.dry_run:
                print(system, "\n---\n", user)
                return 0
            batch, feedback = None, None
            for attempt in (1, 2):
                prompt = user if not feedback else user + "\n\nYour previous output had these problems. Fix all of them:\n- " + "\n- ".join(feedback)
                try:
                    raw, meta = call_ollama(args.ollama, model, system, prompt, schema, spec["temperature"], spec["seed"] + calls)
                    calls += 1
                    parsed = Model.model_validate(raw).model_dump()
                    feedback = problems_in(parsed, ids, held, att_codes, res_codes, pass_mark)
                    batch = parsed
                except (httpx.HTTPError, ValidationError, json.JSONDecodeError) as e:
                    raw, meta, feedback = None, {}, [f"output was not valid against the schema: {str(e)[:300]}"]
                log.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "group": f"{prog} {year}", "ids": ids,
                                      "attempt": attempt, "model": model, "temperature": spec["temperature"],
                                      "seed": spec["seed"] + calls - 1, "system_prompt": system, "user_prompt": prompt,
                                      "raw_output": raw, "problems": feedback, **meta}) + "\n")
                log.flush()
                print(f"{prog} {year} {ids[0]}..{ids[-1]} attempt {attempt}: {len(feedback)} problem(s)", flush=True)
                if not feedback:
                    break
            if feedback:
                repairs += [f"repaired after retry: {p}" for p in feedback]
            got = {s["student_id"]: s for s in (batch or {}).get("students", [])}
            for sid in ids:
                s = got.get(sid) or {"full_name": f"Student {sid}", "cgpa": 7.0, "attendance": [], "results": []}
                if sid not in got:
                    repairs.append(f"{sid}: LLM omitted the student; filled with placeholder values")
                cgpa = min(9.5, max(5.0, round(float(s["cgpa"]), 2)))
                students.append({"student_id": sid, "full_name": s["full_name"].strip(), "programme": prog, "batch_year": year,
                                 "current_semester": cur, "cgpa": cgpa, "active_backlogs": 0})
                a = {x["course_code"]: x["classes_attended"] for x in s["attendance"]}
                for c in att_codes:
                    v = a.get(c)
                    if v is None or not 0 <= v <= held[c]:
                        new = rng.randint(int(held[c] * 0.78), held[c])
                        repairs.append(f"{sid} {c}: attendance {v} invalid; set to {new}")
                        v = new
                    attendance.append({"student_id": sid, "course_code": c, "classes_held": held[c], "classes_attended": v})
                r = {x["course_code"]: x for x in s.get("results", [])}
                for c in res_codes:
                    sem = next(x["semester"] for x in cat if x["course_code"] == c)
                    x = r.get(c) or {"internal_marks": rng.randint(24, 36), "external_marks": rng.randint(28, 50)}
                    i_m, e_m = min(40, max(0, x["internal_marks"])), min(60, max(0, x["external_marks"]))
                    res = "PASS" if i_m + e_m >= pass_mark else "FAIL"
                    if c not in r or x.get("result") != res or (i_m, e_m) != (x["internal_marks"], x["external_marks"]):
                        repairs.append(f"{sid} {c}: result/marks repaired to {i_m}+{e_m}={i_m + e_m} {res}")
                    results.append({"student_id": sid, "course_code": c, "exam_session": b["exam_sessions"][sem],
                                    "exam_type": "REGULAR", "internal_marks": i_m, "external_marks": e_m,
                                    "total_marks": i_m + e_m, "max_marks": spec["marks"]["max_marks"], "result": res})

    # ---- edge-case overlay (exact values, logged)
    overlay: list[str] = []
    st = {s["student_id"]: s for s in students}
    for e in edge:
        sid = e["id"]
        s = st[sid]
        cat = spec["programmes"][s["programme"]]
        b = spec["batches"][s["batch_year"]]
        for code, (att, held) in (e.get("attendance") or {}).items():
            row = next((a for a in attendance if a["student_id"] == sid and a["course_code"] == code), None)
            if row is None:
                attendance.append({"student_id": sid, "course_code": code, "classes_held": held, "classes_attended": att})
            else:
                row.update(classes_held=held, classes_attended=att)
            overlay.append(f"{sid} {code}: attendance set to {att}/{held}")
        if e.get("others_pass"):
            for r in results:
                if r["student_id"] == sid and r["result"] != "PASS":
                    r.update(internal_marks=max(r["internal_marks"], 25), external_marks=max(r["external_marks"], 30))
                    r.update(total_marks=r["internal_marks"] + r["external_marks"], result="PASS")
                    overlay.append(f"{sid} {r['course_code']}: set to PASS so the backlog count is exact")
        for spec_r in e.get("results") or []:
            code = spec_r["course_code"]
            sem = next(x["semester"] for x in cat if x["course_code"] == code)
            etype = spec_r.get("exam_type", "REGULAR")
            session = b["supplementary_session"] if etype == "SUPPLEMENTARY" else b["exam_sessions"][sem]
            results[:] = [r for r in results if not (r["student_id"] == sid and r["course_code"] == code and r["exam_type"] == etype)]
            ext = spec_r["external_marks"]
            results.append({"student_id": sid, "course_code": code, "exam_session": session, "exam_type": etype,
                            "internal_marks": spec_r["internal_marks"], "external_marks": ext,
                            "total_marks": None if ext is None else spec_r["internal_marks"] + ext,
                            "max_marks": spec["marks"]["max_marks"], "result": spec_r["result"]})
            overlay.append(f"{sid} {code} {etype}: result set to {spec_r['result']}")
        if "cgpa" in e:
            s["cgpa"] = e["cgpa"]
            overlay.append(f"{sid}: cgpa set to {e['cgpa']}")

    # ---- active_backlogs derived from results (latest attempt not PASS)
    latest: dict[tuple[str, str], dict] = {}
    for r in sorted(results, key=lambda r: (r["exam_session"][:4], r["exam_type"] == "SUPPLEMENTARY")):
        latest[(r["student_id"], r["course_code"])] = r
    for s in students:
        s["active_backlogs"] = sum(1 for (sid, _), r in latest.items() if sid == s["student_id"] and r["result"] != "PASS")

    def write(name: str, rows: list[dict], cols: list[str]) -> None:
        with open(out / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})

    write("students", students, ["student_id", "full_name", "programme", "batch_year", "current_semester", "cgpa", "active_backlogs"])
    write("courses", courses, ["course_code", "course_name", "programme", "semester", "credits"])
    write("attendance", attendance, ["student_id", "course_code", "classes_held", "classes_attended"])
    write("results", results, ["student_id", "course_code", "exam_session", "exam_type", "internal_marks", "external_marks",
                               "total_marks", "max_marks", "result"])
    (HERE / "generation_summary.json").write_text(json.dumps(
        {"model": model, "temperature": spec["temperature"], "seed": spec["seed"], "llm_calls": calls,
         "students": len(students), "courses": len(courses), "attendance_rows": len(attendance), "result_rows": len(results),
         "repairs": repairs, "edge_case_overlay": overlay}, indent=2))
    print(f"wrote {len(students)} students, {len(courses)} courses, {len(attendance)} attendance rows, {len(results)} results "
          f"in {calls} LLM calls; {len(repairs)} repairs, {len(overlay)} overlay changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
