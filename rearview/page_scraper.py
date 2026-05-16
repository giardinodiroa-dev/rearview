from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

JS_EXTRACT = """
(() => {
    const title = document.title || "";
    const url = window.location.href || "";

    function isVisible(el) {
        if (el === document.body || el === document.documentElement) return true;
        const style = window.getComputedStyle(el);
        if (style.display === "none") return false;
        if (style.visibility === "hidden") return false;
        if (parseFloat(style.opacity) === 0) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return false;
        return true;
    }

    const HEADING_TAGS = new Set(["H1", "H2", "H3", "H4", "H5", "H6"]);

    function isHeading(el) {
        if (HEADING_TAGS.has(el.tagName)) return true;
        if (el.getAttribute("role") === "heading") return true;
        return false;
    }

    const selector = [
        "h1","h2","h3","h4","h5","h6",
        "[role='heading']",
        "p","li","td","th","dt","dd",
        "span[class*='label']",
        "div[class*='field']",
        "input:not([type=hidden])",
        "select",
        "textarea"
    ].join(",");

    const elements = Array.from(document.querySelectorAll(selector));

    const sections = [];
    let currentHeading = "";
    let currentItems = [];
    let lastItem = null;

    function flushSection() {
        if (currentItems.length > 0 || currentHeading !== "") {
            sections.push({ heading: currentHeading, items: currentItems });
        }
    }

    for (const el of elements) {
        if (!isVisible(el)) continue;

        if (isHeading(el)) {
            flushSection();
            currentHeading = (el.innerText || el.textContent || "").trim();
            currentItems = [];
            lastItem = null;
            continue;
        }

        const tag = el.tagName;

        if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
            // Find label
            let labelText = "";
            const id = el.id;
            if (id) {
                const labelEl = document.querySelector("label[for='" + CSS.escape(id) + "']");
                if (labelEl) labelText = (labelEl.innerText || labelEl.textContent || "").trim();
            }
            if (!labelText) {
                let ancestor = el.parentElement;
                while (ancestor && ancestor !== document.body) {
                    if (ancestor.tagName === "LABEL") {
                        labelText = (ancestor.innerText || ancestor.textContent || "").trim();
                        // Remove the field's own value from ancestor text
                        const fieldVal = el.value || "";
                        if (fieldVal && labelText.endsWith(fieldVal)) {
                            labelText = labelText.slice(0, labelText.length - fieldVal.length).trim();
                        }
                        break;
                    }
                    ancestor = ancestor.parentElement;
                }
            }
            if (!labelText) {
                labelText = el.getAttribute("placeholder") || el.getAttribute("aria-label") || "";
                labelText = labelText.trim();
            }

            let value = "";
            if (tag === "SELECT") {
                const opt = el.options[el.selectedIndex];
                value = opt ? (opt.text || "").trim() : "";
            } else {
                value = (el.value || "").trim();
            }

            if (!labelText && !value) continue;

            let item;
            if (labelText && value) {
                item = labelText + ": " + value;
            } else if (labelText) {
                item = labelText + ":";
            } else {
                item = value;
            }

            if (item === lastItem) continue;
            lastItem = item;
            currentItems.push(item);
            continue;
        }

        // Regular text element
        const text = (el.innerText || "").trim();
        if (!text) continue;
        if (text.length < 2 || text.length > 400) continue;
        if (text === lastItem) continue;
        lastItem = text;
        currentItems.push(text);
    }

    flushSection();

    const raw = (document.body ? document.body.innerText : "").trim().slice(0, 8000);

    return JSON.stringify({ title, url, sections, raw });
})()
"""


@dataclass
class Section:
    heading: str
    items: list[str] = field(default_factory=list)


@dataclass
class ScrapedPage:
    title: str
    url: str
    sections: list[Section] = field(default_factory=list)
    raw_text: str = ""
    scraped_at: str = ""


async def scrape_page(page: Any) -> ScrapedPage:
    scraped_at = datetime.now(timezone.utc).isoformat()
    try:
        result_json = await page.evaluate(JS_EXTRACT)
        data = json.loads(result_json)

        sections = [
            Section(heading=s.get("heading", ""), items=s.get("items", []))
            for s in data.get("sections", [])
        ]

        return ScrapedPage(
            title=data.get("title", ""),
            url=data.get("url", ""),
            sections=sections,
            raw_text=data.get("raw", ""),
            scraped_at=scraped_at,
        )
    except Exception as exc:
        logger.exception("scrape_page failed: %s", exc)
        try:
            url = page.url
        except Exception:
            url = ""
        return ScrapedPage(
            title="",
            url=url,
            sections=[],
            raw_text=f"[scrape error: {exc}]",
            scraped_at=scraped_at,
        )


def format_for_display(page: ScrapedPage) -> str:
    """Return plain text formatted for the viewer toast."""
    parts: list[str] = []

    for section in page.sections:
        if not section.items:
            continue
        if section.heading:
            parts.append(f"## {section.heading}")
        for item in section.items:
            parts.append(f"• {item}")
        parts.append("")

    # Strip trailing blank line
    while parts and parts[-1] == "":
        parts.pop()

    return "\n".join(parts)
