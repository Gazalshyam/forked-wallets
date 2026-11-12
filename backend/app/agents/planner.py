"""Planner agent: natural language -> validated TaskSpec.

The budget is NOT taken from the model. It is the number the owner entered (hard rule 4).
The model only fills city / nights / guests / min rating, and its JSON is validated with Pydantic.
"""
import re

from pydantic import BaseModel, Field, field_validator

from ..providers import llm

KNOWN_CITIES = ["Austin", "Seattle", "New York", "Chicago", "San Francisco"]
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "a": 1}


class TaskSpec(BaseModel):
    kind: str = Field(default="HOTEL", pattern="^HOTEL$")
    city: str = Field(min_length=2, max_length=60)
    nights: int = Field(ge=1, le=14)
    guests: int = Field(default=2, ge=1, le=8)
    min_rating: str = Field(default="0.0", pattern=r"^[0-5](\.\d)?$")  # decimal text, not money
    max_total_minor: int = Field(gt=0)  # from the owner's budget, never from the model

    @field_validator("city")
    @classmethod
    def norm_city(cls, v: str) -> str:
        v = v.strip()
        for c in KNOWN_CITIES:
            if c.lower() == v.lower():
                return c
        return v.title()


class _LLMSpec(BaseModel):
    city: str = Field(min_length=2, max_length=60)
    nights: int = Field(ge=1, le=14)
    guests: int = Field(default=2, ge=1, le=8)
    min_rating: float | None = Field(default=None, ge=0, le=5)


SYSTEM = (
    "Extract a hotel search from the user's request. Reply with ONLY a JSON object with keys "
    '"city" (string), "nights" (integer), "guests" (integer, default 2), "min_rating" (number or null). '
    "Do not include prices or any other keys."
)


def _num(tok: str) -> int | None:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else WORDS.get(tok)


def rule_parse(text: str) -> dict:
    t = text.strip()
    out: dict = {}
    for c in KNOWN_CITIES:
        if re.search(rf"\b{re.escape(c)}\b", t, re.I):
            out["city"] = c
            break
    else:
        m = re.search(r"\bin ([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?)", t)
        if m:
            out["city"] = m.group(1)
    m = re.search(r"\b(\d+|one|two|three|four|five|six|seven|a)\s+nights?\b", t, re.I)
    out["nights"] = _num(m.group(1)) if m else 1
    m = re.search(r"\b(\d+|one|two|three|four|five|six)\s+(?:people|guests|persons|adults)\b", t, re.I)
    out["guests"] = _num(m.group(1)) if m else 2
    m = re.search(r"(?:rating|rated)\s*(?:of\s*)?(?:at least|>=|above|over)?\s*([0-5](?:\.\d)?)", t, re.I) or \
        re.search(r"([0-5](?:\.\d)?)\s*\+?\s*stars?", t, re.I)
    out["min_rating"] = m.group(1) if m else "0.0"
    return out


def suggest_budget_minor(text: str) -> int | None:
    """Deterministic code (not AI) reads 'under $200' to pre-fill the budget field; the owner confirms it."""
    m = re.search(r"(?:under|below|max(?:imum)?|within|up to|budget(?: of)?)\s*\$\s?(\d{1,6})(?:\.(\d{2}))?", text, re.I) or \
        re.search(r"\$\s?(\d{1,6})(?:\.(\d{2}))?", text)
    if not m:
        return None
    return int(m.group(1)) * 100 + int(m.group(2) or 0)


def plan(text: str, budget_minor: int) -> tuple[TaskSpec, str]:
    """Returns (spec, source) where source says whether the LLM or the rule parser produced the fields."""
    base = rule_parse(text)
    source = "rule parser (mock LLM)"
    out = llm.complete(SYSTEM, text[:1000], max_tokens=150)
    parsed = llm.extract_json(out, _LLMSpec)
    if parsed is not None:
        base.update({"city": parsed.city, "nights": parsed.nights, "guests": parsed.guests})
        if parsed.min_rating is not None:
            base["min_rating"] = f"{min(5.0, max(0.0, parsed.min_rating)):.1f}"
        source = f"Planner agent ({llm.label()})"
    if not base.get("city"):
        raise ValueError("Could not find a city in the request. Try e.g. 'Book a hotel in Austin for 2 nights under $200'.")
    spec = TaskSpec(city=base["city"], nights=base["nights"], guests=base["guests"],
                    min_rating=str(base.get("min_rating") or "0.0"), max_total_minor=budget_minor)
    return spec, source
