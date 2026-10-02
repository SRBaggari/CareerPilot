"""Evidence comparison: does the evidence a claim rests on actually say what the claim says?

Deterministic and conservative. The checks target the ways generated text goes wrong:

- **Metrics, numbers and scale** (40%, 10,000 users, "millions") must appear in the
  evidence. A different number for the same thing (35% vs 60%) is a contradiction.
- **Named things** (employers, products, certifications) must appear in the evidence.
- **Role escalation** ("led", "managed", "architected", "senior") needs the same wording in
  the evidence, so responsibilities can't be exaggerated.
- **Technologies** named in the claim must be named in the evidence.
- **Qualifiers** ("production", "scalable", "enterprise") must appear in the evidence.
- **Content coverage**: most of the claim's substantive words must come from the evidence.

Findings of the first kinds are *vetoes*: hard factual failures that no later reviewer
(including an LLM) may overrule. Coverage alone is not a veto, because faithful paraphrase
can use different words; an LLM reviewer may judge those, and must cite evidence to do so.

Invented specifics (numbers, scale, names, a bigger role) make a claim UNSUPPORTED: its
substance rests on them. Extra technologies, qualifiers or wording on top of a supported
core make it PARTIALLY_SUPPORTED.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.documents.models import VerificationVerdict
from app.jobs.analysis.vocabulary import find_technologies

V = VerificationVerdict


class ClaimKind(StrEnum):
    STATEMENT = "statement"  # bullets, summary sentences, free text
    SKILL = "skill"  # a bare skill name


@dataclass(frozen=True)
class EvidenceText:
    content: str  # the verbatim evidence statement
    context: str = ""  # the item it belongs to ("Machine Learning Intern at Acme")


@dataclass(frozen=True)
class Comparison:
    verdict: VerificationVerdict
    reason: str
    confidence: float
    veto: bool = False  # a hard factual failure that no reviewer may overrule

    @property
    def supported(self) -> bool:
        return self.verdict == V.SUPPORTED


SUPPORTED_COVERAGE = 0.7
PARTIAL_COVERAGE = 0.45
SAME_TOPIC_COVERAGE = 0.5  # how alike a claim and evidence must be to conflict on a number

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
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "dozen": "12",
    "score": "20",
    "hundred": "100",
    "thousand": "1000",
    "million": "1000000",
    "billion": "1000000000",
}
_VAGUE_QUANTITIES = re.compile(
    r"\b(dozens|hundreds|thousands|millions|billions|several|numerous|multiple|countless)\b",
    re.IGNORECASE,
)
# Outcomes, recognition and sole ownership: each is a fact of its own that the evidence must
# state, however much of the rest of the sentence it covers ("my team won ...").
_OUTCOMES = frozenset(
    {"won", "win", "wins", "winner", "winning", "award", "awarded", "awards", "prize",
     "champion", "championship", "finalist", "medal", "ranked", "rank", "top-ranked",
     "alone", "single-handedly", "singlehandedly", "solely", "solo", "sole", "adopted",
     "published", "patent", "patented", "recognized", "recognised", "promoted", "acquired",
     "funded", "revenue", "profit", "profitable", "customers", "users", "certified",
     "accepted", "featured", "selected", "shortlisted", "hired", "nominated"}
)  # fmt: skip
# Words that deny the statement they belong to ("not deployed to production").
_NEGATIONS = frozenset(
    {"not", "never", "no", "without", "didn't", "wasn't", "weren't", "isn't", "aren't",
     "hasn't", "haven't", "won't", "cannot", "can't", "couldn't", "nor", "neither"}
)  # fmt: skip
# Single words that deny an action, mapped to the stem of the action they deny.
_DENYING_WORDS = {
    "unreleased": "releas",
    "undeployed": "deploy",
    "unshipped": "shipp",
    "unpublished": "publish",
    "unlaunched": "launch",
}
_QUALIFIERS = re.compile(
    r"\b(production|production-grade|production-ready|scalable|enterprise|enterprise-grade|"
    r"large-scale|high-traffic|high-performance|mission-critical|world-class|cutting-edge|"
    r"state-of-the-art|award-winning|industry-leading|robust|highly|significantly|"
    r"dramatically|massive|massively|global|flagship|critical)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\.\d+)?")
_NUMBER_WITH_UNIT = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)*(?:\.\d+)?)\s*(%|x\b|k\b|\+?\s*[a-z]+)", re.IGNORECASE
)
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
    "director": "direct",
    "supervised": "supervise",
    "oversaw": "oversee",
    "owned": "own",
    "architected": "architect",
    "architect": "architect",
    "founded": "found",
    "cofounded": "found",
    "co-founded": "found",
    "principal": "principal",
    "senior": "senior",
    "staff": "staff",
    "expert": "expert",
    "chief": "chief",
    "mentored": "mentor",
    "trained": "train",
    "pioneered": "pioneer",
    "invented": "invent",
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
        "have",
        "has",
        "had",
    ]
)
# Words that only describe *how* something was done; rewording them adds no new fact.
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
        "based",
        "powered",
        "driven",
    ]
)
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|[:;]\s+)([A-Z][\w'-]*)")
# Generic self-praise: presented as fact, it needs evidence like any other claim.
_TRAITS = re.compile(
    r"\b(passionate|hard[- ]?working|detail[- ]oriented|results[- ](?:driven|oriented)|"
    r"self[- ](?:motivated|starter)|motivated|dedicated|team[- ]player|quick learner|"
    r"fast learner|go[- ]getter|proactive|dynamic|innovative|creative|visionary|talented|"
    r"exceptional|outstanding|excellent|strong|proven|track record|extensive|seasoned|"
    r"skilled|proficient|adept|expertise|deep knowledge|world[- ]class|rockstar|ninja|"
    r"communication skills|interpersonal skills|leadership skills|problem[- ]solver)\b",
    re.IGNORECASE,
)
# Openers of sentences that state intent, interest or courtesy rather than facts.
_INTENT_OPENERS = re.compile(
    r"^\s*(dear\b|sincerely|best regards|kind regards|regards|yours|thank you|thanks\b|"
    r"i am writing|i'm writing|i am applying|i'm applying|i am excited|i'm excited|"
    r"i am eager|i'm eager|i am interested|i'm interested|i would|i'd\b|i look forward|"
    r"i hope|i welcome|please)",
    re.IGNORECASE,
)
# Capitalized words a salutation may use ("Dear Northwind Hiring Team,").
_SALUTATION_WORDS = frozenset(
    {"hiring", "team", "manager", "committee", "recruiter", "recruiting", "sir", "madam",
     "talent", "acquisition", "whom", "it", "may", "concern", "the", "people"}
)  # fmt: skip
_SIGN_OFF_WORDS = frozenset(
    {"dear", "sincerely", "best", "kind", "regards", "yours", "faithfully", "truly", "and",
     "of", "at", "for"}
)  # fmt: skip
# Every word a sentence may use and still claim nothing about the candidate (besides the
# job title, the company and salutation words). Deliberately small: a sentence with any
# other word is treated as a factual claim.
_COURTESY_WORDS = frozenset(
    {"i", "i'm", "i'd", "am", "me", "my", "you", "your", "we", "us", "our", "it", "this",
     "that", "a", "an", "the", "to", "for", "of", "at", "with", "in", "on", "about", "from",
     "and", "or", "as", "be", "is", "would", "will", "could", "welcome", "like", "love",
     "appreciate", "appreciated", "writing", "write", "apply", "applying", "application",
     "position", "role", "opening", "opportunity", "chance", "excited", "eager",
     "interested", "pleased", "happy", "glad", "delighted", "thank", "thanks", "time",
     "consideration", "considering", "discuss", "discussing", "speak", "talk", "meet",
     "conversation", "interview", "look", "looking", "forward", "hearing", "hear",
     "hope", "please", "further", "more", "learn", "soon", "attached", "enclosed",
     "find", "resume", "cover", "letter", "review", "reviewing", "dear", "sincerely",
     "regards", "best", "kind", "warm", "warmly", "respectfully", "yours", "faithfully",
     "truly", "very", "much", "again", "convenience", "earliest", "next", "steps",
     "join", "joining", "contact", "reach", "available", "availability"}
)  # fmt: skip
# Wording that turns a sentence into a statement about the candidate's past or abilities.
_FACT_WORDING = re.compile(
    r"\b(?:i|i've|we)\s+(?:have|had|'ve|built|led|developed|created|designed|managed|"
    r"worked|implemented|deployed|delivered|achieved|won|earned|hold|completed|graduated|"
    r"studied|shipped|launched|reduced|increased|improved|trained|mentored|taught|wrote|"
    r"published|founded|own|owned|bring|brought|can|know|am\s+(?:a|an|the)\b)|"
    r"\bmy\s+(?:experience|background|work|skills?|expertise|track record|projects?|"
    r"role|achievements?|degree|studies|research|internship|knowledge|ability)\b",
    re.IGNORECASE,
)


def _norm_number(raw: str) -> str:
    n = raw.replace(",", "")
    return n.rstrip("0").rstrip(".") if "." in n else n


def numbers(text: str) -> set[str]:
    found = {_norm_number(n) for n in _NUMBER_RE.findall(text)}
    found |= {_NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in _NUMBER_WORDS}
    return found


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _unit(raw: str) -> str:
    unit = raw.lstrip("+ ").lower()
    return unit if unit in ("%", "x", "k") else _stem(unit)


@dataclass(frozen=True)
class _Quantity:
    number: str
    unit: str  # "%", "x", "k" or the stem of the word after the number ("question")
    written: str  # as written, for messages ("200 questions")
    about: frozenset[str]  # content stems just before it: what the number measures


_CLAUSE_BREAK = re.compile(r"[,;:()\n]|\.\s|\band\b|\bwhile\b")
_ABOUT_WORDS = 4
_AFTER_WORDS = 3


def _quantities(text: str) -> list[_Quantity]:
    """Numbers with their unit and what they measure, e.g. 35% about {latency, inference}."""
    found = []
    for match in _NUMBER_WITH_UNIT.finditer(text):
        raw_number, raw_unit = match.group(1), match.group(2)
        unit = _unit(raw_unit)
        clause = _CLAUSE_BREAK.split(text[: match.start()])[-1]
        # What a number measures is named around it: "accuracy of 92%" or "92% validation
        # accuracy". The words after it stay within the same clause.
        after = _CLAUSE_BREAK.split(text[match.end() :])[0]
        about = frozenset(
            content_stems(" ".join(words(clause)[-_ABOUT_WORDS:]))
            | content_stems(" ".join(words(after)[:_AFTER_WORDS]))
        )
        written = (
            f"{raw_number}{raw_unit}" if unit in ("%", "x", "k")
            else f"{raw_number} {raw_unit.strip('+ ')}"
        )  # fmt: skip
        found.append(_Quantity(_norm_number(raw_number), unit, written, about))
    return found


def _number_check(text: str, support: str, coverage: float) -> Comparison | None:
    """Numbers are compared by what they measure, so a metric can't be borrowed from an
    unrelated statement, and a different value for the same thing is a contradiction."""
    recorded = _quantities(support)
    for q in _quantities(text):

        def same_thing(r: _Quantity, q: _Quantity = q) -> bool:
            if r.unit != q.unit:
                return False
            if q.about and r.about:
                return bool(q.about & r.about)
            return coverage >= SAME_TOPIC_COVERAGE

        related = [r for r in recorded if same_thing(r)]
        if any(r.number == q.number for r in related):
            continue
        if related:
            return Comparison(
                V.CONTRADICTED, f"Your evidence says {related[0].written}, not {q.written}.",
                0.0, True,
            )  # fmt: skip
        elsewhere = [r for r in recorded if r.number == q.number and r.unit == q.unit]
        if elsewhere and q.about:
            return Comparison(
                V.UNSUPPORTED,
                f"Your evidence gives {q.written} for something else, not for "
                f"{' '.join(sorted(q.about))}.",
                0.0,
                True,
            )
    return None


def words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9+#']*", text.lower())


def content_stems(text: str) -> set[str]:
    return {
        _stem(w) for w in words(text) if len(w) >= 3 and w not in _STOPWORDS and w not in _GENERIC
    }


def _proper_nouns(text: str) -> set[str]:
    """Capitalized words that aren't sentence-initial (names of employers, products, ...)."""
    starts = {m.group(1) for m in _SENTENCE_START.finditer(text)}
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9&.+-]*[A-Za-z0-9+]\b|\b[A-Z]{2,}\b", text)
    names: set[str] = set()
    for token in tokens:
        if token in starts:
            continue
        # "RAG-based": only the capitalized parts name something.
        names |= {part for part in token.split("-") if part[:1].isupper()}
    return names


