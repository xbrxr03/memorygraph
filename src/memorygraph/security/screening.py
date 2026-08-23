from __future__ import annotations

import re
from dataclasses import dataclass

_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction_override", re.compile(r"\b(ignore|disregard)\b.{0,40}\binstructions?\b", re.I)),
    ("role_impersonation", re.compile(r"\b(system|developer)\s+(message|prompt)\b", re.I)),
    ("tool_coercion", re.compile(r"\b(run|execute|call)\b.{0,40}\b(tool|command|shell)\b", re.I)),
    ("destructive_shell", re.compile(r"\brm\s+-rf\b|\bformat\s+[a-z]:", re.I)),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(api[_ -]?key|password|secret)\b.{0,40}\b(send|upload|print)\b",
            re.I,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ContentAssessment:
    disposition: str
    reasons: tuple[str, ...]

    @property
    def safe_for_agent_context(self) -> bool:
        return self.disposition != "quarantined"


def assess_retrieved_content(content: str, *, trust_class: str) -> ContentAssessment:
    reasons = tuple(name for name, pattern in _INSTRUCTION_PATTERNS if pattern.search(content))
    untrusted = trust_class.casefold() in {"untrusted", "imported", "external", "unknown"}
    if reasons and untrusted:
        return ContentAssessment(disposition="quarantined", reasons=reasons)
    if reasons:
        return ContentAssessment(disposition="flagged", reasons=reasons)
    return ContentAssessment(disposition="safe", reasons=())
