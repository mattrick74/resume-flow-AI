"""Skill normalization, aliases and a built-in vocabulary for offline detection."""

from __future__ import annotations

import re
from functools import lru_cache

# canonical -> aliases (all lowercase). Canonical names are what we store and compare.
ALIASES: dict[str, list[str]] = {
    "javascript": ["js", "ecmascript", "es6"],
    "typescript": ["ts"],
    "node.js": ["node", "nodejs", "node js"],
    "react": ["react.js", "reactjs"],
    "vue": ["vue.js", "vuejs"],
    "angular": ["angularjs", "angular.js"],
    "next.js": ["nextjs"],
    "python": ["python3", "py"],
    "c++": ["cpp"],
    "c#": ["csharp", "c sharp"],
    ".net": ["dotnet", ".net core", "asp.net", "asp.net core"],
    "golang": ["go"],
    "postgresql": ["postgres", "psql"],
    "mysql": [],
    "microsoft sql server": ["sql server", "mssql", "ms sql", "t-sql", "tsql"],
    "mongodb": ["mongo"],
    "amazon web services": ["aws"],
    "google cloud platform": ["gcp", "google cloud"],
    "microsoft azure": ["azure"],
    "kubernetes": ["k8s"],
    "docker": ["containers", "containerization"],
    "terraform": [],
    "ci/cd": ["cicd", "continuous integration", "continuous delivery", "continuous deployment"],
    "machine learning": ["ml"],
    "artificial intelligence": ["ai"],
    "natural language processing": ["nlp"],
    "large language models": ["llm", "llms"],
    "deep learning": [],
    "scikit-learn": ["sklearn", "scikit learn"],
    "tensorflow": [],
    "pytorch": ["torch"],
    "power bi": ["powerbi"],
    "microsoft excel": ["excel", "ms excel"],
    "microsoft office": ["ms office", "office 365", "microsoft 365"],
    "rest apis": ["rest", "restful", "rest api", "restful apis", "restful api"],
    "graphql": [],
    "user experience": ["ux"],
    "user interface": ["ui"],
    "quality assurance": ["qa"],
    "search engine optimization": ["seo"],
    "customer relationship management": ["crm"],
    "enterprise resource planning": ["erp"],
    "project management": ["pm"],
    "agile": ["agile methodologies", "agile methodology"],
    "scrum": [],
    "salesforce": ["sfdc"],
    "sap": [],
    "tableau": [],
    "snowflake": [],
    "apache spark": ["spark", "pyspark"],
    "apache kafka": ["kafka"],
    "apache airflow": ["airflow"],
    "databricks": [],
    "linux": ["unix"],
    "git": ["github", "gitlab", "bitbucket", "version control"],
    "object-oriented programming": ["oop", "object oriented programming"],
    "java": [],
    "kotlin": [],
    "swift": [],
    "ruby on rails": ["rails", "ror"],
    "ruby": [],
    "php": [],
    "rust": [],
    "scala": [],
    "r": ["r programming"],
    "sql": ["structured query language"],
    "nosql": [],
    "redis": [],
    "elasticsearch": ["elastic search", "elk"],
    "html": ["html5"],
    "css": ["css3"],
    "tailwind css": ["tailwind", "tailwindcss"],
    "django": [],
    "flask": [],
    "fastapi": [],
    "spring boot": ["spring", "springboot"],
    "microservices": ["microservice architecture"],
    "jenkins": [],
    "ansible": [],
    "jira": [],
    "figma": [],
    "data analysis": ["data analytics"],
    "data engineering": [],
    "data visualization": [],
    "etl": ["elt", "data pipelines"],
    "statistics": ["statistical analysis"],
    "cybersecurity": ["information security", "infosec", "cyber security"],
    "networking": ["tcp/ip", "network administration"],
    "itil": [],
    "hipaa": [],
    "sox": ["sarbanes-oxley"],
    "gaap": [],
    "financial modeling": ["financial modelling"],
    "accounting": [],
    "budgeting": ["budget management"],
    "stakeholder management": [],
    "product management": [],
    "business analysis": [],
    "requirements gathering": [],
    "technical writing": [],
    "customer service": ["customer support"],
    "sales": [],
    "marketing": ["digital marketing"],
    "communication": ["communication skills"],
    "leadership": ["team leadership"],
    "selenium": [],
    "cypress": [],
    "playwright": [],
    "jest": [],
    "pytest": [],
    "unit testing": ["unit tests"],
    "android": [],
    "ios": [],
    "react native": [],
    "flutter": [],
    "unity": [],
    "matlab": [],
    "sas": [],
    "spss": [],
    "looker": [],
    "dbt": [],
    "bigquery": [],
    "redshift": ["amazon redshift"],
    "oracle": ["oracle database"],
    "sharepoint": [],
    "servicenow": [],
    "workday": [],
    "active directory": ["ad"],
    "powershell": [],
    "bash": ["shell scripting"],
    "vmware": [],
    "openshift": [],
    "helm": [],
    "prometheus": [],
    "grafana": [],
    "datadog": [],
    "splunk": [],
    "power automate": [],
    "power apps": ["powerapps"],
    "dynamics 365": ["microsoft dynamics"],
    "quickbooks": [],
    "lean six sigma": ["six sigma", "lean"],
    "pmp": ["project management professional"],
    # Cloud services and platforms (v1.8.5): specific products, so experience with them shows the general skill.
    "aws lambda": ["amazon lambda", "lambda"],  # bare "lambda" is a skill name only: in text it can be a Python lambda
    "amazon eks": ["eks", "elastic kubernetes service", "amazon elastic kubernetes service"],
    "amazon ecs": ["ecs", "elastic container service", "amazon elastic container service"],
    "google kubernetes engine": ["gke"],
    "azure kubernetes service": ["aks"],
    "amazon rds": ["rds", "aws rds"],
    "amazon s3": ["s3", "aws s3"],
    "amazon ec2": ["ec2", "aws ec2"],
    "amazon dynamodb": ["dynamodb"],
    "amazon sqs": ["sqs"],
    "aws cloudformation": ["cloudformation"],
    "azure functions": [],
    "google cloud functions": ["cloud functions"],
    "serverless": ["serverless computing", "serverless architecture"],
    "github actions": [],
    "gitlab ci": ["gitlab ci/cd"],
    "azure devops": [],
    "rabbitmq": [],
    "postgis": [],
    "timescaledb": ["timescale"],
    "pgvector": [],
}