def _coverage(claim_stems: set[str], support_stems: set[str]) -> float:
    return len(claim_stems & support_stems) / len(claim_stems) if claim_stems else 0.0


_NEGATION_SCOPE = 3  # words after a negation that it denies


def _negates(word: str) -> bool:
    return word in _NEGATIONS or word.endswith("n't")


def _denied(text: str, evidence: list[EvidenceText]) -> str | None:
    """The first thing the claim asserts that the evidence explicitly denies, if any.

    "Prototype chatbot; not deployed to production." denies "deploy", so the claim
    "Deployed the chatbot to production." contradicts it, unless the claim is itself
    negative or another evidence statement affirms the same thing.
    """
    claim_words = words(text)
    if any(_negates(w) for w in claim_words):
        return None
    claim_stems = {_stem(w) for w in claim_words}
    denied: list[str] = []
    affirmed: set[str] = set()
    for item in evidence:
        for statement in re.split(r"[.;:!?\n]+", f"{item.context}\n{item.content}"):
            tokens = words(statement)
            scoped: set[int] = set()
            for i, token in enumerate(tokens):
                if token in _DENYING_WORDS:
                    denied.append(_DENYING_WORDS[token])
                    scoped.add(i)
                elif _negates(token):
                    # The first content word after the negation is what it denies.
                    after = [
                        j
                        for j in range(i + 1, min(len(tokens), i + 1 + _NEGATION_SCOPE))
                        if len(tokens[j]) >= 3 and tokens[j] not in _STOPWORDS
                    ]
                    if after:
                        scoped.add(after[0])
                        denied.append(_stem(tokens[after[0]]))
            affirmed |= {_stem(t) for j, t in enumerate(tokens) if j not in scoped}
    for stem in denied:
        if stem in claim_stems and stem not in affirmed:
            return stem
    return None


