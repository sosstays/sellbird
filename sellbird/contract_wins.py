"""A contract the company says it won, for the writer.

CONTRACT_WIN is read off the company's own site: its homepage, the page a
search found, or its news, projects or press page ("we have been awarded
the contract for...", "<name> has been appointed main contractor on...",
"works have commenced on site at..."). It's stored as the sentence the
company wrote, the contract value when stated, and the date.

A firm that has just won work is about to need what comes with it: crews
housed, plant hired, staff recruited, materials bought. So the date matters
as much as the win: a contract from last month is a reason to write, one
from three years ago is history, and one with no date can't be called new.
"""

import re
from datetime import date

# Past this, a win is history, not news (about a year: most jobs a contractor
# announces are under way or finished by then).
CONTRACT_RECENT_DAYS = 365
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")
_SEP = " · "


def contract_text(found: dict) -> str:
    """The CONTRACT_WIN value as stored: "<their sentence> · €12M · 2026-03"."""
    summary = " ".join(str(found.get("summary") or "").replace("·", ",").split())
    return _SEP.join(p for p in (summary, found.get("amount"), found.get("date")) if p)


def parse_contract(text: str) -> dict:
    """contract_text, read back into its parts (any may be "")."""
    out = {"summary": "", "amount": "", "date": ""}
    for part in (p.strip() for p in (text or "").split("·")):
        if not part:
            continue
        if _DATE_RE.match(part):
            out["date"] = part
        elif part[0] in "$€£" and len(part) <= 12:
            out["amount"] = part
        elif not out["summary"]:
            out["summary"] = part
    return out


def _as_date(when: str) -> date | None:
    """The earliest day a stated date could mean: "2025" is 1 January, so an
    imprecise date never makes a win look newer than it is."""
    m = _DATE_RE.match(when or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


def contract_recency(when: str, today: date | None = None) -> str:
    """"recent", "old" (over about a year) or "unknown" (no date found)."""
    day = _as_date(when)
    if day is None:
        return "unknown"
    return "recent" if ((today or date.today()) - day).days <= CONTRACT_RECENT_DAYS else "old"


def contract_fact(found: dict, today: date | None = None) -> str:
    """One fact line for the writer's prompt."""
    said = found["summary"] or "won a contract"
    value = f" (value stated: {found['amount']})" if found["amount"] else ""
    when = found["date"]
    recency = contract_recency(when, today)
    if recency == "recent":
        months = max(0, ((today or date.today()) - _as_date(when)).days // 30)
        return (f"- Contract win, as their own site says: \"{said}\"{value}, dated {when} "
                f"(about {months} month(s) ago)")
    if recency == "old":
        return (f"- Contract win, as their own site says: \"{said}\"{value}, dated {when}. That is "
                "over a year ago, so not recent: don't write as if they just won it")
    return (f"- Contract win, as their own site says: \"{said}\"{value}, but no date was found: "
            "don't call it recent or new")


async def contract_facts(state, company_id: str, today: date | None = None) -> list[str]:
    """Fact lines for the writer's prompt, or [] when no win is known.

    A failed read is just "nothing known", never a reason not to write."""
    if not company_id or state is None:
        return []
    lookup = getattr(state, "contract_win", None)
    if lookup is None:
        return []
    try:
        found = await lookup(company_id)
    except Exception:
        return []
    return [contract_fact(found, today)] if found else []
