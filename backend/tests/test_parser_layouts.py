"""Real-world resume layouts the offline parser must handle (anonymised look-alikes of operator files)."""

from __future__ import annotations

from resumeflow.services.resume_parser import parse_resume

TITLE_DATE_COMPANY = """Morgan Avery
Senior Software Engineer | Backend Engineering
Atlanta, GA | morgan@example.com | +1 404 555 0100
Summary
Senior engineer with 6+ years of experience building distributed systems.
Technical Skills
Programming Languages: Python, Go, Rust, SQL
Work Experience

Senior Software Engineer

Jun 2022 – present
Brightwave • Full-time
Engineer distributed storage infrastructure and backend services for low-latency systems.
Technologies: Distributed Systems, Rust, Load Balancing, Performance Optimization
• Built and scaled the core engine of a distributed key-value store from a prototype into production, supporting large data
volumes and high write throughput.
• Led infrastructure migrations with zero downtime.
Software Engineer
Jun 2020 – Jun 2022
Northgate, Inc. • Full-time
Designed backend services for identity management, emphasizing
scalability and database performance
Technologies: Distributed Systems, Backend Engineering
• Designed a change-detection framework that delivered up to a 47× performance improvement when processing
unchanged identities during imports.
• Reduced database queries by 95%.
Education
Georgia Institute of Technology
Bachelor of Science in Computer Science
Aug 2016 – May 2020
"""

NO_HEADING_PIPES = """CASEY RIVERS
Senior Software Engineer | Full Stack Development
Smyrna, GA | casey@example.com | +1 (706) 555-0101
Summary
Senior Software Engineer with 12+ years of experience building full-stack applications.
Technical Skills
Programming Languages: Java, C#, Python, TypeScript
Cloud & Infrastructure: AWS, AWS Lambda, DynamoDB
Harbor Life Insurance | Senior Software Engineer | Mar 2022 - Present
Lead full-stack engineering for insurance platforms using React and TypeScript.
• Lead development of internal agent portals using React.js, Next.js,
Redux, and TypeScript.
• Co-developed a lead-distribution algorithm that improved processing efficiency by more than 50%.
Lakeside Bank | Associate Data Engineer - Enterprise Data Services | Jul 2015 - Aug 2016
• Recreated risk-modeling ETL processes on Apache Hadoop.
Education
Georgia Institute of Technology
Master of Science in Computer Science | 2019 – 2021
"""

NO_HEADING_ICONS = (
    "Jordan Blake\nSenior AI Engineer\nNorwalk, CT | (470) 555-0102 | jordan@example.com\nSUMMARY\n"
    "8+ years of experience across software engineering and AI.\nTECHNICAL SKILLS\n•\nBackend: Python, FastAPI, REST APIs\n"
    "•\nOther Tools & Practices: Git, Linux, Agile Development, Cross-\nFunctional Collaboration\n"
    "AI Software Engineer Elm University\nNov 2024 – Present\n•\nDesigned and deployed production AI systems supporting research and\n"
    "institutional workflows.\n•\nBuilt AI-powered workflows for publication summarization.\n\n"
    "Machine Learning Engineer II Pexo\nJun 2021 – Apr 2023\n•\nTrained ranking models for a media platform.\n"
    "EDUCATION\nB.S. in Computer Science, Ursa College, 2019\n"
)

PROMOTION_BLOCKS = """Riley Stone
Software Engineer
Columbus, OH | riley@example.com | 614-555-0103
SUMMARY
Full-stack engineer.
EXPERIENCE
WEXFORD | Remote
Senior Software Engineer | May 2024 - Present
Software Engineer 2 | Mar 2022 - May 2024
Environment: C#/.NET, Java, Angular, TypeScript, APIs, microservices, production
reliability
- Earned promotion to Senior Software Engineer after more than two years, reflecting
ownership across full-stack delivery.
- Own end-to-end delivery across Angular user interfaces and Java services.
Delta Petroleum Corporation | Findlay, Ohio
Software Engineer | Jun 2020 - Mar 2022
Software Engineer Intern | May 2019 - Aug 2019
- Resolved recurring production defects, reducing failures by 75% on critical jobs.
ADDITIONAL EXPERIENCE AND LEADERSHIP
Old Mill Winery: Bartender, May 2018 - Aug 2018 | Geneva, Ohio
EDUCATION
Bachelor's Degree in Computer Science | Bowling Green State University | Aug 22, 2016 - Aug 15, 2020
"""

TECH_LIST_LINES = """Taylor Knox
Senior Backend Engineer
Overland Park, KS | taylor@example.com | 913-555-0104
Experience
Senior Rust Software Engineer
Feb 2022 – Present
Telemobile | Overland Park, KS
Rust | Go | Python | Distributed Systems | Microservices | AWS | Kubernetes | Docker |
Kafka | PostgreSQL | gRPC | Terraform | CI/CD
• Designed high-performance backend services using Rust and asynchronous programming.
• Automated deployment workflows, improving release efficiency by 50%.
Senior Backend Engineer
Jul 2018 – Jan 2022
Workflowly, Mountain View, CA
Java | C++ | Rust | Python | Spring Boot | PostgreSQL | Redis | Kafka | Docker | Kubernetes |
Linux | REST APIs
• Built integration services handling 10 million requests a day.
"""


def roles(text: str):
    return [(r.title, r.employer, r.location, r.start, r.end, [b.text for b in r.bullets]) for r in parse_resume(text).profile.employment]