def _names_skill(skill: str, support: str) -> bool:
    """Whether ``support`` names ``skill`` as a word. A known technology must be found as
    one (by the caller). Short names ("Go", "R", "C") are also ordinary words, so they must
    match with the same capitalization and not just start a sentence ("go live" isn't Go)."""
    if find_technologies(skill) == [skill]:
        return False
    if len(skill) > 3:
        pattern = rf"(?<![\w]){re.escape(skill)}(?![\w])"
        return re.search(pattern, support, re.IGNORECASE) is not None
    for m in re.finditer(rf"(?<![\w]){re.escape(skill)}(?![\w+#])", support):
        before = support[: m.start()].rstrip(" \t")
        if before and before[-1] not in ".!?:;\n":
            return True
    return False


def compare(text: str, evidence: list[EvidenceText], kind: ClaimKind) -> Comparison:
    if not evidence:
        return Comparison(V.UNSUPPORTED, "No verified evidence supports this claim.", 0.0, True)
    support = "\n".join(f"{e.context}\n{e.content}" for e in evidence)
    support_lower = support.lower()

    if kind == ClaimKind.SKILL:
        named = text in find_technologies(support) or _names_skill(text, support)
        if named:
            return Comparison(V.SUPPORTED, f"The evidence names {text}.", 1.0)
        return Comparison(V.UNSUPPORTED, f"The evidence doesn't mention {text}.", 0.0, True)

    claim_stems = content_stems(text)
    support_stems = content_stems(support)
    coverage = _coverage(claim_stems, support_stems)

    if (numbers_found := _number_check(text, support, coverage)) is not None:
        return numbers_found

    extra_numbers = numbers(text) - numbers(support)
    if extra_numbers:
        listed = ", ".join(sorted(extra_numbers))
        return Comparison(
            V.UNSUPPORTED, f"Contains numbers or metrics not in the evidence: {listed}.", 0.0, True
        )
    vague = {m.lower() for m in _VAGUE_QUANTITIES.findall(text)} - set(words(support))
    if vague:
        listed = ", ".join(sorted(vague))
        return Comparison(V.UNSUPPORTED, f"Adds a scale not in the evidence: {listed}.", 0.0, True)

    support_words = set(words(support))
    hyphenated = re.compile(r"[a-z][a-z0-9+#'-]*")  # keeps "single-handedly" whole
    support_word_stems = {_stem(w) for w in support_words | set(hyphenated.findall(support_lower))}
    outcomes = sorted(
        {
            w
            for w in hyphenated.findall(text.lower()) + words(text)
            if w in _OUTCOMES and _stem(w) not in support_word_stems
        }
    )
    if outcomes:
        listed = ", ".join(outcomes)
        return Comparison(
            V.UNSUPPORTED, f"Claims an outcome the evidence doesn't state: {listed}.", 0.0, True
        )
    if denied := _denied(text, evidence):
        return Comparison(
            V.CONTRADICTED, f"The evidence says this did not happen: {denied}.", 0.0, True
        )

    escalated = {w for w in words(text) if w in _ESCALATION}
    support_roles = {_ESCALATION[w] for w in support_words if w in _ESCALATION}
    overstated = sorted(w for w in escalated if _ESCALATION[w] not in support_roles)
    if overstated:
        listed = ", ".join(overstated)
        return Comparison(
            V.UNSUPPORTED, f"Overstates the role ({listed}) compared with the evidence.", 0.0, True
        )

    technologies = set(find_technologies(text))
    unknown_names = sorted(
        n for n in _proper_nouns(text) if n.lower() not in support_lower and n not in technologies
    )
    if unknown_names:
        listed = ", ".join(unknown_names)
        return Comparison(V.UNSUPPORTED, f"Names things not in the evidence: {listed}.", 0.0, True)

    extra_tech = technologies - set(find_technologies(support))
    if extra_tech:
        listed = ", ".join(sorted(extra_tech))
        verdict = V.PARTIALLY_SUPPORTED if coverage >= PARTIAL_COVERAGE else V.UNSUPPORTED
        return Comparison(
            verdict, f"Names technologies not in the evidence: {listed}.", coverage / 2, True
        )

    traits = sorted({t.lower() for t in _TRAITS.findall(text)} - set(support_lower.split()))
    traits = [t for t in traits if t not in support_lower]
    if traits:
        listed = ", ".join(traits)
        return Comparison(
            V.UNSUPPORTED,
            f"Presents a generic quality as fact ({listed}); your evidence doesn't show it.",
            0.0,
            True,
        )

    qualifiers = {q.lower() for q in _QUALIFIERS.findall(text)} - support_words
    qualifiers = {q for q in qualifiers if q not in support_lower}
    if qualifiers:
        listed = ", ".join(sorted(qualifiers))
        verdict = V.PARTIALLY_SUPPORTED if coverage >= PARTIAL_COVERAGE else V.UNSUPPORTED
        return Comparison(
            verdict, f"Adds qualifiers the evidence doesn't: {listed}.", coverage / 2, True
        )

    if not claim_stems:
        return Comparison(V.UNSUPPORTED, "The claim has no checkable content.", 0.0)
    if coverage >= SUPPORTED_COVERAGE:
        return Comparison(V.SUPPORTED, "Supported by the evidence.", round(coverage, 2))
    new = ", ".join(sorted(claim_stems - support_stems)[:8])
    verdict = V.PARTIALLY_SUPPORTED if coverage >= PARTIAL_COVERAGE else V.UNSUPPORTED
    return Comparison(
        verdict, f"Adds wording not backed by the evidence: {new}.", round(coverage, 2)
    )


