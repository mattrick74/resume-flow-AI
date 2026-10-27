"""Resume file validation, text extraction (DOCX / text-based PDF) and offline parsing.

The heuristic parser is deliberately conservative: everything it returns is an
*unverified suggestion* for the operator to confirm (PRD §5). AI extraction
(services.ai.extraction) produces the same ``ExtractedResume`` shape.
"""

from __future__ import annotations

import functools
import io
import re
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from resumeflow.domain.links import clean_links, find_url, is_profile_link, link_key
from resumeflow.domain.profile import Bullet, CandidateProfile, Certification, Education, Role
from resumeflow.domain.skills import detect_known_skills, normalize_skill
from resumeflow.errors import AppError

MAX_RESUME_BYTES = 10 * 1024 * 1024
MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
MIN_TEXT_CHARS = 80


class ExtractedSkill(BaseModel):
    name: str
    evidence: str | None = None


class ExtractedResume(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    profile: CandidateProfile = Field(default_factory=CandidateProfile)
    skills: list[ExtractedSkill] = Field(default_factory=list)
    source: Literal["heuristic", "ai"] = "heuristic"
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- validation


def detect_format(filename: str, data: bytes) -> Literal["docx", "pdf"]:
    extension = Path(filename or "").suffix.lower()
    if not data:
        raise AppError(422, "empty_file", "The file is empty.")
    if len(data) > MAX_RESUME_BYTES:
        raise AppError(413, "file_too_large", "Resumes must be 10 MB or smaller.")
    if extension == ".doc":
        raise AppError(422, "unsupported_file", "Legacy .doc files aren't supported. Open it in Word and save as .docx.")
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise AppError(422, "unsupported_file", "This file has a .pdf extension but isn't a valid PDF.")
        return "pdf"
    if extension == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            raise AppError(422, "unsupported_file", "This file has a .docx extension but isn't a valid Word document.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise AppError(422, "unsupported_file", "This .docx file has no document body.")
        except zipfile.BadZipFile as exc:
            raise AppError(422, "unsupported_file", "This .docx file is damaged.") from exc
        return "docx"
    raise AppError(422, "unsupported_file", "Only .docx and text-based .pdf resumes are supported.")


# ---------------------------------------------------------------- text extraction


def extract_text(fmt: Literal["docx", "pdf"], data: bytes) -> str:
    text = _extract_docx(data) if fmt == "docx" else _extract_pdf(data)
    text = _normalize_text(text)
    if len(re.sub(r"\s", "", text)) < MIN_TEXT_CHARS:
        if fmt == "pdf":
            raise AppError(
                422,
                "no_text",
                "This PDF has no selectable text (it is probably a scanned image). Use the original DOCX or a text-based PDF.",
            )
        raise AppError(422, "no_text", "This document contains almost no text.")
    return text


def _extract_docx(data: bytes) -> str:
    from docx import Document
    from docx.oxml.ns import qn

    w_p, w_t, w_tab = qn("w:p"), qn("w:t"), qn("w:tab")
    w_hyperlink, r_id = qn("w:hyperlink"), qn("r:id")
    w_instr, w_fld_simple, w_instr_attr = qn("w:instrText"), qn("w:fldSimple"), qn("w:instr")
    breaks = {qn("w:br"), qn("w:cr")}
    fallback = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

    def nearest_paragraph(node):
        parent = node.getparent()
        while parent is not None and parent.tag != w_p:
            parent = parent.getparent()
        return parent

    def inside_fallback(node) -> bool:
        parent = node.getparent()
        while parent is not None:
            if parent.tag == fallback:
                return True
            parent = parent.getparent()
        return False

    def paragraph_text(paragraph) -> str:
        parts: list[str] = []
        for node in paragraph.iter(w_t, w_tab, *breaks):
            if nearest_paragraph(node) is not paragraph:
                continue  # belongs to a nested text-box paragraph, emitted separately
            if node.tag == w_t:
                parts.append(node.text or "")
            elif node.tag == w_tab:
                parts.append("\t")
            else:
                parts.append("\n")
        return "".join(parts)

    def hyperlinks(paragraph, part) -> list[tuple[str, str]]:
        """(visible text, address) of the web links in a paragraph: hyperlink elements and HYPERLINK fields."""
        found: list[tuple[str, str]] = []
        for link in paragraph.iter(w_hyperlink):
            rel = part.rels.get(link.get(r_id) or "") if nearest_paragraph(link) is paragraph else None
            if rel is not None and rel.is_external:
                found.append(("".join(t.text or "" for t in link.iter(w_t)), rel.target_ref))
        for node in paragraph.iter(w_instr, w_fld_simple):
            if nearest_paragraph(node) is paragraph:
                field = node.text if node.tag == w_instr else node.get(w_instr_attr)
                if match := re.search(r'HYPERLINK\s+"([^"]+)"', field or ""):
                    found.append(("", match.group(1)))
        return found

    def with_link_addresses(text: str, links: list[tuple[str, str]], header_area: bool) -> str:
        # A link shown as a label ("LinkedIn") keeps its address next to it, so the profile gets the real URL. Only in
        # the resume's header area, or for profile sites, so a link inside a bullet doesn't change the bullet's text.
        for label, url in links:
            if not url.lower().startswith(("http://", "https://")) or link_key(url) in link_key_text(text):
                continue
            if not (header_area or is_profile_link(url)):
                continue
            label = label.strip()
            if label and label in text and not find_url(label):
                text = text.replace(label, f"{label} ({url})", 1)
            elif not find_url(label):
                text = f"{text} {url}".strip()
        return text

    def lines_of(element, part, header_lines: int) -> list[str]:
        lines: list[str] = []
        for p in element.iter(w_p):
            if inside_fallback(p):
                continue
            text = paragraph_text(p)
            links = hyperlinks(p, part)
            if links:
                text = with_link_addresses(text, links, len([line for line in lines if line.strip()]) < header_lines)
            lines.append(text)
        return lines

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several types for corrupt files
        raise AppError(422, "unsupported_file", "This Word document could not be read.") from exc

    lines: list[str] = []
    seen_headers: set[str] = set()
    for section in document.sections:
        for header in (section.header, section.first_page_header):
            try:
                header_lines = lines_of(header._element, header.part, 1000)  # page headers hold contact details
            except Exception:
                continue
            key = "\n".join(header_lines)
            if key.strip() and key not in seen_headers:
                seen_headers.add(key)
                lines.extend(header_lines)
    lines.extend(lines_of(document.element.body, document.part, _HEADER_AREA_LINES))
    return "\n".join(lines)


_HEADER_AREA_LINES = 12  # the first non-empty lines of a resume: name, headline and contact details


def link_key_text(text: str) -> str:
    """``text`` compared the way ``link_key`` compares addresses (case and "https://www." don't matter)."""
    return re.sub(r"https?://|www\.", "", text, flags=re.IGNORECASE).lower()


def _pdf_page_text(page, first_page: bool) -> str:
    """A page's text with the address of each web link next to its label ("LinkedIn (https://…)") when the address
    itself isn't shown: in the top part of the first page (contact details), or anywhere for profile sites."""
    text = page.get_text("text")
    top = page.rect.y0 + page.rect.height * 0.25
    for link in page.get_links():
        url = (link.get("uri") or "").strip()
        if not url.lower().startswith(("http://", "https://")) or link_key(url) in link_key_text(text):
            continue
        area = link.get("from")
        if not ((first_page and area is not None and area.y1 <= top) or is_profile_link(url)):
            continue
        label = " ".join((page.get_textbox(area) if area is not None else "").split())
        if find_url(label):
            continue
        if label and label in text:
            text = text.replace(label, f"{label} ({url})", 1)
        else:
            text += f"\n{url}"
    return text


def _extract_pdf(data: bytes) -> str:
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise AppError(422, "unsupported_file", "This PDF could not be opened.") from exc
    with document:
        if document.needs_pass:
            raise AppError(422, "encrypted_pdf", "This PDF is password-protected. Remove the password and try again.")
        return "\n".join(_pdf_page_text(page, index == 0) for index, page in enumerate(document))


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- heuristic parsing

_SECTION_PATTERNS: dict[str, str] = {
    "summary": r"(?:professional |career |executive )?(?:summary|profile|overview)|objective|career objective|about me|professional profile",
    "experience": r"(?:professional |work |relevant |career )?experience|employment(?: history)?|work history|career history|professional background",
    "education": r"education(?: (?:and|&) training)?|academic (?:background|qualifications)",
    "skills": r"(?:technical |core |key |professional )?skills(?: (?:and|&) (?:tools|technologies|abilities|expertise))?|core competencies|competencies|technologies|technical proficiencies|tools(?: (?:and|&) technologies)?|areas of expertise|expertise|tech stack",
    "certifications": r"(?:professional )?certifications?(?: (?:and|&) licen[sc]es?)?|licen[sc]es?(?: (?:and|&) certifications?)?|credentials",
    "projects": r"(?:key |selected |notable )?projects",
    "authorization": r"work authori[sz]ation|authori[sz]ation(?: to work)?|work eligibility|citizenship|visa status|security clearance|clearance",
    "other": r"awards(?: (?:and|&) honors)?|honors|publications|volunteer(?:ing| experience)?|languages|interests|references|activities|affiliations|memberships|"
    r"(?:additional|other) experience(?: (?:and|&) leadership)?|leadership(?: experience| (?:and|&) (?:activities|involvement))?|"
    r"extracurricular(?: activities)?|activities (?:and|&) leadership|additional (?:information|technical strengths|strengths)",
}
# Inside a role, "Technologies: ..." is part of the role, not the start of a Skills section.
_ROLE_SECTIONS = {"experience", "projects"}
_HEADING = {name: re.compile(rf"^(?:{pattern})\s*:?$", re.IGNORECASE) for name, pattern in _SECTION_PATTERNS.items()}
_INLINE_HEADING = {
    name: re.compile(rf"^(?:{pattern})\s*:\s*(?P<rest>.+)$", re.IGNORECASE) for name, pattern in _SECTION_PATTERNS.items()
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]?\d{3,4}[\s.-]?\d{3,4}")
_LINK = re.compile(r"(?:https?://\S+|(?:www\.)?linkedin\.com/\S+|github\.com/\S+)", re.IGNORECASE)
_CITY_STATE = re.compile(r"\b([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,3}),\s*([A-Z]{2})\b(?:\s+\d{5})?")
_BULLET_PREFIX = re.compile(r"^[•▪●◦‣⁃∙·■►➢✓✔\-*–—>]+\s*")
_BULLET_ONLY = re.compile(r"^[•▪●◦‣⁃∙·■►➢✓✔\-*–—>]+$")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_WORD = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = rf"(?:{_MONTH_WORD}\.?,?\s+(?:\d{{1,2}},?\s+)?\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_RANGE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:-|–|—|to|until)\s*(?P<end>{_DATE}|present|current|now|today|ongoing)", re.IGNORECASE
)
_TITLE_WORDS = re.compile(
    r"\b(engineer|developer|manager|analyst|consultant|director|specialist|lead|architect|designer|scientist|"
    r"administrator|coordinator|associate|intern|officer|technician|assistant|head|vp|vice president|president|"
    r"accountant|nurse|teacher|representative|executive|supervisor|programmer|tester|owner|founder|partner|"
    r"advisor|strategist|recruiter|editor|writer|instructor|researcher|operator|agent|clerk|controller|auditor)\b",
    re.IGNORECASE,
)
_COMPANY_WORDS = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|company|co|group|technologies|technology|solutions|systems|labs|bank|"
    r"university|college|hospital|health|partners|consulting|services|agency|associates|gmbh|plc|limited)\b\.?",
    re.IGNORECASE,
)
_DEGREE = re.compile(
    r"\b(bachelor'?s?|master'?s?|mba|ph\.?\s?d|doctor(?:ate)?|associate'?s?|diploma|b\.?\s?sc?\.?|b\.?\s?a\.?|"
    r"m\.?\s?sc?\.?|m\.?\s?a\.?|b\.?\s?eng|m\.?\s?eng|b\.?\s?tech|m\.?\s?tech|high school|ged)\b",
    re.IGNORECASE,
)
_INSTITUTION = re.compile(r"\b(university|college|institute|school|academy|polytechnic)\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(19[5-9]\d|20\d{2})\b")