def test_title_then_dates_then_company_with_technologies_lines():
    parsed = roles(TITLE_DATE_COMPANY)
    assert [(t, e, s, end) for t, e, _, s, end, _ in parsed] == [
        ("Senior Software Engineer", "Brightwave", "2022-06", "present"),
        ("Software Engineer", "Northgate, Inc.", "2020-06", "2022-06"),
    ]
    first, second = parsed[0][5], parsed[1][5]
    assert first == [
        "Engineer distributed storage infrastructure and backend services for low-latency systems.",
        "Built and scaled the core engine of a distributed key-value store from a prototype into production, supporting large data volumes and high write throughput.",
        "Led infrastructure migrations with zero downtime.",
    ]
    assert second[0] == "Designed backend services for identity management, emphasizing scalability and database performance"
    assert second[1].endswith("when processing unchanged identities during imports.")
    assert not any("Technologies" in b for b in first + second)
    result = parse_resume(TITLE_DATE_COMPANY)
    assert result.profile.education[0].institution == "Georgia Institute of Technology" and result.profile.education[0].end_year == 2020


def test_roles_without_an_experience_heading_in_pipe_format():
    parsed = roles(NO_HEADING_PIPES)
    assert [(t, e, s, end) for t, e, _, s, end, _ in parsed] == [
        ("Senior Software Engineer", "Harbor Life Insurance", "2022-03", "present"),
        ("Associate Data Engineer - Enterprise Data Services", "Lakeside Bank", "2015-07", "2016-08"),
    ]
    assert parsed[0][5][1] == "Lead development of internal agent portals using React.js, Next.js, Redux, and TypeScript."
    result = parse_resume(NO_HEADING_PIPES)
    assert not any("Harbor" in s.name for s in result.skills)  # role lines no longer leak into skills
    assert result.warnings == []


def test_icon_separators_and_lone_bullet_glyphs():
    parsed = roles(NO_HEADING_ICONS)
    assert [(t, e) for t, e, *_ in parsed] == [("AI Software Engineer", "Elm University"), ("Machine Learning Engineer II", "Pexo")]
    assert parsed[0][5] == [
        "Designed and deployed production AI systems supporting research and institutional workflows.",
        "Built AI-powered workflows for publication summarization.",
    ]
    assert parse_resume(NO_HEADING_ICONS).profile.summary == "8+ years of experience across software engineering and AI."


def test_promotions_under_one_company_share_its_name_and_bullets():
    parsed = roles(PROMOTION_BLOCKS)
    assert [(t, e, loc) for t, e, loc, *_ in parsed] == [
        ("Senior Software Engineer", "WEXFORD", "Remote"),
        ("Software Engineer 2", "WEXFORD", "Remote"),
        ("Software Engineer", "Delta Petroleum Corporation", None),
        ("Software Engineer Intern", "Delta Petroleum Corporation", None),
    ]
    assert len(parsed[0][5]) == 2 and parsed[1][5] == []  # bullets stay with the latest title
    assert parsed[0][5][0] == "Earned promotion to Senior Software Engineer after more than two years, reflecting ownership across full-stack delivery."
    assert not any("reliability" == b for _, _, _, _, _, bullets in parsed for b in bullets)
    education = parse_resume(PROMOTION_BLOCKS).profile.education[0]
    assert (education.degree, education.institution, education.end_year) == ("Bachelor's Degree in Computer Science", "Bowling Green State University", 2020)


def test_technology_lists_are_not_bullets_and_trailing_places_split():
    parsed = roles(TECH_LIST_LINES)
    assert [(t, e, loc) for t, e, loc, *_ in parsed] == [
        ("Senior Rust Software Engineer", "Telemobile", "Overland Park, KS"),
        ("Senior Backend Engineer", "Workflowly", "Mountain View, CA"),
    ]
    assert parsed[0][5] == [
        "Designed high-performance backend services using Rust and asynchronous programming.",
        "Automated deployment workflows, improving release efficiency by 50%.",
    ]
    assert parsed[1][5] == ["Built integration services handling 10 million requests a day."]


def test_words_hyphenated_at_a_line_break_are_rejoined():
    from resumeflow.services.resume_parser import join_wrapped

    assert join_wrapped("scaling high-", "performance backend infrastructure") == "scaling high-performance backend infrastructure"
    assert join_wrapped("Led migrations with zero", "downtime.") == "Led migrations with zero downtime."
    assert join_wrapped("Python -", "Go") == "Python - Go"
    summary = parse_resume("Sam Lee\nEngineer\nsam@example.com\nSummary\nBuilt high-\nperformance systems with zero-\ndowntime releases.\n").profile.summary
    assert summary == "Built high-performance systems with zero-downtime releases."


def test_cover_letter_may_name_the_job_title_without_claiming_the_skill():
    from resumeflow.domain.content import CoverLetterContent, CoverParagraph
    from resumeflow.services.validation import OtherCandidates, validate_cover_letter

    letter = CoverLetterContent(
        date="October 17, 2026",
        company="Lorven Technologies Inc.",
        job_title="ServiceNow AI Engineer",
        paragraphs=[CoverParagraph(text="I am writing to apply for the ServiceNow AI Engineer position at Lorven Technologies Inc.")],
        signature="Sam Lee",
    )
    snap = {"full_name": "Sam Lee", "employment": [], "skills": [], "total_years_experience": 0}
    codes = {i["code"] for i in validate_cover_letter(letter, snap, OtherCandidates())}
    assert "unverified_skill_mention" not in codes
    bragging = letter.model_copy(update={"paragraphs": [CoverParagraph(text="I have built many ServiceNow integrations at Lorven Technologies Inc.")]})
    assert "unverified_skill_mention" in {i["code"] for i in validate_cover_letter(bragging, snap, OtherCandidates())}