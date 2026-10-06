"""LLM call 1: question -> JSON plan. The planner sees ONLY the question (never documents or records),
so instructions hidden in documents cannot reach it. Tool arguments are filled by code, not the model."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.llm.client import LLMError, LLMResult
from app.policy.rules import PARAMETERS
from app.tools.core import TOOLS

Category = Literal["policy_fact", "procedure", "personal_data", "personal_eligibility", "multi_step", "other"]
PERSONAL = {"personal_data", "personal_eligibility", "multi_step"}
ToolName = Literal["get_student_profile", "get_attendance", "get_results", "check_exam_eligibility",
                   "check_supplementary_eligibility", "check_placement_eligibility", "attendance_projection"]
ParamName = Literal["min_attendance_pct", "max_condonation_pct", "pass_min_total_pct", "supplementary_allowed_results",
                    "min_cgpa_placement", "max_active_backlogs_placement"]


class Plan(BaseModel):
    category: Category
    mentions_other_student: bool = False
    course_mentions: list[str] = Field(default_factory=list, max_length=3)
    rule_parameters: list[ParamName] = Field(default_factory=list, max_length=3)
    tools: list[ToolName] = Field(default_factory=list, max_length=4)
    pass_courses: list[str] = Field(default_factory=list, max_length=3)
    future_classes: int | None = None
    search_queries: list[str] = Field(default_factory=list, max_length=3)


SYSTEM = """You are the planning step of a university student-services assistant.
Return ONLY JSON that matches the schema. Do not answer the question.

category:
- policy_fact: asks what a rule or policy says ("What is the minimum attendance?")
- procedure: asks how to do something ("How do I apply for the supplementary exam?")
- personal_data: asks for the user's own records ("What is my attendance in Data Structures?")
- personal_eligibility: asks whether the user qualifies ("Am I eligible for the CS201 exam?")
- multi_step: combines the user's records, rules and a hypothetical ("If I pass X, can I sit for placements?")
- other: anything else

