"""Named technologies recognized in job descriptions (canonical name -> aliases).

A technology is only reported when one of its names literally appears in the text.
Ambiguous short names (Go, R, C) are matched only in unambiguous spellings.
"""

import re

from app.profiles.models import SkillCategory

L, F, LIB, T, P, D, C, M = (
    SkillCategory.PROGRAMMING_LANGUAGE,
    SkillCategory.FRAMEWORK,
    SkillCategory.LIBRARY,
    SkillCategory.TOOL,
    SkillCategory.PLATFORM,
    SkillCategory.DATABASE,
    SkillCategory.CLOUD,
    SkillCategory.METHODOLOGY,
)

# canonical name, category, aliases (regex fragments, matched case-insensitively on word edges)
TECHNOLOGIES: tuple[tuple[str, SkillCategory, tuple[str, ...]], ...] = (
    ("Python", L, ("python",)),
    ("Java", L, ("java(?!\\s*script)",)),
    ("JavaScript", L, ("javascript", "\\bjs\\b")),
    ("TypeScript", L, ("typescript",)),
    ("Go", L, ("golang", "go \\(golang\\)")),
    ("C++", L, ("c\\+\\+",)),
    ("C#", L, ("c#",)),
    ("Rust", L, ("rust",)),
    ("Kotlin", L, ("kotlin",)),
    ("Swift", L, ("swift",)),
    ("Ruby", L, ("ruby(?! on rails)",)),
    ("PHP", L, ("php",)),
    ("Scala", L, ("scala",)),
    ("SQL", L, ("sql(?!ite| server)",)),
    ("Bash", L, ("bash", "shell scripting")),
    ("PostgreSQL", D, ("postgresql", "postgres")),
    ("MySQL", D, ("mysql",)),
    ("MongoDB", D, ("mongodb", "mongo")),
    ("Redis", D, ("redis",)),
    ("Elasticsearch", D, ("elasticsearch", "elastic search")),
    ("Snowflake", D, ("snowflake",)),
    ("BigQuery", D, ("bigquery",)),
    ("SQL Server", D, ("sql server", "mssql")),
    ("Kafka", T, ("kafka",)),
    ("Apache Spark", T, ("apache spark", "pyspark", "spark")),
    ("Hadoop", T, ("hadoop",)),
    ("Airflow", T, ("airflow",)),
    ("dbt", T, ("dbt",)),
    ("AWS", C, ("aws", "amazon web services")),
    ("Azure", C, ("azure",)),
    ("GCP", C, ("gcp", "google cloud(?: platform)?")),
    ("Docker", T, ("docker",)),
    ("Kubernetes", T, ("kubernetes", "k8s")),
    ("Terraform", T, ("terraform",)),
    ("Ansible", T, ("ansible",)),
    ("Jenkins", T, ("jenkins",)),
    ("GitHub Actions", T, ("github actions",)),
    ("CI/CD", M, ("ci/cd", "ci / cd")),
    ("Git", T, ("git(?!hub|lab)",)),
    ("Linux", P, ("linux",)),
    ("React", F, ("react(?:\\.js|js)?(?! native)",)),
    ("React Native", F, ("react native",)),
    ("Angular", F, ("angular",)),
    ("Vue.js", F, ("vue(?:\\.js)?",)),
    ("Next.js", F, ("next\\.js",)),
    ("Node.js", P, ("node\\.js", "nodejs")),
    ("Express", F, ("express\\.js", "expressjs")),
    ("Django", F, ("django",)),
    ("Flask", F, ("flask",)),
    ("FastAPI", F, ("fastapi",)),
    ("Spring Boot", F, ("spring boot",)),
    (".NET", F, ("\\.net",)),
    ("Ruby on Rails", F, ("ruby on rails", "rails")),
    ("GraphQL", T, ("graphql",)),
    ("REST APIs", M, ("rest(?:ful)? apis?",)),
    ("gRPC", T, ("grpc",)),
    ("HTML", L, ("html5?",)),
    ("CSS", L, ("css3?",)),
    ("Tailwind CSS", F, ("tailwind",)),
    ("PyTorch", LIB, ("pytorch",)),
    ("TensorFlow", LIB, ("tensorflow",)),
    ("Keras", LIB, ("keras",)),
    ("scikit-learn", LIB, ("scikit-learn", "sklearn")),
    ("Pandas", LIB, ("pandas",)),
    ("NumPy", LIB, ("numpy",)),
    ("Hugging Face", LIB, ("hugging ?face",)),
    ("LangChain", LIB, ("langchain",)),
    ("OpenCV", LIB, ("opencv",)),
    ("MLflow", T, ("mlflow",)),
    ("Machine Learning", M, ("machine learning",)),
    ("Deep Learning", M, ("deep learning",)),
    ("NLP", M, ("nlp", "natural language processing")),
    ("Computer Vision", M, ("computer vision",)),
    ("LLMs", M, ("llms?", "large language models?")),
    ("RAG", M, ("rag", "retrieval[- ]augmented generation")),
    ("Tableau", T, ("tableau",)),
    ("Power BI", T, ("power ?bi",)),
    ("Excel", T, ("excel",)),
    ("Figma", T, ("figma",)),
    ("Jira", T, ("jira",)),
    ("Microservices", M, ("microservices?",)),
    ("Agile", M, ("agile", "scrum")),
)

_PATTERNS = [
    (name, category, re.compile(rf"(?<![\w+#.])(?:{'|'.join(aliases)})(?![\w+#])", re.IGNORECASE))
    for name, category, aliases in TECHNOLOGIES
]
CATEGORY_BY_NAME = {name: category for name, category, _ in TECHNOLOGIES}


def find_technologies(text: str) -> list[str]:
    """Canonical names of technologies literally mentioned in ``text``, in order of appearance."""
    hits: list[tuple[int, str]] = []
    for name, _, pattern in _PATTERNS:
        if m := pattern.search(text):
            hits.append((m.start(), name))
    names = [name for _, name in sorted(hits)]
    if "React Native" in names and "React" in names:
        names.remove("React")
    return names


def mentions(text: str, name: str) -> bool:
    """Whether ``name`` (a canonical name or an alias as written) appears in ``text``."""
    if name in CATEGORY_BY_NAME:
        return name in find_technologies(text)
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE) is not None