def _normalize_date(value: str) -> str | None:
    value = value.strip().lower().rstrip(".")
    if value in {"present", "current", "now", "today", "ongoing"}:
        return "present"
    if match := re.fullmatch(r"(\d{1,2})/(\d{4})", value):
        month = int(match.group(1))
        return f"{match.group(2)}-{month:02d}" if 1 <= month <= 12 else match.group(2)
    if match := re.fullmatch(rf"({_MONTH_WORD})\.?,?\s+(?:\d{{1,2}},?\s+)?(\d{{4}})", value, re.IGNORECASE):
        month = _MONTHS.get(match.group(1)[:4].lower().rstrip("t"), _MONTHS.get(match.group(1)[:3].lower()))
        return f"{match.group(2)}-{month:02d}" if month else match.group(2)
    if re.fullmatch(r"\d{4}", value):
        return value
    return None


def join_wrapped(left: str, right: str) -> str:
    """Rejoins a line broken by the PDF: "high-" + "performance" -> "high-performance", else with a space."""
    if not left:
        return right
    if left.endswith("-") and right[:1].isalnum() and not left.endswith(" -"):
        return left + right
    return f"{left} {right}"


def _strip_bullet(line: str) -> str:
    return _BULLET_PREFIX.sub("", line).strip()


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_PREFIX.match(line))


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        heading = None
        if 0 < len(stripped) <= 45:
            heading = next((name for name, pattern in _HEADING.items() if pattern.match(stripped)), None)
        if heading:
            current = heading
            sections.setdefault(current, [])
            continue
        inline = None
        if len(stripped) <= 400 and current not in _ROLE_SECTIONS:
            for name, pattern in _INLINE_HEADING.items():
                if (match := pattern.match(stripped)) and name in {"skills", "summary", "certifications", "authorization"}:
                    inline = (name, match.group("rest"))
                    break
        if inline:
            current = inline[0]
            sections.setdefault(current, []).append(inline[1])
            continue
        if current is None:
            header.append(line)
        else:
            sections[current].append(line)
    return header, sections


