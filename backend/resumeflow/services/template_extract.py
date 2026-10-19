"""Reads the visual style of an existing resume (DOCX or text-based PDF) into a TemplateStyle.

Only the look is copied: fonts, sizes, colors, heading treatment, alignment and
margins. Content and layout stay with the deterministic renderer, so a style taken
from one person's resume can be used for anyone without carrying their text along.
"""

from __future__ import annotations

import io
import re
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from resumeflow.domain.templates import TemplateStyle

HEADING_WORDS = {
    "summary", "professional summary", "career summary", "executive summary", "profile", "professional profile",
    "objective", "career objective", "about me", "about", "experience", "work experience", "professional experience",
    "relevant experience", "employment", "employment history", "work history", "career history", "skills",
    "technical skills", "core skills", "key skills", "skills and abilities", "core competencies", "competencies",
    "areas of expertise", "expertise", "technologies", "tools", "technical proficiencies", "education",
    "education and training", "academic background", "certifications", "certificates", "certification",
    "licenses and certifications", "certifications and licenses", "projects", "key projects", "selected projects",
    "achievements", "key achievements", "accomplishments", "awards", "honors and awards", "publications", "languages",
    "volunteer experience", "volunteering", "interests", "references", "training", "qualifications",
    "highlights", "career highlights", "professional development", "affiliations", "leadership", "contact",
}

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_YEAR = re.compile(r"\b(19|20)\d{2}\b|\bpresent\b", re.IGNORECASE)
_CONTACT = re.compile(r"@|\d{3}[\s.)-]*\d{3}|\||linkedin|github|www\.", re.IGNORECASE)


@dataclass
class _Run:
    text: str
    font: str | None
    size: float | None
    bold: bool
    caps: bool
    color: str | None


@dataclass
class _Para:
    runs: list[_Run]
    align: Literal["left", "center"] = "left"
    rule: str | None = None  # color of a bottom rule ("auto" = default), None = no rule
    x0: float | None = None  # PDF only

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs).strip()

    def biggest(self) -> float:
        return max((r.size or 0) for r in self.runs) if self.runs else 0


@dataclass
class Extracted:
    style: TemplateStyle
    notes: list[str] = field(default_factory=list)


def is_heading(text: str) -> bool:
    cleaned = re.sub(r"[^a-z& ]", "", text.lower()).replace("&", "and")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return 0 < len(text.strip()) <= 45 and cleaned in HEADING_WORDS


