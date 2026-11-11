"""ATS keyword matching, done by code (not by the model) so the score is repeatable and explainable.

Applicant tracking systems mostly check whether the job description's keywords (skills, tools, title,
certifications) appear in the resume. The score here is the weighted share of JD keywords the resume
contains: required keywords count 2, preferred ones count 1.
"""

import re

# Common tech/role keywords, found in the JD even if the model misses them. (term, category)
SKILL_DICTIONARY = [
    # languages
    *[(t, "Languages") for t in ["Python", "Java", "JavaScript", "TypeScript", "Go", "C++", "C#", "C", "Rust", "Kotlin", "Swift",
                                  "Scala", "Ruby", "PHP", "R", "SQL", "Bash", "Shell", "HTML", "CSS", "Dart", "MATLAB"]],
    # frameworks & libraries
    *[(t, "Frameworks") for t in ["Django", "Flask", "FastAPI", "Spring", "Spring Boot", "React", "Next.js", "Angular", "Vue", "Node.js",
                                   "Express", "NestJS", "Rails", ".NET", "TensorFlow", "PyTorch", "scikit-learn", "Pandas", "NumPy",
                                   "Spark", "PySpark", "Hadoop", "Kafka", "Airflow", "LangChain", "LlamaIndex", "Hugging Face", "Celery",
                                   "GraphQL", "gRPC", "Tailwind", "Redux", "Flutter", "React Native"]],
    # cloud & devops
    *[(t, "Cloud & DevOps") for t in ["AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins",
                                       "GitHub Actions", "GitLab CI", "CI/CD", "Linux", "Nginx", "Helm", "Prometheus", "Grafana",
                                       "Lambda", "EC2", "S3", "Serverless", "Microservices"]],
    # data stores
    *[(t, "Databases") for t in ["PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "DynamoDB", "Cassandra", "SQLite",
                                  "Snowflake", "BigQuery", "Redshift", "Oracle", "Neo4j", "Pinecone", "FAISS", "Vector databases"]],
    # concepts & practices
    *[(t, "Concepts") for t in ["REST", "REST APIs", "APIs", "System design", "Distributed systems", "Data structures", "Algorithms",
                                 "Machine learning", "Deep learning", "NLP", "Computer vision", "LLMs", "Generative AI", "RAG",
                                 "Prompt engineering", "MLOps", "Data pipelines", "ETL", "Data modeling", "Unit testing", "TDD",
                                 "Agile", "Scrum", "OOP", "Design patterns", "Caching", "Message queues", "Observability",
                                 "Security", "Performance optimization", "Scalability", "Git", "Jira", "Code review"]],
]
_DICT_CATEGORY = {t.lower(): c for t, c in SKILL_DICTIONARY}

# Different spellings an ATS treats as the same skill.
SYNONYMS = {
    "postgresql": ["postgres"], "javascript": ["js"], "typescript": ["ts"], "node.js": ["nodejs", "node js"],
    "next.js": ["nextjs"], "kubernetes": ["k8s"], "golang": ["go"], "go": ["golang"],
    "machine learning": ["ml"], "deep learning": ["dl"], "natural language processing": ["nlp"], "nlp": ["natural language processing"],
    "large language models": ["llms", "llm"], "llms": ["llm", "large language models"], "generative ai": ["genai", "gen ai"],
    "aws": ["amazon web services"], "gcp": ["google cloud", "google cloud platform"], "google cloud": ["gcp"],
    "ci/cd": ["ci cd", "continuous integration", "cicd"], "rest apis": ["rest api", "restful apis", "restful", "rest"],
    "rest": ["restful", "rest api", "rest apis"], "apis": ["api"], "unit testing": ["unit tests"], "oop": ["object oriented programming", "object-oriented"],
    "scikit-learn": ["sklearn"], "c#": ["csharp"], "c++": ["cpp"], ".net": ["dotnet"], "rag": ["retrieval augmented generation", "retrieval-augmented generation"],
}

GENERIC = {"experience", "team", "work", "skills", "ability", "knowledge", "years", "role", "candidate", "company", "requirements",
           "responsibilities", "communication", "strong", "good", "excellent", "plus", "bonus", "understanding", "environment"}
WEIGHT = {"required": 2, "preferred": 1}
SKILL_CATEGORIES = {"hard_skill", "tool", "language", "framework", "cloud", "database", "certification", "skill"}


def norm(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9+#./ -]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _pattern(term: str) -> re.Pattern:
    escaped = re.escape(norm(term))
    return re.compile(r"(?<![a-z0-9+#])" + escaped + r"(?:s|es)?(?![a-z0-9+#])")


def contains(text: str, term: str) -> bool:
    """Whole-word, case-insensitive match that also accepts plurals and common alternative spellings."""
    t = norm(text)
    variants = [term, *SYNONYMS.get(norm(term), [])]
    return any(v and _pattern(v).search(t) for v in variants)


def dictionary_terms(jd: str) -> list[dict]:
    """Known skills that literally appear in the JD. Very short terms (Go, R, C) need exact-case matches to avoid false hits."""
    found, low = [], norm(jd)
    # Longest phrases first, so "REST APIs" wins over "REST" and "APIs".
    for term, cat in sorted(SKILL_DICTIONARY, key=lambda t: -len(t[0])):
        if len(term) <= 2:
            if not re.search(r"(?<![A-Za-z0-9+#])" + re.escape(term) + r"(?![A-Za-z0-9+#])", jd):
                continue
        elif not contains(jd, term):
            continue
        if any(contains(f["term"], term) for f in found):
            continue
        n = len(_pattern(term).findall(low)) if len(term) > 2 else 1
        found.append({"term": term, "category": cat, "importance": "required" if n >= 2 else "preferred", "source": "dictionary"})
    return found


def build_keywords(jd: str, model_keywords: list[dict]) -> list[dict]:
    """Merges model-found and dictionary-found keywords. A model keyword is kept only if it really appears in the JD."""
    out: dict[str, dict] = {}
    for k in model_keywords:
        term = re.sub(r"\s+", " ", str(k.get("term", ""))).strip(" .,;:")
        if not term or len(term) > 50 or len(term.split()) > 4 or norm(term) in GENERIC or not contains(jd, term):
            continue
        imp = "required" if str(k.get("importance", "")).lower().startswith("req") else "preferred"
        cat = str(k.get("category", "")).lower().replace(" ", "_") or "other"
        out.setdefault(norm(term), {"term": term, "category": cat, "importance": imp, "source": "model"})
    for k in dictionary_terms(jd):
        key = norm(k["term"])
        if key not in out and not any(contains(o["term"], k["term"]) or contains(k["term"], o["term"]) for o in out.values()):
            out[key] = k
    ranked = sorted(out.values(), key=lambda k: (k["importance"] != "required", k["source"] != "model"))
    return ranked[:35]


def is_skill(k: dict) -> bool:
    return k["source"] == "dictionary" or k["category"] in SKILL_CATEGORIES or norm(k["term"]) in _DICT_CATEGORY


def skill_group_for(term: str) -> str:
    return _DICT_CATEGORY.get(norm(term), "Skills")


def score(resume_text: str, keywords: list[dict]) -> dict:
    total = sum(WEIGHT[k["importance"]] for k in keywords) or 1
    matched = [k["term"] for k in keywords if contains(resume_text, k["term"])]
    got = sum(WEIGHT[k["importance"]] for k in keywords if k["term"] in matched)
    return {"score": round(100 * got / total), "matched": matched, "missing": [k["term"] for k in keywords if k["term"] not in matched]}
