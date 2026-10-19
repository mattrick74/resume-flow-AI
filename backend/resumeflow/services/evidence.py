"""Evidence catalogue: every verified fact of a snapshot with a stable id.

Prompts cite these ids; validation checks generated text against them.
"""

from __future__ import annotations

import re
from typing import Any


def evidence_catalogue(snap: dict[str, Any]) -> dict[str, str]:
    items: dict[str, str] = {}
    if snap.get("summary"):
        items["SUM"] = f"Summary: {snap['summary']}"
    for role in snap.get("employment", []):
        dates = f"{role.get('start') or '?'} – {role.get('end') or '?'}"
        items[f"R-{role['id']}"] = f"Role: {role['title']} at {role['employer']} ({dates})"
        for bullet in role["bullets"]:
            items[f"B-{bullet['id']}"] = f"[{role['employer']}] {bullet['text']}"
    for skill in snap.get("skills", []):
        items[f"S-{skill['id'][:8]}"] = f"Skill: {skill['name']}" + (f" — evidence: {skill['evidence']}" if skill.get("evidence") else "")
    for edu in snap.get("education", []):
        items[f"E-{edu['id']}"] = "Education: " + ", ".join(str(v) for v in (edu.get("degree"), edu.get("field"), edu.get("institution"), edu.get("end_year")) if v)
    for cert in snap.get("certifications", []):
        items[f"C-{cert['id']}"] = "Certification: " + ", ".join(str(v) for v in (cert.get("name"), cert.get("issuer"), cert.get("year")) if v)
    if snap.get("work_authorization"):
        items["AUTH"] = f"Work authorization: {snap['work_authorization']}"
    items["YRS"] = f"Years of professional experience (across the roles above): {years_phrase(snap.get('total_years_experience') or 0)}"
    return items


def years_phrase(years: float) -> str:
    """How a resume states experience: "4+" for 4.4 years, "4" for exactly 4, never "4.4"."""
    whole = int(years)
    if whole < 1:
        return "less than 1"
    return f"{whole}+" if years - whole >= 0.25 else str(whole)


def evidence_listing(snap: dict[str, Any]) -> str:
    return "\n".join(f"[{key}] {text}" for key, text in evidence_catalogue(snap).items())


def bullet_index(snap: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """bullet id -> (role id, bullet text)"""
    return {b["id"]: (role["id"], b["text"]) for role in snap.get("employment", []) for b in role["bullets"]}


_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?\s?(?:%|percent\b|[kmbx]\b|\+)?", re.IGNORECASE)


def numbers_in(text: str) -> set[str]:
    found = set()
    for match in _NUMBER.finditer(text or ""):
        value = match.group(0).lower().replace("$", "").replace(",", "").replace(" ", "").replace("percent", "%").rstrip("+")
        if value:
            found.add(value)
    return found


def snapshot_text(snap: dict[str, Any]) -> str:
    return "\n".join(evidence_catalogue(snap).values())