def _looks_like_name(line: str) -> bool:
    words = line.split()
    if not 2 <= len(words) <= 5 or len(line) > 50:
        return False
    if any(ch.isdigit() for ch in line) or "@" in line or "|" in line or "," in line:
        return False
    if any(pattern.match(line) for pattern in _HEADING.values()):
        return False
    return all(w[0].isupper() or not w[0].isalpha() for w in words if w)


def _name_case(line: str) -> str:
    return " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w for w in line.split())


def _parse_header(header: list[str], text: str) -> dict[str, str | None | list[str]]:
    lines = [line for line in header if line.strip()][:10] or [line for line in text.split("\n") if line.strip()][:8]
    name = next((_name_case(line) for line in lines[:5] if _looks_like_name(line)), None)
    email_match = _EMAIL.search(text)
    phone = None
    for match in _PHONE.finditer("\n".join(lines) or text):
        digits = re.sub(r"\D", "", match.group(0))
        if 10 <= len(digits) <= 15:
            phone = match.group(0).strip()
            break
    location = None
    for line in lines:
        for part in re.split(r"[|•·\t]", line):
            part = part.strip()
            if "@" in part or "http" in part.lower():
                continue
            if match := _CITY_STATE.search(part):
                location = match.group(0)
                break
        if location:
            break
    links = clean_links(sorted({m.group(0).rstrip(".,;)") for m in _LINK.finditer("\n".join(lines))}))
    headline = None
    if name:
        index = next((i for i, line in enumerate(lines) if _name_case(line) == name), -1)
        for line in lines[index + 1 : index + 3]:
            if line and "@" not in line and not _PHONE.search(line) and not _CITY_STATE.search(line) and len(line) <= 90:
                headline = line.strip(" |")
                break
    return {
        "name": name,
        "email": email_match.group(0) if email_match else None,
        "phone": phone,
        "location": location,
        "links": links,
        "headline": headline,
    }


