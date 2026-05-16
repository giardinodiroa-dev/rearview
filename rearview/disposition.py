from __future__ import annotations

import logging

from playwright.async_api import Page

from rearview.config import get_config
from rearview.templates import get_template

logger = logging.getLogger(__name__)


async def set_disposition(page: Page, label: str) -> bool:
    """Find the disposition option whose text matches `label` and click it.

    Delegates to the active template's set_disposition() which handles the
    dialer-specific DOM interactions including confirmation steps.

    Returns True on success, False on failure.
    """
    cfg = get_config()
    template = get_template(cfg.rearview.template)

    try:
        result = await template.set_disposition(page, label)
        if result:
            logger.info("set_disposition: set to %r", label)
        else:
            logger.warning("set_disposition: failed for label %r", label)
        return result
    except Exception as exc:
        logger.error("set_disposition: template raised %s", exc)
        return False


async def advance_to_next(page: Page) -> bool:
    """Find and click the next-call button in the dialer UI.

    Delegates to the active template's advance_to_next() which tries multiple
    selector strategies and a keyword-based fallback.

    Returns True if the button was found and clicked, False otherwise.
    """
    cfg = get_config()
    template = get_template(cfg.rearview.template)

    try:
        result = await template.advance_to_next(page)
        if result:
            logger.info("advance_to_next: advanced to next call")
        else:
            logger.warning("advance_to_next: no next-call button found")
        return result
    except Exception as exc:
        logger.error("advance_to_next: template raised %s", exc)
        return False


async def complete_call(page: Page, label: str, auto_advance: bool) -> bool:
    """Set the disposition and, if requested, advance to the next call.

    Executes the two steps sequentially. If set_disposition() fails,
    advance_to_next() is still attempted only when auto_advance is True
    so the dialer keeps moving regardless.

    Returns True only if all requested steps succeeded.
    """
    disposition_ok = await set_disposition(page, label)

    if not disposition_ok:
        logger.warning(
            "complete_call: set_disposition failed for %r; auto_advance=%s",
            label,
            auto_advance,
        )

    advance_ok = True
    if auto_advance:
        advance_ok = await advance_to_next(page)

    success = disposition_ok and advance_ok
    logger.debug(
        "complete_call: label=%r auto_advance=%s disposition_ok=%s advance_ok=%s -> %s",
        label,
        auto_advance,
        disposition_ok,
        advance_ok,
        success,
    )
    return success