def _clean_hex(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lstrip("#").upper()
    return value if re.fullmatch(r"[0-9A-F]{6}", value) else None


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _readable(hex_color: str | None, fallback: str, limit: float = 0.8) -> str:
    """White or pale text (e.g. a name printed on a dark banner) would vanish on a white page."""
    return hex_color if hex_color and _luminance(hex_color) <= limit else fallback


def _mode(counter: Counter, default):
    return counter.most_common(1)[0][0] if counter else default


def _round_half(value: float) -> float:
    return round(value * 2) / 2


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------- shared analysis


def _analyse(paragraphs: list[_Para], margin: float | None, space_after: float | None, notes: list[str]) -> TemplateStyle:
    defaults = TemplateStyle()
    content = [p for p in paragraphs if p.text]
    if not content:
        raise ValueError("No text found to read a style from.")

    # The name: the largest text near the top.
    top = content[:6]
    name = max(top, key=lambda p: p.biggest())
    headings = [p for p in content if is_heading(p.text) and p is not name]
    heading_ids = {id(p) for p in headings}
    body = [p for p in content if p is not name and id(p) not in heading_ids]

    weights_font: Counter = Counter()
    weights_size: Counter = Counter()
    weights_color: Counter = Counter()
    for paragraph in body:
        for run in paragraph.runs:
            n = len(run.text.strip())
            if not n:
                continue
            if run.font:
                weights_font[run.font] += n
            if run.size:
                weights_size[_round_half(run.size)] += n
            weights_color[run.color or "000000"] += n
    body_font = _mode(weights_font, defaults.font)
    body_size = _clamp(_mode(weights_size, defaults.size), 7, 14)
    text_color = _readable(_mode(weights_color, "000000"), "000000", 0.6)

    name_run = max(name.runs, key=lambda r: (r.size or 0, len(r.text)))
    name_size = name_run.size or max(body_size + 8, 16)
    if name_size < body_size + 1.5:
        notes.append("The name isn't larger than the body text, so a larger default size is used.")
        name_size = max(body_size + 8, 16)

    if headings:
        h_runs = [r for p in headings for r in p.runs if r.text.strip()]
        heading_size = _mode(Counter(_round_half(r.size) for r in h_runs if r.size), body_size + 1)
        heading_bold = sum(r.bold for r in h_runs) * 2 >= len(h_runs)
        heading_caps = all(r.caps or r.text.strip().upper() == r.text.strip() for r in h_runs)
        heading_color = _mode(Counter(r.color or text_color for r in h_runs), text_color)
        heading_font = _mode(Counter(r.font for r in h_runs if r.font), body_font)
        ruled = [p.rule for p in headings if p.rule]
        heading_rule = len(ruled) * 2 >= len(headings)
        rule_color = _clean_hex(_mode(Counter(ruled), None)) if heading_rule else None
    else:
        notes.append("No section headings (Summary, Experience…) were recognised; heading style uses defaults.")
        heading_size, heading_bold, heading_caps = body_size + 1, True, True
        heading_color, heading_font, heading_rule, rule_color = text_color, body_font, True, None

    # Contact line and dates are often set in a softer color.
    soft: Counter = Counter()
    index = next(i for i, p in enumerate(content) if p is name)
    for paragraph in content[index + 1 : index + 4]:
        if _CONTACT.search(paragraph.text):
            for run in paragraph.runs:
                if run.color and run.color != text_color and run.text.strip():
                    soft[run.color] += len(run.text)
    if not soft:
        for paragraph in body:
            for run in paragraph.runs:
                if _YEAR.search(run.text) and run.color and run.color != text_color:
                    soft[run.color] += len(run.text)
    muted = _readable(_mode(soft, None), "595959", 0.75)

    heading_color = _readable(heading_color, text_color)
    return TemplateStyle(
        font=body_font,
        heading_font=heading_font or body_font,
        size=body_size,
        name_size=_clamp(_round_half(name_size), 11, 40),
        name_bold=name_run.bold,
        name_align=name.align,
        name_color=_readable(name_run.color or text_color, text_color),
        heading_size=_clamp(heading_size, 8, 18),
        heading_bold=heading_bold,
        heading_caps=heading_caps,
        heading_rule=heading_rule,
        heading_color=heading_color,
        rule_color=_readable(rule_color, heading_color, 0.9),
        text_color=text_color,
        muted_color=muted,
        margin=round(_clamp(margin if margin is not None else defaults.margin, 0.3, 1.5), 2),
        paragraph_space=_clamp(space_after if space_after is not None else defaults.paragraph_space, 0, 12),
    )


# ---------------------------------------------------------------- DOCX


def _safe_xml(data: bytes):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def _theme(archive: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, str]]:
    fonts: dict[str, str] = {}
    colors: dict[str, str] = {}
    name = next((n for n in archive.namelist() if n.startswith("word/theme/") and n.endswith(".xml")), None)
    if not name:
        return fonts, colors
    root = _safe_xml(archive.read(name))
    for kind in ("major", "minor"):
        latin = root.find(f".//{{{_A}}}{kind}Font/{{{_A}}}latin")
        if latin is not None and latin.get("typeface"):
            fonts[kind] = latin.get("typeface")
    scheme = root.find(f".//{{{_A}}}clrScheme")
    if scheme is not None:
        for child in scheme:
            tag = etree.QName(child).localname
            srgb = child.find(f"{{{_A}}}srgbClr")
            sys_color = child.find(f"{{{_A}}}sysClr")
            value = srgb.get("val") if srgb is not None else sys_color.get("lastClr") if sys_color is not None else None
            if value:
                colors[tag] = value
    return fonts, colors


_THEME_COLOR_KEYS = {
    "dark1": "dk1", "text1": "dk1", "dark2": "dk2", "text2": "dk2", "light1": "lt1", "background1": "lt1",
    "light2": "lt2", "background2": "lt2", "hyperlink": "hlink", "followedHyperlink": "folHlink",
    **{f"accent{i}": f"accent{i}" for i in range(1, 7)},
}


