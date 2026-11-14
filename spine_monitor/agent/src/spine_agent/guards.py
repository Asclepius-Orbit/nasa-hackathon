"""Deterministic guardrails of the agent: a post-filter against diagnosis claims and the no-LLM fallback reply.

Pure functions: no I/O, no LLM. Extend the term lists below to cover more conditions or phrasings.

Only AFFIRMATIVE claims about the user are flagged, never a bare condition name: "sciatica is a nerve condition" and a
refusal that repeats the user's condition ("I cannot diagnose herniated discs") are clean. A claim is a lead-in phrase
("you have", "it looks like", "tienes", "sembla"...) followed by a condition, or an assertion phrase ("diagnosed with").
A claim is dropped when a hedge, negation or question word (if, whether, know, tell, no, not, si, saber, puc...) appears
earlier in the same clause (text since the last . ! ? ; : , or newline): "I don't know if you have X", "si tienes X, consulta".
"""

import re
import unicodedata
from typing import Any

DISCLAIMER = "This is an automatic summary, not a medical diagnosis; please consult the flight surgeon for any clinical decision."

# Named conditions (English, Spanish, Catalan), matched after a lead-in phrase.
CONDITION_TERMS = (
    "herniated disc",
    "slipped disc",
    "disc herniation",
    "hernia discal",
    "hernia de disco",
    "hernia de disc",
    "sciatica",
    "ciatica",
    "fracture",
    "fractura",
    "scoliosis",
    "escoliosis",
    "escoliosi",
    "spinal stenosis",
    "estenosis",
    "estenosi",
    "osteoporosis",
    "osteoporosi",
)

# Lead-in phrases that assert the condition about the user when a condition follows ("you have a herniated disc").
LEAD_IN_PHRASES = (
    "you have",
    "you probably have",
    "you likely have",
    "it looks like",
    "looks like",
    "it seems to be",
    "seems to be",
    "it sounds like",
    "sounds like",
    "this is",
    "tienes",
    "tiene",
    "probablemente tienes",
    "probablemente tiene",
    "parece",
    "esto es",
    "tens",
    "probablement tens",
    "sembla",
    "aixo es",
)
ARTICLES = ("a", "an", "una", "un")

# Phrases that are an affirmative claim by themselves.
ASSERTION_PHRASES = (
    "you are suffering from",
    "you suffer from",
    "diagnosed with",
    "has been diagnosed",
    "usted padece",
    "padeces",
    "pateixes",
)

# Words that, earlier in the same clause, turn a claim into a hedge, a negation or a question (accent-free).
HEDGE_WORDS = (
    "if",
    "whether",
    "know",
    "tell",
    "say",
    "determine",
    "no",
    "not",
    "never",
    "si",
    "saber",
    "sap",
    "puc",
    "puedo",
    "poder",
    "nunca",
    "mai",
)


def _alternatives(terms: tuple[str, ...]) -> str:
    return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))


_CONDITION = _alternatives(CONDITION_TERMS)
_PATTERNS = (
    re.compile(rf"\b(?:{_alternatives(ASSERTION_PHRASES)})\b"),
    re.compile(rf"\b(?:{_alternatives(LEAD_IN_PHRASES)})\s+(?:(?:{_alternatives(ARTICLES)})\s+)?(?:{_CONDITION})\b"),
)
_CLAUSE_BREAK = re.compile(r"[.!?;:,\n]")
_HEDGE = re.compile(rf"\b(?:{_alternatives(HEDGE_WORDS)})\b|n['’]t\b")


def _fold(text: str) -> str:
    """Lowercase and strip accents one character at a time, so offsets match the original text."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in text.lower())


def _is_hedged(folded: str, start: int) -> bool:
    """A hedge, negation or question word precedes `start` in the same clause."""
    breaks = [m.end() for m in _CLAUSE_BREAK.finditer(folded, 0, start)]
    return _HEDGE.search(folded, breaks[-1] if breaks else 0, start) is not None


def find_diagnostic_terms(text: str) -> list[str]:
    """Affirmative diagnosis claims found in `text` (lowercased, unique, by first appearance); empty means clean.

    The bare words "diagnose"/"diagnosis" and bare condition names are not flagged (see the module docstring).
    """
    folded = _fold(text)
    found: list[tuple[int, int, str]] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(folded):
            if not _is_hedged(folded, match.start()):
                found.append((match.start(), -match.end(), text[match.start() : match.end()].lower()))
    terms: list[str] = []
    for _, _, term in sorted(found):
        if term not in terms:
            terms.append(term)
    return terms


def _value(value: Any, unit: Any = None) -> str:
    if value is None:
        return "not available"
    return f"{value} {unit}" if unit else str(value)


def _driver_lines(drivers: list[dict[str, Any]]) -> list[str]:
    if not drivers:
        return ["No main drivers are reported."]
    lines = ["Main drivers:"]
    lines += [f"- {d.get('feature_id', 'unknown')}: {_value(d.get('value'), d.get('unit'))}" for d in drivers]
    return lines


def _explanation_lines(explanation: dict[str, Any]) -> list[str]:
    assessment = explanation.get("assessment") or {}
    rules = assessment.get("fired_rules") or []
    lines = ["Rules fired:" if rules else "No rules fired."]
    for rule in rules:
        lines.append(f"- {rule.get('rule_id', 'unknown')}: {rule.get('description') or 'no description'}")
    based_on = assessment.get("based_on") or {}
    if based_on:
        lines.append(
            f"Based on baseline {_value(based_on.get('baseline_id'))} "
            f"(version {_value(based_on.get('baseline_version'))}) "
            f"and engine {_value(based_on.get('engine_version'))}."
        )
    return lines


def fallback_reply(status: dict[str, Any] | None, explanation: dict[str, Any] | None) -> str:
    """English template used when the LLM is unavailable or there is no data. Never guesses, never advises treatment."""
    if status is None:
        lines = ["I don't know: there is no data for this astronaut, so I cannot report a status."]
        return "\n".join([*lines, DISCLAIMER])
    astronaut = status.get("astronaut_id") or "the astronaut"
    assessment = status.get("latest_assessment")
    if not assessment:
        return "\n".join([f"No assessment is available yet for {astronaut}.", DISCLAIMER])
    level = assessment.get("level")
    head = f"Astronaut {astronaut}: current level is {str(level).upper()}" if level else f"Astronaut {astronaut}: level not available"
    as_of = assessment.get("as_of")
    lines = [f"{head} (as of {as_of})." if as_of else f"{head}."]
    lines += _driver_lines(assessment.get("drivers") or [])
    if explanation is not None:
        lines += _explanation_lines(explanation)
    return "\n".join([*lines, DISCLAIMER])
