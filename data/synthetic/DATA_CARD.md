# Data card: synthetic student records (Annex E)

All records in `data/synthetic/out/` are synthetic. They describe fictional people and contain no real personal data.

## Purpose

These records exercise the personal tools: exam eligibility, supplementary eligibility, placement eligibility and attendance projection. They cover the edge cases those tools must get exactly right, plus the effect of a rule change (75% → 80%) on the same student. They are not meant to model a real cohort statistically.

## Generator

| Item | Value |
|---|---|
| Model | `llama3.1:8b` on local Ollama (Q4_K_M, 8.0B parameters, model ID `46e0c10c039e`) |
| Sampling | temperature 0.7, seed 42 |
| Calls | 8: four programme/batch groups × two batches of five students |
| Tokens | 5,352 prompt + 11,972 completion |
| Time | 236 s in total (22–37 s per call) |
| Script | `data/synthetic/generate.py` (spec in `spec.yaml`, edge cases in `edge_cases.yaml`) |

The LLM writes names, CGPA, attendance counts and marks. Code fixes everything structural:
- student IDs (S1001–S1040);
- course codes, names and semesters;
- classes held, exam sessions and maximum marks.

## Prompts

- **System prompt:** [`prompts/system.md`](prompts/system.md), used verbatim.
- **Per-call log:** [`generation_log.jsonl`](generation_log.jsonl) records, for every call, the rendered user prompt, the raw model output, token counts, latency and any validation problems.

## How the schema is enforced

1. **JSON-schema output.** Ollama's `format` constrains the output to the batch schema. `student_id` and `course_code` are enums of the allowed values, so the model cannot invent IDs or courses.
2. **Pydantic validation** of every batch.
3. **One retry per batch** with the violations fed back to the model.
4. **Deterministic repair** of anything still invalid. Every repair is logged.
5. **Edge-case overlay.** Code sets the exact boundary values below and logs each change.
6. **Validation.** CSVs are written in the fixed Annex C schema, then checked by `validate.py`.

## Row counts and distributions

| Table | Rows |
|---|---|
| students | 40 (10 each: B.Tech CSE 2025, CSE 2024, ECE 2025, ECE 2024) |
| courses | 12 (6 per programme) |
| attendance | 220 |
| results | 141 (132 PASS, 6 FAIL, 1 ABSENT, 2 DETAINED) |

**Attendance by percentage band:** below 65%: 10 · 65–74.99%: 35 · 75–79.99%: 38 · 80–89.99%: 53 · 90% and above: 84.

**CGPA range:** 6.49 to 9.10.

## Edge cases included (exact values, asserted by validation check V15)

| Student | Case |
|---|---|
| S1001 | Attendance exactly at the 75% threshold (CS201 30/40) |
| S1002 | 77.50% (CS201 31/40): eligible under the 75% regulation, not under the 80% circular |
| S1003 | One class below 75% (CS201 29/40 = 72.50%) |
| S1004 | Failed by one mark (MA101 39/100) |
| S1005 | Absent in the end-semester exam (CS101) |
| S1006 | Detained for low attendance (CS101 26/44 = 59.09%) |
| S1007 | Rounding trap (CS202 47/59 = 79.66%, must never display as 80%) |
| S1008 | Failed, then passed the supplementary exam (no active backlog) |
| S1011 | Three backlogs, CGPA 6.90 |
| S1012 | One backlog (CS201), CGPA 7.20: the placement what-if |
| S1013 | CGPA exactly at the 6.50 placement cut-off |
| S1014 | CGPA 6.49, just below the cut-off |
| S1021 | ECE attendance exactly at 75% (EC201 30/40) |
| S1031 | ECE student detained (EC201 25/40) |

## Validation results (`validation_report.json`)

All 16 checks pass, with 0 violations. Thresholds come from the rule registry, not from constants.

| ID | Check |
|---|---|
| V01 | Columns and types match Annex C |
| V02 | `student_id` is S + 4 digits and outside S9000–S9999 (reserved) |
| V03 | No course code starts with JDG (reserved for judges) |
| V04 | Value ranges (semester, CGPA, backlogs, attendance, marks) |
| V05 | `total_marks = internal_marks + external_marks` |
| V06 | Attendance and results reference existing students and courses |
| V07 | Course programmes match student programmes |
| V08 | Course semester ≤ the student's current semester (warning) |
| V09 | Result consistent with the 40% pass mark (from the rule registry) |
| V10 | ABSENT results have no external marks |
| V11 | DETAINED only when attendance is below the minimum (from the rule registry) |
| V12 | `active_backlogs` equals the courses whose latest attempt is not PASS |
| V13 | Supplementary attempts follow a non-PASS regular attempt |
| V14 | Coverage: ≥ 30 students, 2 programmes, 2 batches, 6 courses |
| V15 | Every edge case in the manifest holds |
| V16 | Distribution report (information) |

## What the LLM got wrong (from the log)

- **Nothing structural.** All eight calls passed schema and Pydantic validation on the first attempt. The `problems` field is empty in every log row, no retries were needed, and the repair list is empty (`generation_summary.json`).
- **It did not produce the boundary values.** Code overwrote 21 values to create the cases above. Examples: S1007's CS202 attendance set to 47/59, S1014's CGPA to 6.49, and S1006's CS101 result to DETAINED. Asking a sampler at temperature 0.7 for an exact boundary is unreliable by design. The full list of changes is in `generation_summary.json` under `edge_case_overlay`.

## Known limitations

- **CGPA is generated, not recomputed.** It cannot be recomputed from course grades, because grade points are not part of Annex C.
- **Uniform class counts.** `classes_held` is the same for every student in a course.
- **Simple exam history.** One regular session per semester, plus one supplementary session for the batch.
- **Names are illustrative only.** They are LLM-generated fictional names and could coincide with a real person's name by chance.
- **No timetables, electives or transfers.**