_STRONG_SEP = re.compile(r"\s*(?:\||•|·|▪|●)\s*")
_WEAK_SEP = re.compile(r"\s*(?:–|—|\s-\s|,)\s*")
# "Okta, Inc." must not be split at its comma.
_SUFFIX_COMMA = re.compile(r",(?=\s*(?:inc|llc|ltd|l\.l\.c|corp|co|llp|plc|gmbh|p\.?c)\b\.?)", re.IGNORECASE)
_EMPLOYMENT_TYPE = re.compile(
    r"^(?:full[- ]?time|part[- ]?time|contract(?:or)?|contract[- ]to[- ]hire|freelance|internship|temporary|seasonal|"
    r"self[- ]employed|permanent|w-?2|c2c|corp[- ]to[- ]corp)$",
    re.IGNORECASE,
)
_WORKPLACE = {"remote", "hybrid", "on-site", "onsite", "in-office"}
_TRAILING_PLACE = re.compile(r"^(?P<org>.+?),\s*(?P<place>[A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,3},\s*[A-Z]{2}|Remote|Hybrid)$")
# Icon fonts in PDFs leave private-use characters: at the start of a line they are bullets, elsewhere separators.
_ICON_START = re.compile(r"^\s*[-]+\s*")
_ICON = re.compile(r"\s*[-]+\s*")


