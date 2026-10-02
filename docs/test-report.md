# CareerPilot end-to-end test report

**Date:** 2026-10-02 (re-run after the production-readiness review) · **Scope:** the whole product, from candidate creation to a tracked
application, plus hallucination scenarios · **Result:** all checks pass, after the
fixes listed below.

## Summary

| Check | Command | Result |
| --- | --- | --- |
| Backend unit tests | `uv run pytest` (without a database) | **517 passed** |
| Backend integration tests (incl. API end-to-end) | `TEST_DATABASE_URL=… uv run pytest -W error` | **341 passed** (858 in the full run, warnings treated as errors) |
| Browser end-to-end tests | `npm run test:e2e` | **4 passed** (production web build, API, mock site, Chromium) |
| Frontend tests | `npm test --workspace web` (Vitest) | **125 passed** (23 files) |
| Type checking | `mypy --strict` (193 files), `tsc --noEmit` | **No errors** |
| Linting and formatting | `ruff check`, `ruff format --check`, ESLint, Prettier | **Clean** |
| Database migrations | `alembic check` | **No drift** |
| Build | `npm run build:web` | **Succeeds** (23 routes) |

## Test data

A realistic, synthetic AI/ML student (`tests/integration/e2e_data.py`):

- **Aarav Mehta**, final-year B.Tech in Computer Science (AI & ML), CGPA 8.9/10.
- **Projects**:
  - *DocuMind*: a RAG question-answering system (Python, LangChain, FAISS, FastAPI).
  - *Crop Disease Classifier*: PyTorch, 92% validation accuracy.
  - *Campus Events Dashboard*: React and FastAPI.
  - *Student Performance Analysis*: pandas, a college project.
- **Certifications**: Machine Learning Specialization (DeepLearning.AI), Generative AI
  with LLMs (Coursera).
- **Skills**: Python, JavaScript, SQL, PyTorch, scikit-learn, FastAPI, React, LangChain,
  Git, Docker.
- **What the candidate doesn't have** (the basis of the hallucination tests): no
  professional experience, no Python certification, and no production system with users.

Four jobs: **AI Engineer Intern** (Northwind AI), **ML Engineer Intern** (Bluefin Labs),
**Frontend Developer Intern** (Pixel Studio), **Data Analyst Intern** (Ledgerly).

## The journey

`tests/integration/test_e2e_journey.py`: one candidate through every stage, using the real
API, database and services, and a headless browser for the application.

| Stage | Verified |
| --- | --- |
| Candidate creation | Profile created with name and contact email |
| Resume upload | DOCX parsed; suggestions reviewed and accepted. The profile then has all 4 projects with their full titles, the degree, both certifications, and the skills |
| Evidence creation | Every resume bullet becomes confirmed evidence on its record, plus statements the candidate adds (at least 10 confirmed items) |
| RAG retrieval | "retrieval augmented generation" finds the DocuMind evidence first; "React frontend…" finds the dashboard project; only verified evidence is returned |
| Job analysis | All 4 postings analyzed; titles kept; required and preferred requirements as stated in the posting |
| Semantic matching | Coverage ranks AI Engineer above Frontend and Data Analyst, and ML Engineer above Data Analyst; every matched or partial requirement cites evidence |
| Skill gaps | Frontend is missing TypeScript; Data Analyst is missing Tableau/Excel; ML Engineer is missing Kubernetes; AI Engineer is missing neither Python nor RAG |
| Resume tailoring | Tailored resume for AI Engineer is verified; it features the RAG project; every summary and skill claim cites evidence |
| Claim verification | Latest report outcome "approved"; reports stored and listed |
| Cover letter | Generated and verified for Northwind AI |
| Application answers | Both answers verified, then approved by the candidate |
| Human approval | Browser assistance is refused before approval. Review requested; the review shows verified documents and no blockers; approved with confirmation and the content hash |
| Mock browser application | Asks for work authorization (not in the records) instead of guessing; pauses for review with nothing sent; after confirmation the site receives the candidate's details and the exact reviewed resume and cover letter (SHA-256 match) |
| Application tracking | Submitted, with a follow-up reminder and timeline. An interview moves it to "Interview"; the dashboard counts 3 applications, 1 interview and 2 saved |

**Browser** (`tests/e2e/test_web_journey.py`, in Chromium against a production build):

- the dashboard greets the candidate and counts 4 jobs;
- the Jobs list and a job's analysis show;
- the Resume Builder shows the verified resume;
- **Application Review**: opening it approves nothing, and Approve is disabled until the
  checkbox is ticked;
- **assisted application**: work authorization asked for, review shown with "Nothing has
  been submitted yet", Submit disabled until confirmed, then submitted;
