from __future__ import annotations

import logging
from typing import List

from playwright.async_api import Page

from rearview.config import get_config
from rearview.templates import get_template
from rearview.templates.base import Contact

logger = logging.getLogger(__name__)


async def scrape_contact(page: Page) -> Contact:
    """Scrape contact information from the current page using the active template.

    Delegates to the template's scrape_contact() method which uses multi-selector
    fallback logic appropriate for the target dialer UI.

    Returns a Contact dataclass with whatever fields could be extracted.
    """
    cfg = get_config()
    template = get_template(cfg.rearview.template)

    try:
        contact = await template.scrape_contact(page)
        logger.debug(
            "scrape_contact: name=%r company=%r phone=%r",
            contact.name,
            contact.company,
            contact.phone,
        )
        return contact
    except Exception as exc:
        logger.error("scrape_contact: template raised %s", exc)
        return Contact()


async def get_available_dispositions(page: Page) -> List[str]:
    """Query the disposition container in the active template and return a list
    of disposition label strings found in the DOM.

    Useful for auto-discovering dispositions on first run so the config can be
    pre-populated without manual entry.

    Returns an empty list when the disposition panel is not currently visible.
    """
    cfg = get_config()
    template = get_template(cfg.rearview.template)

    container_selector = template.DISPOSITION_CONTAINER_SELECTOR
    button_selector = template.DISPOSITION_BUTTON_SELECTOR

    if not container_selector or not button_selector:
        logger.warning(
            "get_available_dispositions: template %r has no disposition selectors",
            cfg.rearview.template,
        )
        return []

    try:
        # Check if the container is actually visible before scraping
        container_selectors = [s.strip() for s in container_selector.split(",")]
        container_visible = False
        for sel in container_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    container_visible = True
                    break
            except Exception:
                continue

        if not container_visible:
            logger.debug(
                "get_available_dispositions: disposition panel not visible"
            )
            return []

        button_selectors = [s.strip() for s in button_selector.split(",")]

        labels: List[str] = await page.evaluate(
            """(selectors) => {
                const seen = new Set();
                const results = [];
                for (const sel of selectors) {
                    try {
                        const elements = document.querySelectorAll(sel);
                        for (const el of elements) {
                            const text = (el.innerText || el.textContent || '').trim();
                            if (text && !seen.has(text)) {
                                seen.add(text);
                                results.push(text);
                            }
                        }
                    } catch (e) {}
                }
                return results;
            }""",
            button_selectors,
        )

        logger.debug(
            "get_available_dispositions: found %d labels: %r", len(labels), labels
        )
        return labels

    except Exception as exc:
        logger.error("get_available_dispositions: failed with %s", exc)
        return []