def _replace_icons(line: str) -> str:
    return _ICON.sub(" | ", _ICON_START.sub("• ", line)).strip()
# Role-level technology lists ("Technologies: Rust, Kafka") describe the role; they are not accomplishments.
_LABEL_LINE = re.compile(
    r"^(?:technologies(?: used)?|technology stack|tech(?:nology)? stack|stack|environment|tools(?: used)?|"
    r"tools (?:and|&) technologies|key (?:skills|technologies)|skills used)\s*:",
    re.IGNORECASE,
)
_TERMINAL = re.compile(r"[.!?;:]\s*$")
_ORG_JOINERS = {"of", "for", "and", "&", "to", "in", "at", "the", "on"}


def _strong_pieces(text: str) -> list[str]:
    return [p.strip() for p in _STRONG_SEP.split(text) if p.strip()]


def _is_anchor(line: str) -> bool:
    """A line holding a role's date range, e.g. "Jun 2020 – Jun 2022" or "Acme | Engineer | 2019 - Present"."""
    return bool(_RANGE.search(line)) and len(line) <= 160 and not _is_bullet(line) and not line.rstrip().endswith(".")


def _anchor_rest(line: str) -> str:
    match = _RANGE.search(line)
    if match is None:
        return line.strip()
    return (line[: match.start()] + " " + line[match.end() :]).strip(" |,-–—()")


def _header_like(line: str) -> bool:
    """Short, label-free lines such as "Senior Engineer" or "Acme Corp | Remote" (not bullets or sentences)."""
    text = line.strip()
    return bool(text) and not (
        _is_bullet(text)
        or _RANGE.search(text)
        or len(text) > 90
        or text.endswith(".")
        or _LABEL_LINE.match(text)
        or ":" in text[:30]
        or text.count(",") >= 3
        or text[:1].islower()
    )


def _split_inline_title(piece: str) -> tuple[str, str] | None:
    """ "AI Software Engineer Yale University" -> ("AI Software Engineer", "Yale University")."""
    words = piece.split()
    last = max((i for i, w in enumerate(words) if _TITLE_WORDS.fullmatch(w.strip(".,()"))), default=None)
    if last is None or last == len(words) - 1:
        return None
    rest = words[last + 1 :]
    if rest[0].lower() in _ORG_JOINERS or not rest[0][:1].isupper() or re.fullmatch(r"(?:[IVX]+|\d+)", rest[0]):
        return None
    return " ".join(words[: last + 1]), " ".join(rest)


def _split_title_employer(header_text: str) -> tuple[str, str, str | None]:
    header_text = re.sub(r"\s{2,}", " | ", header_text.strip(" |,-–—"))
    protected = _SUFFIX_COMMA.sub("\x00", header_text)
    if " at " in protected.lower():
        idx = protected.lower().index(" at ")
        title, employer = protected[:idx], protected[idx + 4 :]
        pieces = [title, *re.split(r"\s*[|•·,]\s*", employer)]
    else:
        pieces = _strong_pieces(protected)
        if len(pieces) == 1:
            pieces = [p.strip() for p in _WEAK_SEP.split(protected) if p.strip()]
    pieces = [p.replace("\x00", ",").strip() for p in pieces if p.strip()]
    # "Workato, Mountain View, CA" -> employer and location.
    split_pieces: list[str] = []
    for piece in pieces:
        if (match := _TRAILING_PLACE.match(piece)) and not _CITY_STATE.fullmatch(piece):
            split_pieces += [match.group("org").strip(), match.group("place").strip()]
        else:
            split_pieces.append(piece)
    pieces = split_pieces
    location = None
    remaining: list[str] = []
    for piece in pieces:
        if _EMPLOYMENT_TYPE.match(piece):
            continue
        if location is None and (_CITY_STATE.fullmatch(piece) or piece.lower() in _WORKPLACE):
            location = piece
        elif re.fullmatch(r"[A-Z]{2}", piece) and remaining and location is None:
            location = f"{remaining.pop()}, {piece}"
        else:
            remaining.append(piece)

    def ends_with_title_word(piece: str) -> bool:
        words = piece.split()
        return bool(words) and bool(_TITLE_WORDS.fullmatch(words[-1].strip(".,")))

    title = (
        next((p for p in remaining if ends_with_title_word(p)), "")
        or next((p for p in remaining if _TITLE_WORDS.search(p) and not _COMPANY_WORDS.search(p)), "")
        or next((p for p in remaining if _TITLE_WORDS.search(p) and len(remaining) > 1), "")
    )
    employer = next((p for p in remaining if p != title and _COMPANY_WORDS.search(p)), "")
    if not employer:
        employer = next((p for p in remaining if p != title), "")
    if not title and len(remaining) == 1 and (split := _split_inline_title(remaining[0])):
        title, employer = split
    elif not employer and title and (split := _split_inline_title(title)):
        title, employer = split
    if not title:
        title = next((p for p in remaining if p != employer), "")
    return title[:200], employer[:200], location