class _DocxStyles:
    def __init__(self, archive: zipfile.ZipFile):
        self.theme_fonts, self.theme_colors = _theme(archive)
        self.styles: dict[str, etree._Element] = {}
        self.defaults_rpr = None
        self.defaults_ppr = None
        self.default_paragraph_style = None
        if "word/styles.xml" in archive.namelist():
            root = _safe_xml(archive.read("word/styles.xml"))
            for style in root.findall(f"{{{_W}}}style"):
                style_id = style.get(f"{{{_W}}}styleId")
                if style_id:
                    self.styles[style_id] = style
                    if style.get(f"{{{_W}}}type") == "paragraph" and style.get(f"{{{_W}}}default") in ("1", "true"):
                        self.default_paragraph_style = style_id
            self.defaults_rpr = root.find(f"{{{_W}}}docDefaults/{{{_W}}}rPrDefault/{{{_W}}}rPr")
            self.defaults_ppr = root.find(f"{{{_W}}}docDefaults/{{{_W}}}pPrDefault/{{{_W}}}pPr")

    def chain(self, style_id: str | None, kind: str) -> list:
        """rPr/pPr elements of a style and its ancestors, nearest first."""
        found, seen = [], set()
        while style_id and style_id in self.styles and style_id not in seen:
            seen.add(style_id)
            style = self.styles[style_id]
            element = style.find(f"{{{_W}}}{kind}")
            if element is not None:
                found.append(element)
            based = style.find(f"{{{_W}}}basedOn")
            style_id = based.get(f"{{{_W}}}val") if based is not None else None
        return found

    def color(self, element) -> str | None:
        value = element.get(f"{{{_W}}}val")
        if value and value.lower() != "auto" and _clean_hex(value):
            return _clean_hex(value)
        theme = element.get(f"{{{_W}}}themeColor")
        return _clean_hex(self.theme_colors.get(_THEME_COLOR_KEYS.get(theme or "", ""), "")) if theme else None

    def font(self, rfonts) -> str | None:
        name = rfonts.get(f"{{{_W}}}ascii") or rfonts.get(f"{{{_W}}}hAnsi")
        if name:
            return name
        theme = rfonts.get(f"{{{_W}}}asciiTheme") or rfonts.get(f"{{{_W}}}hAnsiTheme") or ""
        if theme.startswith("major"):
            return self.theme_fonts.get("major")
        if theme.startswith("minor"):
            return self.theme_fonts.get("minor")
        return None


def _flag(element) -> bool:
    return element.get(f"{{{_W}}}val", "true").lower() not in ("0", "false", "off", "none")


def _first(rprs: list, tag: str):
    for rpr in rprs:
        if rpr is None:
            continue
        element = rpr.find(f"{{{_W}}}{tag}")
        if element is not None:
            return element
    return None


def _border(pprs: list, side: str) -> str | None:
    element = _first(pprs, "pBdr")
    if element is None:
        return None
    edge = element.find(f"{{{_W}}}{side}")
    if edge is None or edge.get(f"{{{_W}}}val", "none") in ("none", "nil"):
        return None
    return _clean_hex(edge.get(f"{{{_W}}}color")) or "auto"


