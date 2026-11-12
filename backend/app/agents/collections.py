"""Collections agent: writes the shortfall reminder text. It never decides amounts or who pays;
those numbers come from the database and are passed in. The text is length-checked and falls back to a template."""
from pydantic import BaseModel, Field

from ..providers import llm
from ..services.money import fmt_usd

SYSTEM = (
    "You write short, friendly payment reminders for a group of friends sharing a subscription. "
    "Use only the facts given. Never invent amounts, dates, links or names. Two sentences, max 280 characters. "
    "No greeting line, no sign-off, no emojis."
)


class Reminder(BaseModel):
    text: str = Field(min_length=10, max_length=400)


def template(name: str, group: str, merchant: str, owed_minor: int, shortfall_minor: int, renewal: str) -> str:
    if owed_minor > 0:
        return (f"{name}, {group} is {fmt_usd(shortfall_minor)} short for {merchant} renewing on {renewal}. "
                f"Your share of {fmt_usd(owed_minor)} is still open; tap Pay my share to keep the plan running.")
    return (f"{name}, you're all paid up. {group} is still {fmt_usd(shortfall_minor)} short for {merchant} "
            f"renewing on {renewal}; the plan pauses if it isn't covered.")


def write_reminder(name: str, group: str, merchant: str, owed_minor: int, shortfall_minor: int,
                   renewal: str) -> tuple[str, str | None]:
    """Returns (text, ai_label or None if the template was used)."""
    fallback = template(name, group, merchant, owed_minor, shortfall_minor, renewal)
    facts = (f"Recipient: {name}\nGroup: {group}\nSubscription: {merchant}\nRenews on: {renewal}\n"
             f"Group shortfall: {fmt_usd(shortfall_minor)}\n"
             f"Recipient still owes: {fmt_usd(owed_minor) if owed_minor else 'nothing (already paid)'}\n"
             f"The in-app button is called 'Pay my share'.")
    out = llm.complete(SYSTEM, facts, max_tokens=200)
    if not out:
        return fallback, None
    try:
        text = Reminder(text=out.strip().strip('"')).text
    except ValueError:
        return fallback, None
    # Guard: every dollar figure in the text must be one we supplied.
    import re

    allowed = {fmt_usd(shortfall_minor), fmt_usd(owed_minor)}
    for amt in re.findall(r"\$\d[\d,]*(?:\.\d{2})?", text):
        norm = amt if "." in amt else amt + ".00"
        if norm not in allowed:
            return fallback, None
    return text, llm.label()