def _header_start(lines: list[str], anchor: int, floor: int) -> int:
    """First line of a role's header: the date line itself, or up to two short lines above it."""
    rest = _anchor_rest(lines[anchor])
    if len(_strong_pieces(rest)) >= 2 or " at " in rest.lower():
        return anchor  # "Acme | Senior Engineer | 2020 - Present" is complete on its own
    start = anchor
    for back in (1, 2):
        j = anchor - back
        if j <= floor or j < 0 or not _header_like(lines[j]):
            break
        start = j
        title, employer, _ = _split_title_employer(" | ".join(p for p in (lines[j], rest) if p))
        if title and employer:
            break
    return start


def _merge_bullet_glyphs(lines: list[str]) -> list[str]:
    """PDFs often put "•" on its own line with the text on the next one."""
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _BULLET_ONLY.match(line.strip()) and index + 1 < len(lines) and lines[index + 1].strip():
            merged.append(f"• {lines[index + 1].strip()}")
            index += 2
            continue
        merged.append(line)
        index += 1
    return merged


def _is_tech_list(text: str) -> bool:
    """ "Rust | Go | Python | Kafka |" — a role's technology list, not an accomplishment."""
    if _RANGE.search(text):
        return False  # "Acme | Senior Engineer | 2022 - Present" is a role header
    pieces = [p for p in re.split(r"\s*[|•·]\s*", text) if p.strip()]
    return text.count("|") >= 2 and all(len(p.split()) <= 4 and not p.endswith(".") for p in pieces)


def _role_bullets(lines: list[str]) -> list[Bullet]:
    bullets: list[Bullet] = []
    in_label = False
    label_continues = False
    last_was_glyph = False
    for line in lines:
        is_glyph = _is_bullet(line)
        text = _strip_bullet(line)
        if not text:
            continue
        if _LABEL_LINE.match(text) or _is_tech_list(text) or (in_label and label_continues):
            in_label = True
            label_continues = text.endswith(("|", ",", "&"))
            continue
        if in_label and not is_glyph and text[:1].islower():
            continue  # wrapped remainder of a "Technologies:" line
        in_label = False
        wraps = bullets and not is_glyph and (text[:1].islower() or (last_was_glyph and not _TERMINAL.search(bullets[-1].text)))
        if wraps:
            bullets[-1].text = join_wrapped(bullets[-1].text, text)[:1000]
            continue
        bullets.append(Bullet(text=text[:1000]))
        last_was_glyph = is_glyph
    return bullets


def _parse_experience(lines: list[str]) -> list[Role]:
    lines = [line for line in lines if line.strip()]
    anchors = [i for i, line in enumerate(lines) if _is_anchor(line)]
    header_starts: list[int] = []
    floor = -1
    for anchor in anchors:
        header_starts.append(_header_start(lines, anchor, floor))
        floor = anchor

    roles: list[Role] = []
    adjacent: list[bool] = []  # a title line directly under the previous one: same company, promoted
    for idx, anchor in enumerate(anchors):
        start = header_starts[idx]
        end = header_starts[idx + 1] if idx + 1 < len(anchors) else len(lines)
        date_match = _RANGE.search(lines[anchor])
        assert date_match is not None
        header_parts = [lines[i] for i in range(start, anchor)]
        if rest := _anchor_rest(lines[anchor]):
            header_parts.append(rest)
        body_start = anchor + 1
        title, employer, location = _split_title_employer(" | ".join(header_parts))
        # "Senior Engineer / Jun 2022 – Present / Acme • Full-time": the employer line comes after the dates.
        if (not employer or not title) and body_start < end and _header_like(lines[body_start]) and len(lines[body_start]) <= 80:
            header_parts.append(lines[body_start])
            body_start += 1
            title, employer, location = _split_title_employer(" | ".join(header_parts))
        adjacent.append(idx > 0 and start == anchor and anchors[idx - 1] == anchor - 1)
        roles.append(
            Role(
                employer=employer,
                title=title,
                location=location,
                start=_normalize_date(date_match.group("start")),
                end=_normalize_date(date_match.group("end")),
                bullets=_role_bullets(lines[body_start:end])[:25],
            )
        )

    # Promotions listed under one company header share its name and bullets; keep the bullets with the latest title.
    block_start = 0
    for idx in range(1, len(roles) + 1):
        if idx < len(roles) and adjacent[idx]:
            if not roles[idx].employer:
                roles[idx].employer = roles[block_start].employer
                roles[idx].location = roles[idx].location or roles[block_start].location
            continue
        block = roles[block_start:idx]
        if len(block) > 1 and not any(r.bullets for r in block[:-1]) and block[-1].bullets:
            block[0].bullets, block[-1].bullets = block[-1].bullets, []
        block_start = idx
    return roles


