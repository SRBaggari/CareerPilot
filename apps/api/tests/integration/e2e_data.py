"""Realistic (synthetic) end-to-end test data: an AI/ML student and four internships.

The candidate is a student: projects, coursework, certifications and education, but no
years of professional experience, no Python certification, and no production system with
real users. Those absences are what the hallucination tests rely on.
"""

CANDIDATE_NAME = "Aarav Mehta"
CANDIDATE_EMAIL = "aarav.mehta@example.test"

RESUME_LINES = [
    "AARAV MEHTA",
    "Bengaluru, India | aarav.mehta@example.test | +91 90000 12345",
    "linkedin.com/in/aarav-mehta-ml | github.com/aarav-ml",
    "",
    "SUMMARY",
    "Final-year B.Tech student specialising in artificial intelligence and machine learning.",
    "",
    "EDUCATION",
    "Bangalore Institute of Engineering, Bengaluru    2022 - 2026",
    "B.Tech in Computer Science and Engineering (AI & ML)",
    "CGPA: 8.9/10",
    "",
    "PROJECTS",
    "DocuMind - RAG Question Answering | Python, LangChain, FAISS, FastAPI",
    "• Built a retrieval-augmented generation (RAG) pipeline that answers questions over "
    "course notes using FAISS vector search.",
    "• Served the RAG pipeline through a FastAPI REST API with streaming responses.",
    "Crop Disease Classifier | Python, PyTorch",
    "• Trained a convolutional neural network in PyTorch to classify leaf diseases, "
    "reaching 92% validation accuracy.",
    "• Built the image preprocessing and data augmentation pipeline in Python.",
    "Campus Events Dashboard | React, FastAPI",
    "• Built a React frontend for a college club to browse and register for events.",
    "• Implemented the FastAPI backend with SQLite for event registrations.",
    "Student Performance Analysis | Python, pandas, Matplotlib",
    "• Analysed exam data with pandas and visualised trends with Matplotlib for a college project.",
    "",
    "TECHNICAL SKILLS",
    "Languages: Python, JavaScript, SQL",
    "Frameworks: PyTorch, scikit-learn, FastAPI, React, LangChain",
    "Tools: Git, Docker",
    "",
    "CERTIFICATIONS",
    "Machine Learning Specialization - DeepLearning.AI (2024)",
    "Generative AI with Large Language Models - Coursera (2025)",
    "",
    "RELEVANT COURSEWORK",
    "Machine Learning, Deep Learning, Natural Language Processing, Database Systems",
]

# Evidence the candidate adds by hand after the upload (confirmed on creation).
EXTRA_EVIDENCE = [
    "Evaluated the DocuMind RAG pipeline on 50 questions written by classmates.",
    "Used scikit-learn to build baseline classifiers for the crop disease project.",
]

JOBS = {
    "ai": {
        "title": "AI Engineer Intern",
        "company_name": "Northwind AI",
        "description": """AI Engineer Intern - Northwind AI (Bengaluru, Hybrid)

About the role
You will help build retrieval-augmented generation (RAG) features for our document
assistant and ship them behind a FastAPI service.

Requirements
- Strong programming skills in Python (required)
- Experience building RAG pipelines or LLM applications (required)
- Experience with FastAPI or similar Python web frameworks (required)
- Familiarity with vector databases such as FAISS (preferred)
- Knowledge of machine learning fundamentals (required)

Nice to have
- Experience with Docker
""",
    },
    "ml": {
        "title": "ML Engineer Intern",
        "company_name": "Bluefin Labs",
        "description": """ML Engineer Intern - Bluefin Labs (Remote)

You will train and evaluate machine learning models for our computer vision products.

Requirements
- Python and PyTorch experience (required)
- Experience training deep learning models (required)
- Understanding of data preprocessing and augmentation (required)
- Experience with Kubernetes (required)
- Familiarity with MLflow is a plus
""",
    },
    "frontend": {
        "title": "Frontend Developer Intern",
        "company_name": "Pixel Studio",
        "description": """Frontend Developer Intern - Pixel Studio (Pune, Onsite)

Build responsive web interfaces for our design tools.

Requirements
- Experience with React (required)
- Strong TypeScript skills (required)
- Solid CSS and responsive design skills (required)
- Experience with Figma (preferred)
- Experience writing unit tests with Jest (preferred)
""",
    },
    "data": {
        "title": "Data Analyst Intern",
        "company_name": "Ledgerly",
        "description": """Data Analyst Intern - Ledgerly (Hyderabad, Hybrid)

Turn financial data into insights for our product teams.

Requirements
- Advanced SQL skills (required)
- Experience with Tableau or Power BI dashboards (required)
- Advanced Excel skills (required)
- Python with pandas for data analysis (preferred)
- Knowledge of statistics (required)
""",
    },
}

# What the candidate does NOT have: generated text must never claim these.
HALLUCINATIONS = {
    "rag_experience": "I have 5 years of professional RAG experience.",
    "python_certification": "I hold a Python certification.",
    "production_users": ("My college project is a production system used by 10,000 users."),
}
# The true counterparts, which must be supported.
TRUTHS = {
    "rag_project": "I built a RAG pipeline that answers questions over course notes.",
    "python_project": "I built the image preprocessing pipeline in Python.",
    "college_project": "I analysed exam data with pandas for a college project.",
}
