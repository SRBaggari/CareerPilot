"""Claim verification: is a generated claim supported by the evidence it cites?

Deterministic and conservative: a claim is kept only if every check passes. Checks target
the ways generated text goes wrong:

- **Metrics and numbers** (40%, 5 years, 200 users, 2023) must appear in the evidence.
- **Technologies and skills** named in the claim must be named in the evidence.
- **Proper nouns** (employers, products, project names) must appear in the evidence.
- **Role escalation** ("led", "managed", "architected", "senior") needs the same wording
  in the evidence, so responsibilities can't be exaggerated.
- **Content coverage**: most of the claim's substantive words must come from the evidence,
  so a "rewording" can't add new facts or embellishments.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.documents.models import VerificationVerdict
from app.jobs.analysis.vocabulary import find_technologies


class ClaimKind(StrEnum):
    BULLET = "bullet"
    SUMMARY = "summary"
    SKILL = "skill"


@dataclass(frozen=True)
class EvidenceText:
    content: str  # the verbatim evidence statement
    context: str = ""  # the item it belongs to ("Machine Learning Intern at Acme")


@dataclass(frozen=True)
class Verification:
    verdict: VerificationVerdict
    rationale: str
    confidence: float

    @property
    def supported(self) -> bool:
        return self.verdict == VerificationVerdict.SUPPORTED


SUPPORTED_COVERAGE = 0.7
PARTIAL_COVERAGE = 0.45

_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "twenty": "20",
    "hundred": "100",
    "thousand": "1000",
    "million": "1000000",
}
_VAGUE_QUANTITIES = re.compile(
    r"\b(dozens|hundreds|thousands|millions|several|numerous|multiple)\b", re.IGNORECASE
)
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\.\d+)?")
_ESCALATION = {
    "led": "lead",
    "lead": "lead",
    "leading": "lead",
    "leader": "lead",
    "managed": "manage",
    "manage": "manage",
    "managing": "manage",
    "manager": "manage",
    "headed": "head",
    "head": "head",
    "spearheaded": "spearhead",
    "directed": "direct",
    "supervised": "supervise",
    "oversaw": "oversee",
    "owned": "own",
    "architected": "architect",
    "founded": "found",
    "cofounded": "found",
    "principal": "principal",
    "senior": "senior",
    "expert": "expert",
    "chief": "chief",
    "mentored": "mentor",
    "trained": "train",
}
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "into",
        "over",
        "under",
        "via",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "our",
        "my",
        "your",
        "his",
        "her",
        "they",
        "we",
        "i",
        "you",
        "he",
        "she",
        "which",
        "who",
        "whom",
        "whose",
        "than",
        "then",
        "also",
        "both",
        "each",
        "such",
        "very",
        "more",
        "most",
        "less",
        "using",
        "use",
        "used",
        "across",
        "within",
        "through",
        "per",
        "about",
        "after",
        "before",
        "while",
        "during",
    ]
)
# Verbs that only describe *how* something was done; rewording them adds no new fact.
_GENERIC = frozenset(
    [
        "built",
        "build",
        "building",
        "developed",
        "develop",
        "developing",
        "created",
        "create",
        "creating",
        "implemented",
        "implement",
        "implementing",
        "designed",
        "design",
        "designing",
        "delivered",
        "deliver",
        "worked",
        "work",
        "working",
        "applied",
        "apply",
        "leveraged",
        "leverage",
        "utilized",
        "utilize",
        "wrote",
        "write",
        "writing",
        "made",
        "make",
        "making",
        "engineered",
        "engineer",
        "produced",
        "produce",
        "performed",
        "perform",
        "set",
        "up",
    ]
)
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|[:;]\s+)([A-Z][\w'-]*)")


def _numbers(text: str) -> set[str]:
    found = {
        n.replace(",", "").rstrip("0").rstrip(".") if "." in n else n.replace(",", "")
        for n in _NUMBER_RE.findall(text)
    }
    found |= {_NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in _NUMBER_WORDS}
    return found


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9+#'-]*", text.lower())


def _content_stems(text: str) -> set[str]:
    return {
        _stem(w) for w in _words(text) if len(w) >= 3 and w not in _STOPWORDS and w not in _GENERIC
    }


def _proper_nouns(text: str) -> set[str]:
    """Capitalized words that aren't sentence-initial (names of employers, products, ...)."""
    starts = {m.group(1) for m in _SENTENCE_START.finditer(text)}
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9&.+-]*[A-Za-z0-9+]\b|\b[A-Z]{2,}\b", text)
    return {t for t in tokens if t not in starts}


def verify_claim(text: str, evidence: list[EvidenceText], kind: ClaimKind) -> Verification:
    V = VerificationVerdict
    if not evidence:
        return Verification(V.UNSUPPORTED, "The claim cites no evidence.", 0.0)
    support = "\n".join(f"{e.context}\n{e.content}" for e in evidence)
    support_lower = support.lower()

    if kind == ClaimKind.SKILL:
        named = text in find_technologies(support) or re.search(
            rf"(?<![\w]){re.escape(text.lower())}(?![\w])", support_lower
        )
        if named:
            return Verification(V.SUPPORTED, f"The evidence names {text}.", 1.0)
        return Verification(V.UNSUPPORTED, f"The cited evidence doesn't mention {text}.", 0.0)

    extra_numbers = _numbers(text) - _numbers(support)
    if extra_numbers:
        return Verification(
            V.UNSUPPORTED,
            "Contains numbers or metrics not in the evidence: "
            + ", ".join(sorted(extra_numbers))
            + ".",
            0.0,
        )
    vague = {m.lower() for m in _VAGUE_QUANTITIES.findall(text)} - set(_words(support))
    if vague:
        return Verification(
            V.UNSUPPORTED, f"Adds a quantity not in the evidence: {', '.join(sorted(vague))}.", 0.0
        )

    extra_tech = set(find_technologies(text)) - set(find_technologies(support))
    if extra_tech:
        return Verification(
            V.UNSUPPORTED,
            "Names technologies not in the evidence: " + ", ".join(sorted(extra_tech)) + ".",
            0.0,
        )

    support_words = set(_words(support))
    escalated = {w for w in _words(text) if w in _ESCALATION}
    support_roles = {_ESCALATION[w] for w in support_words if w in _ESCALATION}
    overstated = sorted(w for w in escalated if _ESCALATION[w] not in support_roles)
    if overstated:
        return Verification(
            V.UNSUPPORTED,
            "Overstates the role (" + ", ".join(overstated) + ") compared with the evidence.",
            0.0,
        )

    unknown_names = sorted(n for n in _proper_nouns(text) if n.lower() not in support_lower)
    if unknown_names:
        return Verification(
            V.UNSUPPORTED,
            "Names things not in the evidence: " + ", ".join(unknown_names) + ".",
            0.0,
        )

    claim_stems = _content_stems(text)
    if not claim_stems:
        return Verification(V.PARTIALLY_SUPPORTED, "The claim has no checkable content.", 0.5)
    support_stems = _content_stems(support)
    coverage = len(claim_stems & support_stems) / len(claim_stems)
    if coverage >= SUPPORTED_COVERAGE:
        return Verification(V.SUPPORTED, "Supported by the cited evidence.", round(coverage, 2))
    new = sorted(claim_stems - support_stems)
    verdict = V.PARTIALLY_SUPPORTED if coverage >= PARTIAL_COVERAGE else V.UNSUPPORTED
    return Verification(
        verdict,
        "Adds wording not backed by the evidence: " + ", ".join(new[:8]) + ".",
        round(coverage, 2),
    )