def _recover_experience(sections: dict[str, list[str]]) -> None:
    """No "Experience" heading: roles then follow the previous section (often Skills). Move them back out."""
    if sections.get("experience"):
        return
    for name in ("skills", "summary", "other", "projects", "certifications"):
        lines = [line for line in sections.get(name, []) if line.strip()]
        for index, line in enumerate(lines):
            if not _is_anchor(line):
                continue
            start = _header_start(lines, index, -1)
            header = " ".join(lines[start : index + 2])
            following = lines[index + 1 : index + 6]
            looks_like_role = _TITLE_WORDS.search(header) and any(_is_bullet(x) or len(x) > 60 for x in following)
            if looks_like_role:
                sections["experience"] = lines[start:]
                sections[name] = lines[:start]
                return


def _parse_education(lines: list[str]) -> list[Education]:
    entries: list[Education] = []
    current: Education | None = None
    for raw in lines:
        line = _strip_bullet(raw)
        if not line:
            continue
        has_institution = bool(_INSTITUTION.search(line))
        has_degree = bool(_DEGREE.search(line))
        year_match = _YEAR.findall(line)
        # Drop dates first ("Aug 22, 2016 - Aug 15, 2020") so only the degree and school remain.
        cleaned = _YEAR.sub("", _RANGE.sub("", line)).strip(" ,|-–—()")
        if has_institution and has_degree:
            # "B.S. Computer Science, University of X, 2016" -> split the two parts.
            parts = [p.strip() for p in re.split(r"\s*[,|–—]\s*|\s+-\s+", cleaned) if re.search(r"[A-Za-z]{2}", p)]
            institution = next((p for p in parts if _INSTITUTION.search(p)), cleaned)
            degree = ", ".join(p for p in parts if p != institution) or None
            current = Education(institution=institution[:300], degree=degree[:200] if degree else None)
            entries.append(current)
        elif has_institution:
            if current is None or current.institution:
                current = Education(institution=cleaned[:300])
                entries.append(current)
            else:
                current.institution = cleaned[:300]
        elif has_degree:
            if current is None or current.degree:
                current = Education(institution="")
                entries.append(current)
            current.degree = cleaned[:200]
        if year_match and current is not None:
            current.end_year = int(year_match[-1])
    return [e for e in entries if e.institution or e.degree][:10]


def balance_parentheses(name: str) -> str:
    """Drops separators and unmatched brackets left after removing a year, keeping "(CKA)" intact."""
    name = re.sub(r"\(\s*\)", "", name).strip(" ,-–—")
    while name.endswith("(") or (name.endswith(")") and name.count(")") > name.count("(")):
        name = name[:-1].rstrip(" ,-–—")
    while name.startswith(")") or (name.startswith("(") and name.count("(") > name.count(")")):
        name = name[1:].lstrip(" ,-–—")
    if name.count("(") == name.count(")") + 1 and name.rfind("(") > name.rfind(")"):
        name += ")"  # "Administrator (CKA" -> "Administrator (CKA)"
    return name


def _parse_certifications(lines: list[str]) -> list[Certification]:
    certifications: list[Certification] = []
    for raw in lines:
        for part in re.split(r"\s*[;•|]\s*", _strip_bullet(raw)):
            years = _YEAR.findall(part)
            name = balance_parentheses(_YEAR.sub("", part))
            if 3 <= len(name) <= 300:
                certifications.append(Certification(name=name, year=int(years[-1]) if years else None))
    return certifications[:20]