- the mock site received exactly one submission;
- the application shows Submitted and the dashboard counts it;
- the mobile menu works, and nothing scrolls sideways at phone width.

## Hallucination scenarios

`tests/integration/test_e2e_hallucination.py` (6 tests):

| Candidate has | Must never become | Result |
| --- | --- | --- |
| A RAG project | "I have 5 years of professional RAG experience." | **Rejected** |
| Python projects | "I hold a Python certification." | **Rejected** |
| A college project | "My college project is a production system used by 10,000 users." | **Rejected** |

Each scenario was attacked five ways:

1. **Claim checker, rules only**: all three are rejected. The true counterparts ("I built
   a RAG pipeline…", "…preprocessing pipeline in Python", "…pandas for a college
   project") are supported.
2. **A model that vouches for them**: the claims cite the candidate's *real* RAG, Python
   and college evidence, and the AI reviewer answers "supported". All three are still
   rejected, because the rules decide and an AI reviewer can't upgrade a claim the rules
   refute.
3. **A hallucinating model writing the documents**: the tailored resume, cover letter and
   answers are generated by a model that writes exactly these claims, citing real
   evidence. None reaches a document, and they appear in the verification audit as
   caught.
4. **The candidate's own edits**: adding any of the three to the resume, the cover letter
   or an answer is refused (422) with the reason.
5. **Default documents for all four jobs**: none contains years of experience, a Python
   certification, "production system" or "10,000". Only the candidate's two real
   certifications appear.

**Paraphrases**: 12 rewordings are all rejected, while two true claims worded differently
from the evidence are accepted. The rewordings include:

- "five years of RAG experience" and "3+ years of professional experience…";
- "senior RAG engineer";
- "certified Python developer", "earned a Python certification from Coursera" and "PCAP
  Python certification";
- "used by thousands of users", "deployed to production for 10,000 students" and "used
  in production by farmers";
- "led a team of 5 engineers", "worked … at Google" and "99% accuracy".

## Issues found and fixed

| # | Issue | Found by | Fix |
| --- | --- | --- | --- |
| 1 | **Project titles were cut short**: "DocuMind - RAG Question Answering \| Python, …" became "DocuMind", because the resume parser split on " - " before the tech list | Journey, resume upload | The title now runs up to the first " \| " (`resumes/heuristic.py`) |
| 2 | **A certification issuer was stored as a link**: "… - DeepLearning.AI (2024)" set `credential_url = "DeepLearning.AI"`, because a dotted name looked like a domain | Journey, resume upload | Only real links (a scheme, `www.`, or a path) are treated as URLs; the issuer is kept |
| 3 | **Year-only dates gained a month**: resume dates such as "2022 - 2026" or "(2024)" are stored with a month (January/December) that the resume doesn't state | Journey, resume upload | The candidate is now warned while reviewing the extracted information, and corrects the months before accepting. See the open item below |
| 4 | **A true claim was rejected**: "I trained a PyTorch model with 92% validation accuracy" was refused, because the verifier only looked *before* a number for what it measures, while the evidence named it after ("…reaching 92% validation accuracy") | Hallucination paraphrase test | The words after a number in the same clause count too (`verification/compare.py`). A metric still can't be borrowed for something else (unit test added) |
| 5 | **The browser test script left the web server running** | Browser end-to-end run | `tests/e2e/run.sh` builds first and stops every listener on its ports at exit; the throwaway database is dropped |

Each fix has a test: parser warnings are asserted (`test_resume_parsing.py`), the metric
rule has a unit test (`test_verification_compare.py`), and the journey and paraphrase
tests cover the rest.

## Open items

- **Date precision** (issue 3): profile dates have month precision, so a year-only date
  still needs the candidate to check the month (they are now told to). A complete fix
  stores date precision per record, so a year-only date is shown as a year.
- **AI provider in tests**: all tests run without a real AI provider. The model-backed
  paths are exercised with deterministic adversarial and hallucinating fakes, which is
  the stricter test of the safeguards. A run against the real provider would measure
  wording quality, not safety.
- **Semantic retrieval in tests** uses the built-in hash embedder (lexical similarity).
  With a real embedding provider the rankings may differ; the assertions only check
  ordering that both should satisfy.

## How to reproduce

```bash
# Backend: unit + integration (+ API end-to-end)
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://…/careerpilot_test uv run pytest -W error

# Browser end-to-end (isolated stack)
E2E_ADMIN_DB=postgresql://…/postgres npm run test:e2e

# Frontend, types, lint, build
npm test --workspace web && npm run typecheck && npm run lint && npm run build:web
```