# Informal spellings of the same product. When a job asks for the product by its proper name, a bullet that uses the
# informal spelling can use the job's wording instead ("Postgres" → "PostgreSQL"); nothing else about it changes.
SPELLINGS: dict[str, str] = {
    "postgres": "postgresql", "k8s": "kubernetes", "mongo": "mongodb", "sklearn": "scikit-learn", "nodejs": "node.js",
    "reactjs": "react", "react.js": "react", "nextjs": "next.js", "vuejs": "vue",
}

_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in ALIASES.items():
    _ALIAS_TO_CANONICAL[_canonical] = _canonical
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL.setdefault(_alias, _canonical)

# Short aliases that are too ambiguous to detect in free text (still fine as exact skill names).
_TEXT_DETECTION_BLOCKLIST = {"go", "r", "ai", "ui", "ux", "pm", "ad", "ts", "py", "rest", "spring", "lean", "node", "sas", "unity", "swift", "ruby", "rust", "oracle", "sales", "marketing", "communication", "leadership", "containers", "unix", "ml", "qa", "seo", "crm", "erp", "elk", "es6", "lambda", "ecs", "rds"}


def normalize_skill(name: str) -> str:
    value = name.strip().lower()
    value = re.sub(r"\(.*?\)", "", value)  # drop parentheticals: "AWS (EC2, S3)" -> "aws"
    value = value.replace("’", "'")
    # A leading dot is part of a name (".NET"); only trailing dots are punctuation.
    value = re.sub(r"\s+", " ", value).strip(" ,;:-–—•*").rstrip(".").strip()
    value = re.sub(r"\s+\d+(\.\d+)*$", "", value)  # "python 3.12" -> "python"
    return _ALIAS_TO_CANONICAL.get(value, value)