def is_non_factual(text: str, allowed_names: set[str]) -> bool:
    """True for a greeting, statement of intent or courtesy that claims nothing about the
    candidate ("I am writing to apply for the ML Engineer role at Northwind.").

    Deliberately strict: numbers, technologies, generic qualities, statements about the
    candidate's past or abilities, or names other than ``allowed_names`` (the job title
    and company) make it a factual claim, which then needs evidence.
    """
    if not _INTENT_OPENERS.match(text):
        return False
    if numbers(text) or _TRAITS.search(text) or _FACT_WORDING.search(text):
        return False
    if _QUALIFIERS.search(text) or _VAGUE_QUANTITIES.search(text):
        return False
    allowed_words = {w.lower() for name in allowed_names for w in re.findall(r"[\w'&.+-]+", name)}
    allowed_words |= _SALUTATION_WORDS
    # Role words are fine when they are the job's own title ("Senior ... Engineer").
    if any(w in _ESCALATION and w not in allowed_words for w in words(text)):
        return False
    # A salutation or sign-off names the company or role and nothing else.
    if re.match(r"\s*(dear|sincerely|best regards|kind regards|regards|yours)\b", text, re.I):
        extra = set(words(text)) - allowed_words - _SIGN_OFF_WORDS
        if extra:
            return False
    allowed_tech = {t for name in allowed_names for t in find_technologies(name)}
    if set(find_technologies(text)) - allowed_tech:
        return False
    names = {
        n
        for n in _proper_nouns(text)
        if n != "I" and n.lower().removesuffix("'s").removesuffix("'") not in allowed_words
    }
    if names:
        return False
    # Allow-list, not block-list: every word must be courtesy or intent vocabulary, or part
    # of the job title and company. Anything else ("I'd bring ...", "who has shipped ...",
    # "my team won ...") is a claim about the candidate and needs evidence.
    possessive = {w.removesuffix("'s") for w in words(text)}
    return not (possessive - allowed_words - _SIGN_OFF_WORDS - _COURTESY_WORDS)
