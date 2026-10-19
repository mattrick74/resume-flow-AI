import type { CSSProperties } from "react";

import type { TemplateStyle } from "@/lib/types";

/** Mirrors the backend's built-in "professional" template; used until templates load. */
export const DEFAULT_TEMPLATE_STYLE: TemplateStyle = {
  font: "Calibri",
  heading_font: "Calibri",
  size: 10.5,
  name_size: 20,
  name_bold: true,
  name_align: "left",
  name_color: "1F3A6E",
  heading_size: 11,
  heading_bold: true,
  heading_caps: true,
  heading_rule: true,
  heading_color: "1F3A6E",
  rule_color: "1F3A6E",
  text_color: "000000",
  muted_color: "555F6D",
  margin: 0.7,
  paragraph_space: 2,
};

const SERIF = /garamond|times|georgia|cambria|book antiqua|palatino|constantia|didot|merriweather|serif|baskerville|bodoni|minion|caslon|century schoolbook/i;

/** CSS font stack: the template's font first, then a look-alike generic family. */
export function fontStack(font: string) {
  return `"${font}", ${SERIF.test(font) ? "Georgia, 'Times New Roman', serif" : "Calibri, 'Segoe UI', Arial, sans-serif"}`;
}

/** Screen pixels per document point in the paper preview (13px body text at 10.5pt, as before). */
export const PX_PER_PT = 1.24;

export const hex = (value: string) => `#${value}`;

export function paperStyle(style: TemplateStyle): CSSProperties {
  return {
    fontFamily: fontStack(style.font),
    fontSize: style.size * PX_PER_PT,
    color: hex(style.text_color),
    padding: `${Math.round(style.margin * 56)}px ${Math.round(style.margin * 60)}px`,
  };
}

export function nameStyle(style: TemplateStyle, scale = 1): CSSProperties {
  return {
    fontFamily: fontStack(style.heading_font),
    fontSize: style.name_size * PX_PER_PT * scale,
    fontWeight: style.name_bold ? 700 : 400,
    color: hex(style.name_color),
    lineHeight: 1.15,
  };
}

export function headingStyle(style: TemplateStyle): CSSProperties {
  return {
    fontFamily: fontStack(style.heading_font),
    fontSize: style.heading_size * PX_PER_PT,
    fontWeight: style.heading_bold ? 700 : 400,
    color: hex(style.heading_color),
    textTransform: style.heading_caps ? "uppercase" : "none",
    letterSpacing: style.heading_caps ? "0.03em" : undefined,
    borderBottom: style.heading_rule ? `1px solid ${hex(style.rule_color)}` : "none",
  };
}

export function mutedStyle(style: TemplateStyle): CSSProperties {
  return { color: hex(style.muted_color) };
}