_DISPLAY = {
    "c++": "C++", "c#": "C#", ".net": ".NET", "sql": "SQL", "nosql": "NoSQL", "css": "CSS", "html": "HTML", "etl": "ETL",
    "ci/cd": "CI/CD", "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB", "graphql": "GraphQL",
    "javascript": "JavaScript", "typescript": "TypeScript", "node.js": "Node.js", "next.js": "Next.js", "dbt": "dbt",
    "pytorch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn", "fastapi": "FastAPI", "itil": "ITIL",
    "hipaa": "HIPAA", "sox": "SOX", "gaap": "GAAP", "sap": "SAP", "sas": "SAS", "spss": "SPSS", "matlab": "MATLAB",
    "ios": "iOS", "bigquery": "BigQuery", "powershell": "PowerShell", "vmware": "VMware", "openshift": "OpenShift",
    "servicenow": "ServiceNow", "sharepoint": "SharePoint", "quickbooks": "QuickBooks", "pmp": "PMP", "jira": "Jira",
    "github": "GitHub", "rest apis": "REST APIs", "power bi": "Power BI", "amazon web services": "AWS",
    "google cloud platform": "Google Cloud", "microsoft azure": "Azure", "apache kafka": "Kafka",
    "large language models": "LLMs", "user experience": "UX", "user interface": "UI", "quality assurance": "QA",
    "ruby on rails": "Ruby on Rails", "dynamics 365": "Dynamics 365", "lean six sigma": "Lean Six Sigma",
    "aws lambda": "AWS Lambda", "amazon eks": "Amazon EKS", "amazon ecs": "Amazon ECS", "amazon rds": "Amazon RDS",
    "amazon s3": "Amazon S3", "amazon ec2": "Amazon EC2", "amazon dynamodb": "Amazon DynamoDB", "amazon sqs": "Amazon SQS",
    "aws cloudformation": "AWS CloudFormation", "github actions": "GitHub Actions", "gitlab ci": "GitLab CI",
    "azure devops": "Azure DevOps", "rabbitmq": "RabbitMQ", "postgis": "PostGIS", "timescaledb": "TimescaleDB",
    "pgvector": "pgvector",
}


def display_name(canonical: str, original: str | None = None) -> str:
    # Keep the candidate's own casing ("PostgreSQL", "AWS") unless it's just the lowercase canonical form.
    tidy = re.sub(r"\s+", " ", original or "").strip(" ,;:-–—•*").rstrip(".").strip()
    if tidy and normalize_skill(tidy) == canonical and not tidy.islower():
        return tidy
    if canonical in _DISPLAY:
        return _DISPLAY[canonical]
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in canonical.split())


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w+#.]){re.escape(term)}(?![\w+#])", re.IGNORECASE)


@lru_cache(maxsize=4096)
def _patterns_for(canonical: str, include_ambiguous: bool) -> tuple[re.Pattern[str], ...]:
    terms = {canonical, *ALIASES.get(canonical, [])}
    if not include_ambiguous:
        terms = {t for t in terms if t not in _TEXT_DETECTION_BLOCKLIST and len(t) > 1}
    return tuple(_term_pattern(t) for t in sorted(terms, key=len, reverse=True))


def find_skill_in_text(canonical: str, text: str, *, include_ambiguous: bool = False) -> str | None:
    """Returns the line of ``text`` that mentions the skill (or an alias), if any."""
    if not text:
        return None
    for pattern in _patterns_for(canonical, include_ambiguous):
        match = pattern.search(text)
        if match:
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            return text[start : end if end != -1 else len(text)].strip()
    return None


# Practices, methods and business skills: describing work this way isn't a claim about using a particular tool.
PRACTICE_SKILLS = {
    "etl", "data analysis", "data engineering", "data visualization", "statistics", "agile", "scrum", "project management",
    "product management", "business analysis", "requirements gathering", "technical writing", "customer service", "sales",
    "marketing", "communication", "leadership", "stakeholder management", "budgeting", "accounting", "financial modeling",
    "unit testing", "quality assurance", "user experience", "user interface", "rest apis", "microservices", "ci/cd",
    "serverless",
}


def detect_tools(text: str) -> set[str]:
    """Technologies, platforms and products named in ``text`` (canonical names), without practices such as ETL or Agile."""
    return set(detect_known_skills(text)) - PRACTICE_SKILLS


