"""Resume/cover-letter template styles.

A template is only visual style: fonts, sizes, colors, heading look, margins. The
section order and content come from the deterministic renderer, so every template
works for every candidate and AI never controls formatting (PRD §8).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX = re.compile(r"^[0-9A-F]{6}$")
_CUSTOM_KEY = re.compile(r"^custom:[0-9a-f-]{36}$")


class TemplateStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font: str = Field(default="Calibri", min_length=1, max_length=60, description="Body font family")
    heading_font: str = Field(default="Calibri", min_length=1, max_length=60)
    size: float = Field(default=10.5, ge=7, le=14, description="Body size in points")
    name_size: float = Field(default=20, ge=11, le=40)
    name_bold: bool = True
    name_align: Literal["left", "center"] = "left"
    name_color: str = "1F3A6E"
    heading_size: float = Field(default=11, ge=8, le=18)
    heading_bold: bool = True
    heading_caps: bool = True
    heading_rule: bool = True
    heading_color: str = "1F3A6E"
    rule_color: str = "1F3A6E"
    text_color: str = "000000"
    muted_color: str = "555F6D"
    margin: float = Field(default=0.7, ge=0.3, le=1.5, description="Page margins in inches")
    paragraph_space: float = Field(default=2, ge=0, le=12, description="Space after body paragraphs in points")

    @field_validator("name_color", "heading_color", "rule_color", "text_color", "muted_color", mode="before")
    @classmethod
    def _hex(cls, value: str) -> str:
        text = str(value).strip().lstrip("#").upper()
        if not _HEX.match(text):
            raise ValueError("Colors are 6-digit hex values such as 1F3A6E")
        return text


BUILTIN_TEMPLATES: dict[str, tuple[str, TemplateStyle]] = {
    "professional": ("Professional", TemplateStyle()),
    "compact": (
        "Compact",
        TemplateStyle(
            font="Arial",
            heading_font="Arial",
            size=9.5,
            name_size=16,
            name_color="222222",
            heading_size=10,
            heading_color="222222",
            rule_color="222222",
            muted_color="606060",
            margin=0.55,
            paragraph_space=1,
        ),
    ),
}

DEFAULT_TEMPLATE = "professional"


def is_template_key(value: str) -> bool:
    """Built-in names, or ``custom:<id>`` for a template imported from a resume."""
    return value in BUILTIN_TEMPLATES or bool(_CUSTOM_KEY.match(value))


def custom_key(template_id: str) -> str:
    return f"custom:{template_id}"
