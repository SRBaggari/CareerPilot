"""Question understanding: what an application question asks for, and what to retrieve.

Deterministic, so the candidate can see (and trust) how each question was read.
"""

import re
from dataclasses import dataclass

from app.documents.models import QuestionType
from app.jobs.analysis.vocabulary import find_technologies


@dataclass(frozen=True)
class Understanding:
    question_type: QuestionType
    focus: str | None  # the skill or technology a skill question is about
    summary: str  # how the question was read, shown to the candidate
    query: str  # what evidence retrieval searches for


_FIT = re.compile(
    r"why should (we|i|they) hire|good fit|right fit|best fit|best candidate|right candidate|"
    r"what (can|would|will) you bring|what makes you|why (are )?you (the )?(best|right|ideal)|"
    r"stand out|qualif(y|ied|ies) you|strongest qualifications",
    re.IGNORECASE,
)
_MOTIVATION = re.compile(
    r"why .*\b(interested|apply|applying|want|join|pursue)\b|what (attracts|interests|excites|"
    r"draws|motivates) you|why (this|our) (role|company|team|position|job)|motivat|"
    r"interest(ed)? in (this|the|our) (role|position|company|job)",
    re.IGNORECASE,
)
_BEHAVIORAL = re.compile(
    r"describe a (time|situation)|tell (us|me) about a (time|situation)|give (us |me )?an "
    r"example|challeng|conflict|mistake|failure|disagree|difficult",
    re.IGNORECASE,
)
_PROJECT = re.compile(r"\bprojects?\b", re.IGNORECASE)
_SKILL = re.compile(
    r"experience (with|in|using)|familiar(ity)? with|proficien|how (have|do) you use|"
    r"knowledge of|skills? (in|with)|worked with|expertise (in|with)|comfortable with",
    re.IGNORECASE,
)
_SKILL_TARGET = re.compile(
    r"(?:experience (?:with|in|using)|familiar(?:ity)? with|proficien\w* (?:in|with)|"
    r"knowledge of|skills? (?:in|with)|worked with|expertise (?:in|with)|use|using|"
    r"comfortable with)\s+([A-Za-z][\w+#.-]*(?:\s[A-Z][\w+#.-]*)?)",
    re.IGNORECASE,
)
_EXPERIENCE = re.compile(
    r"experience|background|about yourself|relevant work|work history|career|previous role",
    re.IGNORECASE,
)


def understand(question: str, job_title: str) -> Understanding:
    text = " ".join(question.split())
    technologies = find_technologies(text)
    if _SKILL.search(text) or (technologies and re.search(r"experience|used|use", text, re.I)):
        target = _SKILL_TARGET.search(text)
        focus = (
            technologies[0] if technologies else (target.group(1).rstrip(".?") if target else None)
        )
        if focus:
            return Understanding(
                QuestionType.SKILL,
                focus,
                f"Asks about your experience with {focus}.",
                f"{focus} experience {focus}",
            )
    if _FIT.search(text):
        return Understanding(
            QuestionType.FIT,
            None,
            "Asks why you are a good fit: answered with your evidence that matches the job.",
            f"{job_title} {text}",
        )
    if _MOTIVATION.search(text):
        return Understanding(
            QuestionType.MOTIVATION,
            None,
            "Asks why you are interested: your interest, plus the experience that relates to it.",
            f"{job_title} {text}",
        )
    if _BEHAVIORAL.search(text):
        return Understanding(
            QuestionType.BEHAVIORAL,
            None,
            "Asks about a specific situation: answered only with what your evidence describes.",
            text,
        )
    if _PROJECT.search(text):
        return Understanding(
            QuestionType.PROJECT,
            None,
            "Asks you to describe a project relevant to this job.",
            f"project {job_title} {text}",
        )
    if _EXPERIENCE.search(text):
        return Understanding(
            QuestionType.EXPERIENCE,
            None,
            "Asks about your relevant experience.",
            f"{job_title} {text}",
        )
    return Understanding(QuestionType.OTHER, None, "A general question.", f"{job_title} {text}")


SUMMARIES: dict[QuestionType, str] = {
    QuestionType.FIT: "Asks why you are a good fit: answered with your evidence that matches "
    "the job.",
    QuestionType.MOTIVATION: "Asks why you are interested: your interest, plus the experience "
    "that relates to it.",
    QuestionType.BEHAVIORAL: "Asks about a specific situation: answered only with what your "
    "evidence describes.",
    QuestionType.PROJECT: "Asks you to describe a project relevant to this job.",
    QuestionType.EXPERIENCE: "Asks about your relevant experience.",
    QuestionType.OTHER: "A general question.",
}


def summary_of(question_type: QuestionType, focus: str | None) -> str:
    if question_type == QuestionType.SKILL and focus:
        return f"Asks about your experience with {focus}."
    return SUMMARIES.get(question_type, "A general question.")