# Work with the key technology shows use of the ones it implies — specific to general only, never the reverse:
# C# work is .NET work, a FastAPI service is Python, a PostgreSQL database is SQL. "SQL" never implies "SQL Server".
IMPLIES: dict[str, tuple[str, ...]] = {
    "c#": (".net",),
    "spring boot": ("java",),
    "django": ("python",),
    "flask": ("python",),
    "fastapi": ("python",),
    "pytest": ("python", "unit testing"),
    "apache airflow": ("python",),
    "scikit-learn": ("python", "machine learning"),
    "pytorch": ("python", "machine learning", "deep learning"),
    "tensorflow": ("machine learning", "deep learning"),
    "next.js": ("react", "javascript"),
    "react native": ("react", "javascript"),
    "react": ("javascript",),
    "vue": ("javascript",),
    "angular": ("typescript",),
    "typescript": ("javascript",),
    "node.js": ("javascript",),
    "jest": ("javascript", "unit testing"),
    "cypress": ("javascript",),
    "postgresql": ("sql",),
    "mysql": ("sql",),
    "microsoft sql server": ("sql",),
    "redshift": ("sql",),
    "bigquery": ("sql",),
    "snowflake": ("sql",),
    "dbt": ("sql",),
    "helm": ("kubernetes",),
    "openshift": ("kubernetes",),
    "jenkins": ("ci/cd",),
    "power bi": ("data visualization",),
    "tableau": ("data visualization",),
    "looker": ("data visualization",),
    "ruby on rails": ("ruby",),
    "databricks": ("apache spark",),
    "mongodb": ("nosql",),
    "postgis": ("postgresql", "sql"),
    "timescaledb": ("postgresql", "sql"),
    "pgvector": ("postgresql",),
    # Managed services are the general platform: EKS is Kubernetes on AWS, Lambda is AWS serverless.
    "aws lambda": ("amazon web services", "serverless"),
    "amazon eks": ("kubernetes", "amazon web services"),
    "google kubernetes engine": ("kubernetes", "google cloud platform"),
    "azure kubernetes service": ("kubernetes", "microsoft azure"),
    "amazon ecs": ("docker", "amazon web services"),
    "amazon rds": ("amazon web services",),
    "amazon s3": ("amazon web services",),
    "amazon ec2": ("amazon web services",),
    "amazon dynamodb": ("amazon web services", "nosql"),
    "amazon sqs": ("amazon web services",),
    "aws cloudformation": ("amazon web services",),
    "azure functions": ("microsoft azure", "serverless"),
    "google cloud functions": ("google cloud platform", "serverless"),
    "github actions": ("ci/cd",),
    "gitlab ci": ("ci/cd",),
    "azure devops": ("ci/cd",),
}

# Wording that describes using a technology without naming it: "serverless functions on AWS" are AWS Lambda.
# Only descriptions that can't mean anything else belong here.
DESCRIBES: dict[str, tuple[str, ...]] = {
    "aws lambda": (
        r"serverless functions?\s+(?:on|in|with|using|running on)\s+(?:aws|amazon web services)\b",
        r"\b(?:aws|amazon)\s+serverless functions?\b",
        # "Lambda" (capitalised, the product name) next to other AWS services, not a Python lambda.
        r"\b(?P<term>(?-i:Lambda))\b(?=.*\b(?:aws|s3|dynamodb|api gateway|sqs|sns|eventbridge|kinesis|step functions)\b)",
        r"\b(?:aws|s3|dynamodb|api gateway|sqs|sns|eventbridge|kinesis|step functions)\b.*\b(?P<term>(?-i:Lambda))\b",
    ),
    "kubernetes": (r"\bkubectl\b",),
    "ci/cd": (r"\b(?:build|deployment|release|delivery)\s+pipelines?\b", r"\bautomated\s+(?:builds?|deployments?|releases?)\b"),
    "git": (r"\b(?:pull|merge)\s+requests?\b", r"\bbranching\s+(?:strategy|model|workflow)\b"),
}