def _docx_paragraphs(data: bytes) -> tuple[list[_Para], float | None, float | None]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        styles = _DocxStyles(archive)
        parts = [n for n in archive.namelist() if re.fullmatch(r"word/header\d*\.xml", n)] + ["word/document.xml"]
        roots = [_safe_xml(archive.read(n)) for n in parts]
    body = roots[-1].find(f"{{{_W}}}body")

    margin = None
    sect = body.find(f".//{{{_W}}}sectPr/{{{_W}}}pgMar") if body is not None else None
    if sect is not None:
        try:
            margin = (int(sect.get(f"{{{_W}}}left", "1440")) + int(sect.get(f"{{{_W}}}right", "1440"))) / 2 / 1440
        except ValueError:
            margin = None

    paragraphs: list[_Para] = []
    spacing: Counter = Counter()
    elements = [p for root in roots for p in root.iter(f"{{{_W}}}p")]
    for index, p in enumerate(elements):
        ppr = p.find(f"{{{_W}}}pPr")
        style_el = ppr.find(f"{{{_W}}}pStyle") if ppr is not None else None
        style_id = style_el.get(f"{{{_W}}}val") if style_el is not None else styles.default_paragraph_style
        pprs = [ppr, *styles.chain(style_id, "pPr"), styles.defaults_ppr]
        para_rprs = styles.chain(style_id, "rPr")

        runs: list[_Run] = []
        for r in p.iter(f"{{{_W}}}r"):
            text = "".join(t.text or "" for t in r.iter(f"{{{_W}}}t"))
            if not text:
                continue
            rpr = r.find(f"{{{_W}}}rPr")
            r_style = rpr.find(f"{{{_W}}}rStyle") if rpr is not None else None
            char_rprs = styles.chain(r_style.get(f"{{{_W}}}val"), "rPr") if r_style is not None else []
            rprs = [rpr, *char_rprs, *para_rprs, styles.defaults_rpr]
            rfonts = _first(rprs, "rFonts")
            size = _first(rprs, "sz")
            bold = _first(rprs, "b")
            caps = _first(rprs, "caps")
            color = _first(rprs, "color")
            try:
                size_pt = int(size.get(f"{{{_W}}}val")) / 2 if size is not None else 11.0
            except (TypeError, ValueError):
                size_pt = 11.0
            runs.append(
                _Run(
                    text=text,
                    font=styles.font(rfonts) if rfonts is not None else None,
                    size=size_pt,
                    bold=_flag(bold) if bold is not None else False,
                    caps=_flag(caps) if caps is not None else False,
                    color=styles.color(color) if color is not None else None,
                )
            )
        jc = _first(pprs, "jc")
        align = "center" if jc is not None and jc.get(f"{{{_W}}}val") == "center" else "left"
        rule = _border(pprs, "bottom")
        if rule is None and index + 1 < len(elements):
            # A rule can also be the next paragraph's top border, or an empty paragraph with a bottom border.
            following = elements[index + 1]
            next_ppr = following.find(f"{{{_W}}}pPr")
            next_text = "".join(t.text or "" for t in following.iter(f"{{{_W}}}t")).strip()
            rule = _border([next_ppr], "top") or (_border([next_ppr], "bottom") if not next_text else None)
        paragraph = _Para(runs=runs, align=align, rule=rule)
        paragraphs.append(paragraph)
        # Body spacing: ignore the name block, headings and bullets (lists are usually set tighter).
        after = next((s for s in (x.find(f"{{{_W}}}spacing") for x in pprs if x is not None) if s is not None and s.get(f"{{{_W}}}after")), None)
        is_list = _first(pprs, "numPr") is not None or "list" in (style_id or "").lower()
        if after is not None and index >= 4 and not is_list and paragraph.text and not is_heading(paragraph.text):
            try:
                spacing[_round_half(int(after.get(f"{{{_W}}}after")) / 20)] += 1
            except ValueError:
                pass
    return paragraphs, margin, _mode(spacing, None)


# ---------------------------------------------------------------- PDF

_FONT_ALIASES = {
    "helv": "Arial", "helvetica": "Arial", "arial": "Arial", "arialmt": "Arial", "arialnarrow": "Arial Narrow",
    "timesnewroman": "Times New Roman", "times": "Times New Roman", "tiro": "Times New Roman", "timesroman": "Times New Roman",
    "cour": "Courier New", "courier": "Courier New", "couriernew": "Courier New", "calibri": "Calibri",
    "calibrilight": "Calibri Light", "cambria": "Cambria", "georgia": "Georgia", "garamond": "Garamond",
    "ebgaramond": "EB Garamond", "verdana": "Verdana", "tahoma": "Tahoma", "trebuchetms": "Trebuchet MS",
    "segoeui": "Segoe UI", "bookantiqua": "Book Antiqua", "palatinolinotype": "Palatino Linotype",
    "palatino": "Palatino Linotype", "centurygothic": "Century Gothic", "gillsansmt": "Gill Sans MT",
    "gillsans": "Gill Sans MT", "lato": "Lato", "roboto": "Roboto", "opensans": "Open Sans",
    "sourcesanspro": "Source Sans Pro", "sourcesans": "Source Sans Pro", "aptos": "Aptos",
    "helveticaneue": "Helvetica Neue", "franklingothic": "Franklin Gothic Book", "constantia": "Constantia",
    "corbel": "Corbel", "candara": "Candara", "didot": "Didot", "montserrat": "Montserrat", "raleway": "Raleway",
    "merriweather": "Merriweather", "inter": "Inter", "nunito": "Nunito", "poppins": "Poppins",
}
_WEIGHT_SUFFIX = re.compile(
    r"(?:Bold|Italic|Regular|Oblique|Semibold|SemiBold|Demi|Light|Medium|Black|Heavy|Condensed|Book|Roman|It|Bd)+$"
)


