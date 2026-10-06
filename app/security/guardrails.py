"""Input and output guardrails.

Input:  prompt injection and jailbreak attempts, encoded payloads, bulk / other-student data requests, abuse.
        PII in questions is redacted before anything is logged or audited.
Output: no other student's identifiers, no system-prompt leakage, no contact details that are not in the evidence.

These are deterministic checks in front of (and behind) the LLM. The structural defences remain the main line:
the planner never sees documents, the composer has no tools, identity is injected by code.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field

INJECTION = [re.compile(p, re.I) for p in [
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.?!]{0,40}?(?:\b(?:instructions?|prompts?|guidelines)\b|\b(?:your|system|all|these|those|above|prior)\s+(?:rules?|polic(?:y|ies)|constraints?|filters?)\b)",
    r"\b(?:reveal|print|show|display|repeat|output|leak|tell me)\b[^.?!]{0,30}\b(?:system|hidden|initial|developer)\s+(?:prompt|instructions?|message)\b",
    r"\bwhat (?:is|are) your (?:system )?(?:prompt|instructions)\b",
    r"<\|?(?:im_start|im_end|system|endoftext)\|?>|\[/?INST\]|###\s*(?:system|instruction)|\bBEGIN SYSTEM PROMPT\b",
    r"\b(?:new|updated|real) instructions?\s*:", r"\bfrom now on,? you\b",
    r"\byour\s+(?:hidden|secret|initial|internal|original|system|developer)\s+(?:instructions?|prompts?|rules|guidelines)\b",
    r"\brepeat\s+(?:everything|all|the text|the words|what is written|whatever is)\b[^.?!]{0,20}\b(?:above|before)\b",
]]
JAILBREAK = [re.compile(p, re.I) for p in [
    r"\byou are now\b", r"\bdeveloper mode\b", r"\bjail ?break\b", r"\bDAN\b", r"\bdo anything now\b",
    r"\bpretend (?:to be|you are|that you)\b", r"\bact as (?:an? |the )?(?:admin|administrator|registrar|dean|controller|system|root|hacker|unrestricted)",
    r"\bno (?:rules|restrictions|limits)\b", r"\bwithout (?:any )?(?:restrictions|filters|rules)\b",
]]
RECORD_CHANGE = re.compile(r"\b(?:approve|change|update|edit|modify|increase|correct|set|mark)\b[^?]{0,20}\bmy (?:attendance|marks?|results?|cgpa|grades?)\b", re.I)
BULK = [re.compile(p, re.I) for p in [
    r"\b(?:list|show|give|display|send|export|dump|tell me)\b[^?]{0,30}\b(?:all|every|each|other)\s+(?:the\s+)?students?\b",
    r"\b(?:list|show|give|which|who are the)\b[^?]{0,20}\bstudents?\s+(?:with|who have|having|who failed|who are)\b",
    r"\b(?:attendance|marks|results?|cgpa|records?|backlogs?)\s+of\s+(?:all|every|each|other)\s+(?:the\s+)?students?\b",
    r"\b(?:which|what|how many|list(?: the)?|name(?: the)?)\s+students?\s+(?:have|has|had|got|scored|failed|with|are|were)\b"
    r"[^?]{0,30}\b(?:cgpa|gpa|marks?|attendance|backlogs?|results?|grades?|scores?)\b",
    r"\b(?:dump|export|download)\b[^?]{0,30}\b(?:database|table|records|data)\b",
    r"\bselect\s+\*\s+from\b|\bdrop\s+table\b|;\s*--",
]]
ABUSE = re.compile(r"\byou(?:'re| are)?\s+(?:an?\s+)?(?:idiot|stupid|moron|useless|dumb)\b|\b(?:f+u+c+k\w*|b[i1]tch\w*|bastard|dumbass)\b", re.I)
PII = [("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
       ("phone", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
       ("aadhaar", re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")),
       ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"))]
B64 = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}")
LEAK = re.compile(r"You are the planning step|You write the final answer for a university|Return ONLY JSON|untrusted document content", re.I)

REFUSALS = {
    "prompt_injection": ("I can't follow instructions that try to change how I work. Ask me about university rules or your own records.",
                         "The question contained instructions aimed at the assistant, so it was blocked by the input guardrail."),
    "jailbreak": ("I can't take on a different role or bypass the university's rules. Ask me about rules or your own records.",
                  "The question tried to change the assistant's role or permissions, so it was blocked by the input guardrail."),
    "bulk_data": ("I can't share data about other students or export records.",
                  "Personal records are available only to the student who is signed in, one student at a time."),
    "record_change": ("I can only read records; I can't change attendance, marks or results.",
                      "Corrections go through your department office or the Controller of Examinations."),
    "abuse": ("Let's keep this respectful. Ask me about university rules or your own records and I'll help.",
              "The message contained abusive language."),
}


@dataclass
class GuardResult:
    blocked: bool = False
    reason: str | None = None
    findings: list[str] = field(default_factory=list)
    redacted: str = ""


def _decoded_payloads(text: str) -> list[str]:
    out = []
    for m in B64.findall(text):
        try:
            s = base64.b64decode(m + "=" * (-len(m) % 4), validate=False).decode("utf-8", errors="ignore")
            if sum(c.isprintable() for c in s) > 0.9 * max(1, len(s)):
                out.append(s)
        except (binascii.Error, ValueError):
            continue
    return out


def redact_pii(text: str) -> tuple[str, list[str]]:
    found = []
    for name, rx in PII:
        if rx.search(text):
            found.append(f"pii:{name}")
            text = rx.sub(f"[{name} removed]", text)
    return text, found


def check_input(question: str) -> GuardResult:
    q = " ".join(question.split())
    redacted, findings = redact_pii(q)
    candidates = [q] + _decoded_payloads(q)
    if len(candidates) > 1:
        findings.append("encoded_payload")
    for text in candidates:
        if any(p.search(text) for p in INJECTION):
            return GuardResult(True, "prompt_injection", findings + ["injection_pattern"], redacted)
        if any(p.search(text) for p in JAILBREAK):
            return GuardResult(True, "jailbreak", findings + ["jailbreak_pattern"], redacted)
    if RECORD_CHANGE.search(q):
        return GuardResult(True, "record_change", findings + ["write_request"], redacted)
    if any(p.search(q) for p in BULK):
        return GuardResult(True, "bulk_data", findings + ["bulk_request"], redacted)
    if ABUSE.search(q):
        return GuardResult(True, "abuse", findings + ["abuse_lexicon"], redacted)
    return GuardResult(False, None, findings, redacted)


def check_output(text: str, self_id: str | None, evidence_text: str) -> tuple[str, list[str]]:
    """Returns (safe_text, findings). Removes other students' IDs, prompt leakage and contact details not in evidence."""
    findings = []
    for sid in set(re.findall(r"\bS\d{4}\b", text)) - ({self_id} if self_id else set()):
        text = text.replace(sid, "[another student]")
        findings.append("other_student_id")
    if LEAK.search(text):
        findings.append("prompt_leak")
        text = LEAK.sub("", text)
    for name, rx in PII[:2]:
        for m in set(rx.findall(text)):
            if m not in evidence_text:
                text = text.replace(m, f"[{name} removed]")
                findings.append(f"unsupported_{name}")
    return text, findings