# Related technologies: experience with one is transferable to the other but never shows it ("Docker on ECS" is
# container work, not Kubernetes). Used to choose which accomplishments to emphasise, never to claim the skill.
RELATED: dict[str, tuple[str, ...]] = {
    "kubernetes": ("docker", "amazon ecs", "microservices"),
    "docker": ("kubernetes", "amazon eks", "openshift"),
    "react": ("angular", "vue", "javascript", "typescript"),
    "angular": ("react", "vue", "javascript"),
    "vue": ("react", "angular", "javascript"),
    "next.js": ("react", "node.js"),
    "node.js": ("javascript", "typescript"),
    "typescript": ("javascript",),
    "postgresql": ("mysql", "microsoft sql server", "oracle", "sql", "amazon rds"),
    "mysql": ("postgresql", "microsoft sql server", "oracle", "sql", "amazon rds"),
    "microsoft sql server": ("postgresql", "mysql", "oracle", "sql"),
    "oracle": ("postgresql", "mysql", "microsoft sql server", "sql"),
    "mongodb": ("amazon dynamodb", "nosql", "redis"),
    "aws lambda": ("serverless", "azure functions", "google cloud functions", "amazon web services"),
    "azure functions": ("aws lambda", "google cloud functions", "serverless"),
    "amazon web services": ("microsoft azure", "google cloud platform"),
    "microsoft azure": ("amazon web services", "google cloud platform"),
    "google cloud platform": ("amazon web services", "microsoft azure"),
    "terraform": ("aws cloudformation", "ansible"),
    "ansible": ("terraform",),
    "apache kafka": ("rabbitmq", "amazon sqs"),
    "rabbitmq": ("apache kafka", "amazon sqs"),
    "jenkins": ("github actions", "gitlab ci", "azure devops", "ci/cd"),
    "github actions": ("jenkins", "gitlab ci", "azure devops", "ci/cd"),
    "java": ("kotlin", "scala", "c#"),
    "kotlin": ("java",),
    "c#": ("java",),
    "spring boot": ("java",),
    "tableau": ("power bi", "looker"),
    "power bi": ("tableau", "looker"),
    "looker": ("tableau", "power bi"),
    "snowflake": ("redshift", "bigquery", "databricks"),
    "redshift": ("snowflake", "bigquery"),
    "bigquery": ("snowflake", "redshift"),
    "jest": ("pytest", "unit testing", "cypress"),
    "pytest": ("jest", "unit testing"),
    "cypress": ("playwright", "selenium"),
    "playwright": ("cypress", "selenium"),
    "selenium": ("cypress", "playwright"),
    "graphql": ("rest apis",),
    "fastapi": ("flask", "django"),
    "django": ("flask", "fastapi"),
    "flask": ("django", "fastapi"),
    "prometheus": ("grafana", "datadog"),
    "grafana": ("prometheus", "datadog"),
    "datadog": ("prometheus", "grafana", "splunk"),
    "splunk": ("datadog", "elasticsearch"),
}


def implied_skills(text: str) -> dict[str, str]:
    """canonical skill -> what in ``text`` shows it: a technology that implies it (``"sql" -> "postgresql"``) or a
    phrase that describes it (``"aws lambda" -> "serverless functions on AWS"``). The earliest mention wins."""
    found: dict[str, tuple[int, str]] = {}
    for tool in detect_tools(text):
        span = term_span(text, tool)
        for skill in IMPLIES.get(tool, ()):
            at = span[0] if span else len(text)
            if skill not in found or (at, tool) < found[skill]:
                found[skill] = (at, tool)
    for skill, patterns in DESCRIBES.items():
        for pattern in patterns:
            match = re.search(pattern, text or "", re.IGNORECASE)
            if match and skill not in found:
                part = "term" if "term" in match.re.groupindex else 0  # the words that stand for the skill
                found[skill] = (match.start(part), match.group(part))
    # Specific to general carries on: what shows AWS Lambda also shows AWS.
    pending = list(found)
    while pending:
        skill = pending.pop()
        for general in IMPLIES.get(skill, ()):
            if general not in found:
                found[general] = found[skill]
                pending.append(general)
    return {skill: term for skill, (_, term) in found.items()}


def implied_closure(skill: str) -> set[str]:
    """Every skill that ``skill`` shows, directly or through another: Next.js → React, JavaScript."""
    seen: set[str] = set()
    pending = list(IMPLIES.get(skill, ()))
    while pending:
        general = pending.pop()
        if general not in seen:
            seen.add(general)
            pending.extend(IMPLIES.get(general, ()))
    return seen


def term_span(text: str, term: str) -> tuple[int, int] | None:
    """Where ``term`` (a canonical skill, matched with its aliases, or literal wording) first appears in ``text``."""
    patterns = _patterns_for(term, False) if term in ALIASES else (re.compile(re.escape(term), re.IGNORECASE),)
    spans = [m.span() for p in patterns if (m := p.search(text or ""))]
    return min(spans) if spans else None


def skill_covers(name: str, canonical: str) -> bool:
    """A profile skill entry covers a job skill when it is that skill or names it: "CI/CD Pipelines" covers CI/CD,
    "Stash/Bitbucket" covers Git, "Microsoft SQL Server" covers SQL. Short ambiguous names only match exactly."""
    return normalize_skill(name) == canonical or bool(find_skill_in_text(canonical, name))


def detect_known_skills(text: str, extra_vocabulary: set[str] | None = None) -> dict[str, str]:
    """canonical -> evidence line for every vocabulary skill found in ``text``."""
    found: dict[str, str] = {}
    vocabulary = set(ALIASES) | {normalize_skill(v) for v in (extra_vocabulary or set())}
    for canonical in vocabulary:
        if canonical in _TEXT_DETECTION_BLOCKLIST or len(canonical) < 2:
            continue
        evidence = find_skill_in_text(canonical, text)
        if evidence:
            found[canonical] = evidence
    return found
