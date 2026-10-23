from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from docx import Document as DocxDocument

from resumeflow.domain.templates import TemplateStyle
from resumeflow.services.render import render_resume
from resumeflow.services.template_extract import extract_style, normalize_pdf_font
from resumeflow.services.templates import sample_resume
from tests.conftest import DOCX, upload
from tests.test_workflow import drain, generated_app, setup  # noqa: F401  (fixture)

DISTINCT = TemplateStyle(
    font="Georgia",
    heading_font="Georgia",
    size=11,
    name_size=24,
    name_bold=False,
    name_align="center",
    name_color="2E6B30",
    heading_size=12,
    heading_bold=True,
    heading_caps=False,
    heading_rule=False,
    heading_color="2E6B30",
    rule_color="2E6B30",
    text_color="222222",
    muted_color="707070",
    margin=1.0,
    paragraph_space=4,
)


def _ruled_pdf() -> bytes:
    """A resume PDF with Times fonts, a navy name and ruled, capitalised headings."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 60
    page.insert_text((54, y), "Morgan Lee", fontname="tibo", fontsize=22, color=(0.1, 0.2, 0.45))
    page.insert_text((54, y + 20), "Denver, CO | morgan@example.com | (555) 010-2000", fontname="tiro", fontsize=9, color=(0.4, 0.4, 0.4))
    y += 50
    for heading, lines in (("SUMMARY", ["Platform engineer with ten years of cloud experience."]), ("EXPERIENCE", ["Staff Engineer, Example Corp, 2019 - Present", "Built the deployment platform used by 40 teams"]), ("EDUCATION", ["B.S. Computer Science, 2012"])):
        page.insert_text((54, y), heading, fontname="tibo", fontsize=12, color=(0.1, 0.2, 0.45))
        page.draw_line((54, y + 3), (558, y + 3), color=(0.1, 0.2, 0.45), width=0.8)
        y += 20
        for line in lines:
            page.insert_text((54, y), line, fontname="tiro", fontsize=10.5, color=(0, 0, 0))
            y += 15
        y += 10
    data = document.tobytes()
    document.close()
    return data


def test_docx_style_round_trips():
    extracted = extract_style("docx", render_resume(sample_resume(), DISTINCT))
    assert extracted.style == DISTINCT


def test_builtin_styles_round_trip():
    for key in ("professional", "compact"):
        from resumeflow.domain.templates import BUILTIN_TEMPLATES

        assert extract_style("docx", render_resume(sample_resume(), key)).style == BUILTIN_TEMPLATES[key][1]


def test_pdf_style_is_read_from_fonts_colors_and_rules():
    style = extract_style("pdf", _ruled_pdf()).style
    assert style.font == "Times New Roman" and style.heading_font == "Times New Roman"
    assert style.name_size == 22 and style.name_bold and style.name_color == "1A3373"
    assert style.heading_caps and style.heading_rule and style.heading_bold
    assert style.heading_size == 12 and style.size == 10.5
    assert style.muted_color == "666666"
    assert style.margin == 0.75


@pytest.mark.parametrize(
    ("raw", "family"),
    [("ABCDEF+Calibri-Bold", "Calibri"), ("TimesNewRomanPS-BoldMT", "Times New Roman"), ("ArialMT", "Arial"), ("Garamond,Italic", "Garamond"), ("SourceSerifPro-Semibold", "Source Serif Pro"), ("helv", "Arial")],
)
def test_pdf_font_names_become_families(raw, family):
    assert normalize_pdf_font(raw) == family


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Certified Kubernetes Administrator (CKA), 2021", "Certified Kubernetes Administrator (CKA)"),
        ("PMP (2019)", "PMP"),
        ("(ISC)2 CISSP, 2020", "(ISC)2 CISSP"),
        ("AWS Certified Data Engineer – Associate, 2022", "AWS Certified Data Engineer – Associate"),
    ],
)
def test_certification_names_keep_their_brackets(raw, name):
    from resumeflow.services.resume_parser import _parse_certifications

    assert _parse_certifications([raw])[0].name == name


def test_light_text_on_a_banner_never_becomes_invisible():
    white_name = DISTINCT.model_copy(update={"name_color": "FFFFFF", "heading_color": "F5F5F5"})
    style = extract_style("docx", render_resume(sample_resume(), white_name)).style
    assert style.name_color == style.text_color == "222222"
    assert style.heading_color == "222222"


def test_import_select_rename_and_delete(client, tmp_path):
    listing = client.get("/templates").json()
    assert listing["selected"] == "professional"
    assert [t["key"] for t in listing["templates"]] == ["professional", "compact"]

    imported = client.post(
        "/templates/import", files={"file": ("Jamie_Doe_Resume_final.docx", render_resume(sample_resume(), DISTINCT), DOCX)}
    )
    assert imported.status_code == 201, imported.text
    template = imported.json()
    assert template["name"] == "Jamie Doe style"
    assert template["style"] == DISTINCT.model_dump()
    assert template["source_filename"] == "Jamie_Doe_Resume_final.docx"

    again = client.post("/templates/import", files={"file": ("Jamie_Doe_Resume_final.docx", render_resume(sample_resume(), DISTINCT), DOCX)}).json()
    assert again["name"] == "Jamie Doe style (2)"

    assert client.patch("/settings", json={"resume_template": template["key"]}).status_code == 200
    assert client.get("/templates").json()["selected"] == template["key"]

    tweaked = client.patch(f"/templates/{template['id']}", json={"name": "Green serif", "style": {**template["style"], "heading_rule": True}}).json()
    assert tweaked["name"] == "Green serif" and tweaked["style"]["heading_rule"] is True
    bad = client.patch(f"/templates/{template['id']}", json={"style": {**template["style"], "name_color": "green"}})
    assert bad.status_code == 422

    # Deleting the template in use puts documents back on the default.
    assert client.delete(f"/templates/{template['id']}").status_code == 204
    assert client.get("/settings").json()["settings"]["resume_template"] == "professional"
    assert client.patch("/settings", json={"resume_template": template["key"]}).json()["error"]["code"] == "template_not_found"
    assert client.patch("/settings", json={"resume_template": "fancy"}).status_code == 422


def test_template_from_a_candidates_stored_resume(client):
    candidate = upload(client, 0).json()["candidate"]
    version = candidate["resume_versions"][0]
    created = client.post("/templates/from-resume", json={"candidate_id": candidate["id"], "version_id": version["id"]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Avery Quinlan style" and body["source_filename"] == "Avery_Quinlan_Resume.docx"
    other = client.post("/templates/from-resume", json={"candidate_id": "nope", "version_id": version["id"]})
    assert other.status_code == 404


def test_unusable_files_are_rejected(client):
    scanned = pymupdf.open()
    scanned.new_page()
    response = client.post("/templates/import", files={"file": ("scan.pdf", scanned.tobytes(), "application/pdf")})
    assert response.status_code == 422 and response.json()["error"]["code"] == "no_style_found"
    assert client.post("/templates/import", files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 422


def test_sample_documents(client, config):
    client.patch("/settings", json={"pdf_converter": "none"})
    paths = client.post("/templates/sample", json={"key": "compact"}).json()
    assert Path(paths["resume_docx"]).is_relative_to(config.data_dir)
    assert Path(paths["resume_docx"]).exists() and Path(paths["cover_letter_docx"]).exists()
    assert paths["resume_pdf"] is None
    assert client.post("/templates/sample", json={"key": "custom:00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_export_uses_the_selected_template(client, setup):
    template = client.post("/templates/import", files={"file": ("green.docx", render_resume(sample_resume(), DISTINCT), DOCX)}).json()
    client.patch("/settings", json={"resume_template": template["key"]})
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    client.post(f"/applications/{app['id']}/export")
    drain(client)
    exported = client.get(f"/applications/{app['id']}").json()
    assert exported["status"] == "EXPORTED"
    resume = next(f["path"] for f in exported["files"] if f["type"] == "resume" and f["format"] == "docx")
    assert Path(resume).parent.name == "001 Avery Quinlan"
    assert extract_style("docx", Path(resume).read_bytes()).style.model_dump(exclude={"paragraph_space"}) == DISTINCT.model_dump(exclude={"paragraph_space"})
    assert DocxDocument(resume).styles["Normal"].font.name == "Georgia"
