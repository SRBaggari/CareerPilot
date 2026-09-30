"""Application answers without a database: question understanding and rule-based answers,
which only ever restate the candidate's evidence."""

import uuid

import pytest

from app.documents.answers.generator import RuleAnswerGenerator, fit_to_words, mentions
from app.documents.answers.questions import understand
from app.documents.cover_letter.content import LetterSentence
from app.documents.models import QuestionType
from app.documents.resume.workspace import EvidenceItem, Workspace
from app.jobs.models import Job
from app.profiles.models import CandidateProfile, Project, WorkExperience

Q = QuestionType


@pytest.mark.parametrize(
    ("question", "kind", "focus"),
    [
        ("Why are you interested in this role?", Q.MOTIVATION, None),
        ("What attracts you to our company?", Q.MOTIVATION, None),
        ("Why do you want to join Northwind?", Q.MOTIVATION, None),
        ("Describe a relevant project.", Q.PROJECT, None),
        ("Tell us about a project you are proud of.", Q.PROJECT, None),
        ("Why should we hire you?", Q.FIT, None),
        ("What makes you a good fit for this team?", Q.FIT, None),
        ("What would you bring to the role?", Q.FIT, None),
        ("Describe your experience with Python.", Q.SKILL, "Python"),
        ("How have you used Docker?", Q.SKILL, "Docker"),
        ("What is your experience with Kubernetes?", Q.SKILL, "Kubernetes"),
        ("Are you familiar with LangGraph?", Q.SKILL, "LangGraph"),
        ("Describe a time you resolved a conflict.", Q.BEHAVIORAL, None),
        ("Tell us about your background.", Q.EXPERIENCE, None),
        ("What is your favourite colour?", Q.OTHER, None),
    ],
)
def test_questions_are_understood(question: str, kind: QuestionType, focus: str | None) -> None:
    u = understand(question, "ML Engineer")
    assert (u.question_type, u.focus) == (kind, focus)
    assert u.summary and u.query


# --- Rule-based answers -----------------------------------------------------------------

DOCKER, LATENCY, RAG, PYTHON = (uuid.uuid4() for _ in range(4))


def workspace() -> Workspace:
    profile = CandidateProfile(id=uuid.uuid4(), full_name="Test Candidate")
    work = WorkExperience(
        id=uuid.uuid4(), title="Machine Learning Intern", company_name="Acme Analytics"
    )
    project = Project(id=uuid.uuid4(), title="Multi-Agent Research Assistant")
    profile.work_experiences, profile.projects, profile.skills = [work], [project], []
    job = Job(title="ML Engineer", company_name="Northwind")
    job.requirements = []
    ws = Workspace(profile=profile, job=job)
    for evidence_id, content, subject, context, relevance in (
        (
            DOCKER,
            "Deployed ML models with Docker on AWS.",
            work.id,
            "Machine Learning Intern at Acme Analytics",
            0.9,
        ),
        (
            LATENCY,
            "Reduced model inference latency by 35% using ONNX.",
            work.id,
            "Machine Learning Intern at Acme Analytics",
            0.7,
        ),
        (
            RAG,
            "Implemented RAG pipeline in Multi-Agent Research Assistant.",
            project.id,
            "Multi-Agent Research Assistant",
            0.8,
        ),
        (
            PYTHON,
            "Built data pipelines in Python and SQL.",
            project.id,
            "Multi-Agent Research Assistant",
            0.6,
        ),
    ):
        item = EvidenceItem(evidence_id, content, context, subject, relevance)
        ws.evidence[evidence_id] = item
        ws.by_subject.setdefault(subject, []).append(item)
    return ws


def answer(question: str, retrieved: list[uuid.UUID] | None = None, max_words: int | None = None):  # type: ignore[no-untyped-def]
    ws = workspace()
    order = [ws.evidence[i] for i in (retrieved or [DOCKER, RAG, LATENCY, PYTHON])]
    return RuleAnswerGenerator().generate(
        ws, None, understand(question, "ML Engineer"), order, max_words
    )


def cited(draft) -> set[uuid.UUID]:  # type: ignore[no-untyped-def]
    return {i for s in draft.sentences for i in s.evidence_ids}


def test_a_skill_answer_uses_only_evidence_that_names_the_skill() -> None:
    draft = answer("Describe your experience with Python.")
    assert [s.text for s in draft.sentences] == [
        "In my Multi-Agent Research Assistant project, I built data pipelines in Python and SQL."
    ]
    assert cited(draft) == {PYTHON}


def test_a_skill_the_evidence_never_mentions_gets_no_answer() -> None:
    draft = answer("What is your experience with Kubernetes?")
    assert draft.sentences == []
    assert "doesn't show experience with Kubernetes" in draft.notes[0]


def test_a_project_answer_describes_one_project() -> None:
    draft = answer("Describe a relevant project.", [RAG, DOCKER, PYTHON])
    assert [s.text for s in draft.sentences] == [
        "In my Multi-Agent Research Assistant project, I implemented RAG pipeline in "
        "Multi-Agent Research Assistant.",
        "I also built data pipelines in Python and SQL.",
    ]
    assert cited(draft) == {RAG, PYTHON}


def test_a_motivation_answer_states_interest_then_evidence() -> None:
    texts = [s.text for s in answer("Why are you interested in this role?").sentences]
    assert texts[0] == "I am interested in the ML Engineer role at Northwind."
    assert texts[-1] == "I would welcome the opportunity to discuss the ML Engineer role with you."
    assert len(texts) == 4  # interest, two evidence sentences, closing


def test_a_fit_answer_is_evidence_only() -> None:
    draft = answer("Why should we hire you?")
    assert draft.sentences and all(s.evidence_ids for s in draft.sentences)


def test_word_limits_drop_sentences_from_the_end() -> None:
    draft = answer("Why should we hire you?", max_words=25)
    assert sum(len(s.text.split()) for s in draft.sentences) <= 25 or len(draft.sentences) == 1
    assert any("word limit" in n for n in draft.notes)
    kept, trimmed = fit_to_words([LetterSentence(text="one two three")], 20)
    assert len(kept) == 1 and not trimmed


def test_mentions_matches_whole_words_and_known_technologies() -> None:
    item = EvidenceItem(uuid.uuid4(), "Built pipelines in Python.", "Project", None, 0.0)
    assert mentions(item, "Python")
    assert not mentions(item, "Py")


def test_a_skill_named_only_in_a_skills_list_gets_a_plain_cited_sentence() -> None:
    ws = workspace()
    skills_list = uuid.uuid4()
    ws.evidence[skills_list] = EvidenceItem(
        skills_list, "Python, SQL, Java, C++", "Skills", None, 0.5
    )
    draft = RuleAnswerGenerator().generate(
        ws,
        None,
        understand("Describe your experience with Java.", "ML Engineer"),
        [ws.evidence[skills_list]],
    )
    assert [s.text for s in draft.sentences] == ["I have worked with Java."]
    assert cited(draft) == {skills_list}