tools (the user's identity is added automatically; never put student IDs anywhere):
{tools}

rule_parameters (the rules the question depends on):
{params}

Rules:
- mentions_other_student = true only if the user asks for the records of a specific other person (a name, an ID, "my friend").
- course_mentions: course names or codes exactly as written in the question.
- pass_courses: courses the user says "if I pass ..." about.
- future_classes: a number of upcoming classes if the question gives one.
- search_queries: 1 to 3 short queries that would find the governing clause in university documents."""

EXAMPLES = """Examples:
Q: "Am I eligible to sit the Data Structures exam?" -> {"category":"personal_eligibility","course_mentions":["Data Structures"],"tools":["check_exam_eligibility"],"rule_parameters":["min_attendance_pct"],"search_queries":["minimum attendance end-semester examination"]}
Q: "How do I apply for the supplementary exam?" -> {"category":"procedure","tools":[],"rule_parameters":[],"search_queries":["supplementary examination application procedure"]}
Q: "I failed Mathematics III. If I pass the supplementary, can I sit for placements?" -> {"category":"multi_step","course_mentions":["Mathematics III"],"pass_courses":["Mathematics III"],"tools":["check_supplementary_eligibility","check_placement_eligibility"],"rule_parameters":["min_cgpa_placement","max_active_backlogs_placement"],"search_queries":["placement eligibility CGPA backlogs"]}
Q: "What is Rahul's attendance?" -> {"category":"personal_data","mentions_other_student":true,"tools":[],"search_queries":["attendance"]}"""


def build_prompt(question: str) -> tuple[str, str]:
    tools = "\n".join(f"- {n}: {TOOLS[n].description}" for n in ToolName.__args__)
    params = "\n".join(f"- {p}: {PARAMETERS[p].label}" for p in ParamName.__args__)
    return SYSTEM.format(tools=tools, params=params), f"{EXAMPLES}\n\nQuestion: <<<{question}>>>"


# ----------------------------------------------------------------------------- deterministic router (fallback + cross-check)
# Personal = asks about MY record, or about MY eligibility. A first-person hypothetical about policy
# ("Is 65% enough if I have a medical certificate?") is a policy question, not a personal one.
PERSONAL_DATA_RE = re.compile(r"\bmy\s+(?:own\s+)?(?:attendance|marks?|results?|grades?|cgpa|gpa|backlogs?|records?|scores?|"
                              r"eligibility|courses?|semester)\b|\bhow many (?:classes|lectures) (?:have|did) i\b", re.I)
PERSONAL_ELIG_RE = re.compile(r"\b(?:am i|can i|will i|could i|do i|would i|may i|shall i|should i|i am|i'm)\b[^?.]{0,50}?"
                              r"\b(?:eligible|qualif\w*|sit|appear|write|take|register|allowed|permitted|attend|miss|skip|bunk|pass|clear)\b"
                              r"|\bi (?:failed|passed|have \d+ backlogs?)\b|\bif i pass\b", re.I)
PERSONAL_RE = re.compile(f"{PERSONAL_DATA_RE.pattern}|{PERSONAL_ELIG_RE.pattern}", re.I)
OTHER_RE = re.compile(r"\b(my friend|friend's|classmate|roommate|someone else|another student|other student|his|her)\b[^?]{0,60}\b(attendance|marks|result|cgpa|record|grade|backlog)", re.I)
T = {
    "attendance": re.compile(r"attendance|classes? attended|attended|present|bunk|miss(ed)? classes", re.I),
    "eligible": re.compile(r"eligib|allowed|permitted|qualif|can i (sit|appear|write|take|register)|sit (for|the)|appear (for|in)|debar|detain", re.I),
    "supplementary": re.compile(r"supplementar|re-?exam|re-?appear|repeat exam|make-?up exam", re.I),
    "placement": re.compile(r"placement|recruit|campus drive|company|companies", re.I),
    "results": re.compile(r"\b(result|marks?|grade|score|scored|passed|failed|pass mark|passing)\b", re.I),
    "profile": re.compile(r"\b(cgpa|gpa|backlogs?)\b", re.I),
    "whatif": re.compile(r"\bif i\b|suppose|assuming|what if|would i", re.I),
    "projection": re.compile(r"how many (more )?(classes|lectures)|can i (miss|skip|bunk)|afford to miss", re.I),
    "procedure": re.compile(r"\bhow (do|can|to|should)\b|procedure|process|steps|apply|application|register for", re.I),
    "fees": re.compile(r"\bfees?\b|tuition|hostel|charges?", re.I),
}
NUM_RE = re.compile(r"\b(\d{1,3})\s+(?:more\s+)?(?:classes|lectures)\b", re.I)
CODE_RE = re.compile(r"\b[A-Z]{2,4}\s?\d{3}\b")


def route(question: str) -> Plan:
    q = question
    hit = {k: bool(r.search(q)) for k, r in T.items()}
    personal = bool(PERSONAL_RE.search(q))
    tools: list[str] = []
    params: list[str] = []
    if hit["attendance"] or hit["eligible"] and not (hit["supplementary"] or hit["placement"]):
        params.append("min_attendance_pct")
    if re.search(r"condon|medical", q, re.I) and hit["attendance"]:
        params.append("max_condonation_pct")
    if hit["supplementary"]:
        params.append("supplementary_allowed_results")
    if hit["placement"]:
        params += ["min_cgpa_placement", "max_active_backlogs_placement"]
    if hit["results"] and not personal:
        params.append("pass_min_total_pct")

    if personal and hit["whatif"] and (hit["placement"] or hit["supplementary"]):
        category = "multi_step"
        if hit["supplementary"]:
            tools.append("check_supplementary_eligibility")
        if hit["placement"]:
            tools.append("check_placement_eligibility")
    elif personal and hit["projection"]:
        category, tools = "multi_step", ["attendance_projection"]
    elif personal and (hit["eligible"] or hit["supplementary"] or hit["placement"]) and not hit["procedure"]:
        category = "personal_eligibility"
        if hit["supplementary"]:
            tools.append("check_supplementary_eligibility")
        elif hit["placement"]:
            tools.append("check_placement_eligibility")
        else:
            tools.append("check_exam_eligibility")
    elif personal and (hit["attendance"] or hit["results"] or hit["profile"]) and not hit["procedure"]:
        category = "personal_data"
        if hit["attendance"]:
            tools.append("get_attendance")
        if hit["results"]:
            tools.append("get_results")
        if hit["profile"]:
            tools.append("get_student_profile")
    elif hit["procedure"]:
        category = "procedure"
    elif any(hit[k] for k in ("attendance", "eligible", "supplementary", "placement", "results", "profile", "fees")):
        category = "policy_fact"
    else:
        category = "other"

    if category == "procedure":
        params = []
    m = NUM_RE.search(q)
    return Plan(category=category, mentions_other_student=bool(OTHER_RE.search(q)),
                course_mentions=CODE_RE.findall(q)[:3], rule_parameters=list(dict.fromkeys(params))[:3],
                tools=list(dict.fromkeys(tools))[:4], pass_courses=[], future_classes=int(m.group(1)) if m else None,
                search_queries=[question])


def plan_question(llm, question: str) -> tuple[Plan, str, LLMResult | None, list[str]]:
    """Returns (plan, source, llm_result, notes). Falls back to the router if the LLM output is unusable."""
    router = route(question)
    system, user = build_prompt(question)
    notes: list[str] = []
    result = None
    unavailable = False
    for attempt in range(2):
        try:
            result = llm.chat_json("plan", system, user, Plan.model_json_schema(), context={"question": question})
            plan = Plan.model_validate(result.data)
            break
        except (LLMError, ValidationError) as e:
            notes.append(f"plan attempt {attempt + 1} rejected: {str(e)[:160]}")
            plan = None
            if getattr(e, "kind", None) in ("unavailable", "timeout"):
                unavailable = True
                break                                   # the gateway already retried; go straight to the router
    if plan is None:
        return router, "router_fallback" if unavailable else "router", result, notes

    # Cross-check with the deterministic router: code has the final say on personal intent and tool choice
    # (measured: the 8B planner sometimes picks a plausible-but-wrong tool, e.g. CGPA for an attendance question).
    plan.mentions_other_student = plan.mentions_other_student or router.mentions_other_student
    if router.category in PERSONAL:
        plan.category = router.category
        plan.tools = router.tools or [t for t in plan.tools]
    elif plan.category in PERSONAL and not PERSONAL_RE.search(question):
        plan.category, plan.tools = router.category, []
    if plan.category not in PERSONAL:
        plan.tools = []
    if plan.category == "procedure":
        plan.rule_parameters = []            # procedures are answered from text; rule anchors would crowd it out
    if plan.category != "procedure":
        plan.rule_parameters = list(dict.fromkeys(plan.rule_parameters + router.rule_parameters))[:3]
    plan.course_mentions = list(dict.fromkeys(plan.course_mentions + router.course_mentions))[:3]
    if plan.future_classes is None:
        plan.future_classes = router.future_classes
    plan.search_queries = list(dict.fromkeys([q for q in plan.search_queries if q.strip()] + [question]))[:3]
    return plan, "llm", result, notes


# ----------------------------------------------------------------------------- policy what-if
WHATIF_PCT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent\b|percent\b)", re.I)
MEDICAL = re.compile(r"medical|sick|ill(?:ness)?\b|hospital|doctor", re.I)
NO_MEDICAL = re.compile(r"\b(?:without|no|not|don'?t|do not|didn'?t|haven'?t|have no|lacking)\b[^.?!]{0,25}?"
                        r"(?:medical|sick|doctor|hospital)", re.I)
WHATIF_CUE = re.compile(r"\b(enough|sufficient|ok(?:ay)?|fine|acceptable|allowed|permitted|eligible|qualif\w*|sit|appear|write|take)\b", re.I)


def attendance_whatif(question: str) -> dict | None:
    """'Is 65% attendance enough with a medical certificate?' -> {"value_pct": "65", "medical": True}.
    Exactly one percentage, about attendance, asked as a yes/no about sitting the exam. Code then decides."""
    vals = WHATIF_PCT.findall(question)
    if len(vals) != 1 or not re.search(r"\battendance\b", question, re.I) or not WHATIF_CUE.search(question):
        return None
    if not 0 <= float(vals[0]) <= 100:
        return None
    medical = bool(MEDICAL.search(question)) and not NO_MEDICAL.search(question)
    return {"value_pct": vals[0], "medical": medical}
