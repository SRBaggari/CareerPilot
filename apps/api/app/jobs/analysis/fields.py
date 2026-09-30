"""Parsers for explicitly stated job fields: salary, deadline, work mode, employment type.

Each returns None rather than guessing: an ambiguous currency symbol, an ambiguous
day/month order, or conflicting work modes produce no value.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.jobs.analysis.extracted import SalaryInfo
from app.jobs.models import SalaryPeriod
from app.profiles.models import EmploymentType, WorkplaceType

# --- Salary -------------------------------------------------------------------------------

_CURRENCY = r"(?:US\$|USD|EUR|GBP|INR|CAD|AUD|SGD|Rs\.?|[$\u20ac\u00a3\u20b9])"
_AMOUNT = r"\d[\d,]*(?:\.\d+)?"
_SCALE = r"(?:k|K|m|M|lakhs?|lacs?|L)\b"
SALARY_RE = re.compile(
    rf"(?P<cur>{_CURRENCY})\s?(?P<min>{_AMOUNT})\s?(?P<smin>{_SCALE})?"
    rf"(?:\s*(?:-|\u2013|\u2014|to)\s*(?:{_CURRENCY})?\s?(?P<max>{_AMOUNT})\s?(?P<smax>{_SCALE})?)?"
    r"(?:\s*(?P<lpa>LPA)\b)?"
    r"(?:\s*(?:/|per|an?)\s*(?P<period>hour|hr|day|week|month|mo|year|yr|annum)\b)?",
    re.IGNORECASE,
)
LPA_RE = re.compile(
    rf"(?P<min>{_AMOUNT})\s*(?:(?:-|\u2013|to)\s*(?P<max>{_AMOUNT})\s*)?LPA\b", re.IGNORECASE
)
_SALARY_CONTEXT = re.compile(
    r"salary|compensation|\bpay\b|pay range|\bctc\b|stipend|base|package|remuneration|"
    r"\bper (hour|year|month|annum)\b|/\s?(hr|yr|hour|year|month)\b|\blpa\b",
    re.IGNORECASE,
)
_CURRENCY_CODES = {
    "USD": "USD",
    "US$": "USD",
    "EUR": "EUR",
    "\u20ac": "EUR",
    "GBP": "GBP",
    "\u00a3": "GBP",
    "INR": "INR",
    "\u20b9": "INR",
    "RS": "INR",
    "RS.": "INR",
    "CAD": "CAD",
    "AUD": "AUD",
    "SGD": "SGD",
}
_PERIODS = {
    "hour": SalaryPeriod.HOUR,
    "hr": SalaryPeriod.HOUR,
    "day": SalaryPeriod.DAY,
    "week": SalaryPeriod.WEEK,
    "month": SalaryPeriod.MONTH,
    "mo": SalaryPeriod.MONTH,
    "year": SalaryPeriod.YEAR,
    "yr": SalaryPeriod.YEAR,
    "annum": SalaryPeriod.YEAR,
}


def _amount(raw: str | None, scale: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000}.get((scale or "").lower()[:1], 1)
    if (scale and scale.lower().startswith(("lakh", "lac"))) or scale == "L":
        multiplier = 100_000
    return value * multiplier


def parse_salary(text: str) -> SalaryInfo | None:
    """The first explicitly stated salary, from a line that talks about pay."""
    for line in text.splitlines():
        if not _SALARY_CONTEXT.search(line):
            continue
        if m := SALARY_RE.search(line):
            lpa = bool(m.group("lpa"))
            minimum = _amount(m.group("min"), m.group("smin") or ("L" if lpa else None))
            maximum = _amount(
                m.group("max"), m.group("smax") or m.group("smin") or ("L" if lpa else None)
            )
            currency = _CURRENCY_CODES.get(m.group("cur").upper())
            period = SalaryPeriod.YEAR if lpa else _PERIODS.get((m.group("period") or "").lower())
            info = SalaryInfo(line.strip()[:300], minimum, maximum, currency, period)
        elif m := LPA_RE.search(line):  # "12-18 LPA": Indian lakhs per annum
            info = SalaryInfo(line.strip()[:300], _amount(m.group("min"), "L"),
                              _amount(m.group("max"), "L"), "INR", SalaryPeriod.YEAR)  # fmt: skip
        else:
            continue
        if info.minimum is not None and info.maximum is not None and info.maximum < info.minimum:
            info.minimum = info.maximum = None  # keep the text, drop contradictory numbers
        return info
    return None


# --- Deadline -----------------------------------------------------------------------------

_DEADLINE_CUE = re.compile(
    r"deadline|apply by|apply before|applications? (?:close|closes|are due|due)|last date"
    r"|closing date|submit (?:your application|applications) by",
    re.IGNORECASE,
)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}  # fmt: skip
_MONTH_WORD = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DATE_PATTERNS = (
    re.compile(rf"{_MONTH_WORD}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>20\d\d)", re.I),
    re.compile(
        rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_WORD},?\s+(?P<year>20\d\d)", re.I
    ),
    re.compile(r"(?P<year>20\d\d)-(?P<mnum>\d{1,2})-(?P<day>\d{1,2})"),
)
_NUMERIC_DATE = re.compile(r"\b(?P<a>\d{1,2})[/.](?P<b>\d{1,2})[/.](?P<year>20\d\d)\b")


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_deadline(text: str) -> tuple[date | None, str | None, str | None]:
    """(deadline, verbatim line, warning). Only from lines that announce a deadline."""
    for line in text.splitlines():
        if not _DEADLINE_CUE.search(line):
            continue
        for pattern in _DATE_PATTERNS:
            if m := pattern.search(line):
                month = int(m.group("mnum")) if "mnum" in m.groupdict() and m.group("mnum") else \
                    _MONTHS[m.group("month")[:3].lower()]  # fmt: skip
                return _date(int(m.group("year")), month, int(m.group("day"))), line.strip(), None
        if m := _NUMERIC_DATE.search(line):
            a, b, year = int(m.group("a")), int(m.group("b")), int(m.group("year"))
            if a > 12 >= b:  # day/month
                return _date(year, b, a), line.strip(), None
            if b > 12 >= a:  # month/day
                return _date(year, a, b), line.strip(), None
            return (
                None,
                line.strip(),
                (
                    f"The deadline {m.group(0)!r} could be day/month or month/day, "
                    "so it was not set."
                ),
            )
    return None, None, None


# --- Work mode and employment type --------------------------------------------------------

_WORK_MODES = (
    (
        WorkplaceType.REMOTE,
        r"\b(?:fully[- ])?remote\b(?![- ]?(?:teams?|colleagues|meetings?))"
        r"|\bwork from home\b|\bwfh\b",
    ),
    (WorkplaceType.HYBRID, r"\bhybrid\b"),
    (WorkplaceType.ONSITE, r"\bon[- ]?site\b|\bin[- ]office\b|\bwork from office\b|\bwfo\b"),
)  # fmt: skip
_EMPLOYMENT = (
    (EmploymentType.INTERNSHIP, r"\binternships?\b|\bintern\b"),
    (EmploymentType.PART_TIME, r"\bpart[- ]time\b"),
    (EmploymentType.FULL_TIME, r"\bfull[- ]time\b|\bpermanent\b"),
    (EmploymentType.CONTRACT, r"\bcontract(?:or|ual)?\b|\btemporary\b|\bfixed[- ]term\b"),
    (EmploymentType.FREELANCE, r"\bfreelance\b"),
    (EmploymentType.VOLUNTEER, r"\bvolunteer\b"),
)


def detect_one[T](text: str, options: tuple[tuple[T, str], ...]) -> tuple[T | None, list[T]]:
    """The single option mentioned in ``text``; None if none or several are mentioned."""
    found = [value for value, pattern in options if re.search(pattern, text, re.IGNORECASE)]
    return (found[0] if len(found) == 1 else None), found


def work_mode(text: str) -> tuple[WorkplaceType | None, list[WorkplaceType]]:
    mode, found = detect_one(text, _WORK_MODES)
    # A hybrid role describes its office days ("3 days in office"); that is not a conflict.
    if set(found) == {WorkplaceType.HYBRID, WorkplaceType.ONSITE}:
        return WorkplaceType.HYBRID, found
    return mode, found


def employment_type(text: str) -> tuple[EmploymentType | None, list[EmploymentType]]:
    return detect_one(text, _EMPLOYMENT)