def _join_wrapped_lists(lines: list[str]) -> list[str]:
    """ "Reliability: Distributed Systems, Performance" + "Optimization, Production Reliability" is one list line."""
    joined: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        previous = joined[-1] if joined else ""
        explicit = previous.endswith((",", "|", "&"))  # "Kafka | Redis |" + "Linux | REST APIs"
        # A long comma list that ran into the margin; short lines are separate lists.
        wrapped = "," in previous and len(previous) >= 70 and not re.search(r"[,;.|]$", previous)
        continues = bool(previous) and (explicit or wrapped) and not _is_bullet(line) and ":" not in line[:40]
        if continues:
            joined[-1] = join_wrapped(previous, line)
        else:
            joined.append(line)
    return joined


def _parse_skills(lines: list[str], source: str = "Skills section") -> list[ExtractedSkill]:
    skills: dict[str, ExtractedSkill] = {}
    for raw in _join_wrapped_lists(lines):
        line = _strip_bullet(raw)
        if not line:
            continue
        if ":" in line[:40]:
            line = line.split(":", 1)[1]
        for token in re.split(r"\s*[,;|•·\t]\s*|\s{2,}", line):
            token = token.strip(" -–—*").rstrip(".")  # keep the dot of ".NET"
            if not token or len(token) > 50 or len(token.split()) > 6 or token.isdigit():
                continue
            key = normalize_skill(token)
            if key and key not in skills:
                skills[key] = ExtractedSkill(name=token, evidence=f"{source}: {_strip_bullet(raw)}"[:500])
    return list(skills.values())[:80]


def _role_technologies(lines: list[str]) -> list[ExtractedSkill]:
    """Skills listed inside roles ("Technologies: Rust, Kafka" or "Rust | Go | Kafka |")."""
    picked: list[str] = []
    continuing = False
    for raw in lines:
        text = _strip_bullet(raw)
        if not text:
            continue
        if continuing and not _is_bullet(raw) and not _is_anchor(text) and (picked[-1].endswith(("|", ",", "&")) or text[:1].islower()):
            picked[-1] = join_wrapped(picked[-1], text)  # the list wrapped onto this line
        elif _LABEL_LINE.match(text) or _is_tech_list(text):
            picked.append(text)
            continuing = True
            continue
        else:
            continuing = False
    return _parse_skills(picked, source="Role technologies")


def parse_resume(text: str) -> ExtractedResume:
    lines = _merge_bullet_glyphs([_replace_icons(line) for line in text.split("\n")])
    header, sections = _split_sections(lines)
    _recover_experience(sections)
    contact = _parse_header(header, text)
    warnings: list[str] = []

    roles = _parse_experience(sections.get("experience", []))
    if not sections.get("experience"):
        warnings.append("No experience section was recognised; add roles manually.")
    elif not roles:
        warnings.append("Experience section found but no dated roles were recognised; add roles manually.")

    skills = _parse_skills(sections.get("skills", []))
    listed = {normalize_skill(s.name) for s in skills}
    for skill in _role_technologies(sections.get("experience", [])):
        if normalize_skill(skill.name) not in listed:
            skills.append(skill)
            listed.add(normalize_skill(skill.name))
    for canonical, evidence in detect_known_skills(text).items():
        if normalize_skill(canonical) not in listed:
            skills.append(ExtractedSkill(name=canonical, evidence=evidence[:500]))
            listed.add(canonical)

    summary_lines = [_strip_bullet(line) for line in sections.get("summary", []) if line.strip()]
    authorization_lines = [_strip_bullet(line) for line in sections.get("authorization", []) if line.strip()]
    profile = CandidateProfile(
        work_authorization="; ".join(authorization_lines)[:300] or None,
        headline=contact["headline"],  # type: ignore[arg-type]
        summary=functools.reduce(join_wrapped, summary_lines, "")[:4000] or None,
        employment=roles,
        education=_parse_education(sections.get("education", [])),
        certifications=_parse_certifications(sections.get("certifications", [])),
        links=contact["links"],  # type: ignore[arg-type]
    )
    if not contact["name"]:
        warnings.append("Candidate name wasn't recognised; it was taken from the file name.")
    return ExtractedResume(
        full_name=contact["name"],  # type: ignore[arg-type]
        email=contact["email"],  # type: ignore[arg-type]
        phone=contact["phone"],  # type: ignore[arg-type]
        location=contact["location"],  # type: ignore[arg-type]
        profile=profile,
        skills=skills[:120],
        source="heuristic",
        warnings=warnings,
    )