def normalize_pdf_font(name: str) -> str:
    base = name.split("+", 1)[-1]  # subset prefix, e.g. ABCDEF+Calibri-Bold
    base = re.split(r"[-,]", base)[0]
    base = re.sub(r"(?:PSMT|PS|MT)$", "", base)
    key = re.sub(r"[^a-z]", "", base.lower())
    if key in _FONT_ALIASES:
        return _FONT_ALIASES[key]
    stripped = _WEIGHT_SUFFIX.sub("", base) or base
    key = re.sub(r"[^a-z]", "", stripped.lower())
    if key in _FONT_ALIASES:
        return _FONT_ALIASES[key]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stripped).strip() or "Calibri"


def _pdf_paragraphs(data: bytes) -> tuple[list[_Para], float | None]:
    import pymupdf

    paragraphs: list[_Para] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if document.page_count == 0:
            raise ValueError("The PDF has no pages.")
        page = document[0]
        width = page.rect.width
        rules: list[tuple[float, str]] = []
        for drawing in page.get_drawings():
            color = drawing.get("color") or drawing.get("fill")
            hex_color = "".join(f"{round(c * 255):02X}" for c in color[:3]) if color else "000000"
            for item in drawing.get("items", []):
                if item[0] == "l" and abs(item[1].y - item[2].y) < 1.5 and abs(item[2].x - item[1].x) > width * 0.35:
                    rules.append((item[1].y, hex_color))
                elif item[0] == "re" and item[1].height < 3 and item[1].width > width * 0.35:
                    rules.append((item[1].y0, hex_color))
        min_x = width
        for page_index in range(min(2, document.page_count)):
            for block in document[page_index].get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    runs = [
                        _Run(
                            text=s["text"],
                            font=normalize_pdf_font(s["font"]),
                            size=float(s["size"]),
                            bold=bool(s["flags"] & 16) or bool(re.search(r"bold|black|heavy|semibold", s["font"], re.IGNORECASE)),
                            caps=False,
                            color=f"{s['color']:06X}",
                        )
                        for s in spans
                    ]
                    x0 = min(s["bbox"][0] for s in spans)
                    x1 = max(s["bbox"][2] for s in spans)
                    y1 = max(s["bbox"][3] for s in spans)
                    centered = abs((x0 + x1) / 2 - width / 2) < width * 0.06 and (x1 - x0) < width * 0.85
                    rule = None
                    if page_index == 0:
                        rule = next((c for y, c in rules if y1 - 4 <= y <= y1 + 12), None)
                        min_x = min(min_x, x0)
                    paragraphs.append(_Para(runs=runs, align="center" if centered else "left", rule=rule, x0=x0))
    margin = min_x / 72 if min_x < width else None
    return paragraphs, margin


# ---------------------------------------------------------------- entry point


def installed_fonts() -> set[str] | None:
    """Font families registered on this Windows PC (lower case), or None elsewhere."""
    if sys.platform != "win32":
        return None
    import winreg

    names: set[str] = set()
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    value = winreg.EnumValue(key, index)[0]
                    for part in re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", value).split("&"):
                        family = _WEIGHT_SUFFIX.sub("", part.strip().replace(" ", "")).lower()
                        names.add(part.strip().lower())
                        names.add(family)
        except OSError:
            continue
    return names


def _font_installed(font: str, fonts: set[str] | None) -> bool:
    if fonts is None:
        return True
    return font.lower() in fonts or font.replace(" ", "").lower() in fonts


def extract_style(fmt: Literal["docx", "pdf"], data: bytes) -> Extracted:
    notes: list[str] = []
    if fmt == "docx":
        paragraphs, margin, space_after = _docx_paragraphs(data)
    else:
        paragraphs, margin = _pdf_paragraphs(data)
        space_after = None
    try:
        style = _analyse(paragraphs, margin, space_after, notes)
    except ValueError as exc:
        raise ValueError(f"{exc} Scanned (image-only) files can't be used as templates.") from exc
    fonts = installed_fonts()
    for font in dict.fromkeys((style.font, style.heading_font)):
        if not _font_installed(font, fonts):
            notes.append(f"The font “{font}” isn't installed on this PC. Word will substitute a similar font in PDFs; install it for an exact match.")
    return Extracted(style=style, notes=notes)
