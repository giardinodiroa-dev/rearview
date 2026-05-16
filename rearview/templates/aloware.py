from __future__ import annotations

import logging
from typing import Optional

from rearview.templates.base import BaseTemplate, Contact
from rearview.config import get_config

logger = logging.getLogger(__name__)


class AlowareTemplate(BaseTemplate):
    """
    Template for Aloware cloud power dialer (app.aloware.com).
    Aloware is a Vue.js SPA; selectors use a combination of class-based
    and attribute-based targeting with multiple fallbacks per field.
    """

    # During an active call the call card / timer is visible
    CALL_ACTIVE_SELECTOR: str = (
        ".active-call-card, "
        ".call-timer, "
        "[data-testid='active-call'], "
        ".dialer-call-active, "
        ".in-call-panel"
    )

    # After the call ends Aloware shows a disposition / wrap-up panel
    CALL_ENDED_SELECTOR: str = (
        ".disposition-panel, "
        ".dispositions-panel, "
        ".call-wrap-up, "
        ".post-call-panel, "
        "[data-testid='disposition-panel']"
    )

    # Contact name selectors — ordered most-specific first
    CONTACT_NAME_SELECTOR: str = (
        ".contact-name, "
        ".lead-name, "
        ".caller-name, "
        ".active-call-card .name, "
        ".dialer-contact .name, "
        "h4.contact-name, "
        "h3.lead-name"
    )

    CONTACT_COMPANY_SELECTOR: str = (
        ".company-name, "
        ".contact-company, "
        ".lead-company, "
        ".active-call-card .company, "
        ".dialer-contact .company"
    )

    CONTACT_DESCRIPTION_SELECTOR: str = (
        ".contact-notes, "
        ".lead-description, "
        ".contact-bio, "
        ".lead-notes, "
        ".contact-info .description, "
        "[data-field='description']"
    )

    CONTACT_PHONE_SELECTOR: str = (
        ".phone-number, "
        ".contact-phone, "
        ".lead-phone, "
        ".active-call-card .phone, "
        ".dialer-phone-number, "
        "[data-field='phone']"
    )

    DISPOSITION_CONTAINER_SELECTOR: str = (
        ".disposition-panel, "
        ".dispositions-panel, "
        ".call-wrap-up, "
        ".post-call-panel, "
        "[data-testid='disposition-panel']"
    )

    # Individual disposition option within the container
    DISPOSITION_BUTTON_SELECTOR: str = (
        ".disposition-option, "
        ".disposition-btn, "
        "[data-disposition], "
        ".dispositions-panel .btn, "
        ".disposition-panel button, "
        ".call-disposition-item"
    )

    NEXT_CALL_BUTTON_SELECTOR: str = (
        ".next-call-btn, "
        ".dial-next, "
        "[data-testid='next-call'], "
        ".power-dial-next, "
        "button.next-call, "
        ".dialer-next-btn"
    )

    # Aloware fires a PATCH/PUT to /calls/:id when a call ends
    CALL_END_API_PATTERN: str = "/calls/"

    # Confirm / save button selectors used after selecting a disposition
    _CONFIRM_BUTTON_SELECTORS = [
        ".disposition-save",
        ".confirm-disposition",
        "[data-testid='save-disposition']",
        ".wrap-up-save",
        "button.save",
        ".modal-footer .btn-primary",
        ".disposition-panel .btn-primary",
        "[data-action='save']",
    ]

    async def scrape_contact(self, page) -> Contact:
        """
        Scrape contact info from the active call card.
        Tries multiple selectors per field; returns whatever is found.
        """

        async def _text(selectors: str) -> str:
            for selector in [s.strip() for s in selectors.split(",")]:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        text = await el.inner_text()
                        text = text.strip()
                        if text:
                            return text
                except Exception:
                    continue
            return ""

        name = await _text(self.CONTACT_NAME_SELECTOR)
        company = await _text(self.CONTACT_COMPANY_SELECTOR)
        description = await _text(self.CONTACT_DESCRIPTION_SELECTOR)
        phone = await _text(self.CONTACT_PHONE_SELECTOR)

        raw: dict = {}
        if name:
            raw["name"] = name
        if company:
            raw["company"] = company
        if description:
            raw["description"] = description
        if phone:
            raw["phone"] = phone

        logger.debug(
            "AlowareTemplate.scrape_contact: name=%r company=%r phone=%r",
            name,
            company,
            phone,
        )

        return Contact(
            name=name,
            company=company,
            description=description,
            phone=phone,
            raw=raw,
        )

    async def set_disposition(self, page, label: str) -> bool:
        """
        Find the disposition option whose text matches `label` (case-insensitive),
        click it via page.evaluate(), then look for and click a confirm/save button.
        Returns True on success, False on failure.
        """
        target = label.strip().lower()

        # Build a combined CSS selector string for all disposition button variants
        button_selectors = [s.strip() for s in self.DISPOSITION_BUTTON_SELECTOR.split(",")]

        # Find all candidate elements and pick the one matching the label
        matched = await page.evaluate(
            """(args) => {
                const {selectors, target} = args;
                for (const sel of selectors) {
                    const elements = document.querySelectorAll(sel);
                    for (const el of elements) {
                        const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (text === target) {
                            return true;
                        }
                    }
                }
                return false;
            }""",
            {"selectors": button_selectors, "target": target},
        )

        if not matched:
            logger.warning(
                "AlowareTemplate.set_disposition: no button found for label %r", label
            )
            return False

        # Click the matched element via evaluate so Vue reactivity fires correctly
        clicked = await page.evaluate(
            """(args) => {
                const {selectors, target} = args;
                for (const sel of selectors) {
                    const elements = document.querySelectorAll(sel);
                    for (const el of elements) {
                        const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (text === target) {
                            el.click();
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            return true;
                        }
                    }
                }
                return false;
            }""",
            {"selectors": button_selectors, "target": target},
        )

        if not clicked:
            logger.warning(
                "AlowareTemplate.set_disposition: click failed for label %r", label
            )
            return False

        logger.debug("AlowareTemplate.set_disposition: clicked %r", label)

        # Look for a confirm / save button and click it
        for confirm_sel in self._CONFIRM_BUTTON_SELECTORS:
            try:
                confirm_el = await page.query_selector(confirm_sel)
                if confirm_el and await confirm_el.is_visible():
                    await confirm_el.click()
                    logger.debug(
                        "AlowareTemplate.set_disposition: confirmed via %r", confirm_sel
                    )
                    break
            except Exception as exc:
                logger.debug(
                    "AlowareTemplate.set_disposition: confirm selector %r raised %s",
                    confirm_sel,
                    exc,
                )
                continue

        return True

    async def advance_to_next(self, page) -> bool:
        """
        Find and click the next-call button.
        Returns True if a button was found and clicked, False otherwise.
        """
        for selector in [s.strip() for s in self.NEXT_CALL_BUTTON_SELECTOR.split(",")]:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    await el.click()
                    logger.debug(
                        "AlowareTemplate.advance_to_next: clicked via %r", selector
                    )
                    return True
            except Exception as exc:
                logger.debug(
                    "AlowareTemplate.advance_to_next: selector %r raised %s",
                    selector,
                    exc,
                )
                continue

        # Fallback: try via evaluate to find any visible "Next" button
        clicked = await page.evaluate(
            """() => {
                const keywords = ['next call', 'dial next', 'next', 'continue'];
                const buttons = document.querySelectorAll('button, [role="button"], a.btn');
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    for (const kw of keywords) {
                        if (text.includes(kw) && btn.offsetParent !== null) {
                            btn.click();
                            return true;
                        }
                    }
                }
                return false;
            }"""
        )

        if clicked:
            logger.debug("AlowareTemplate.advance_to_next: clicked via keyword fallback")
        else:
            logger.warning("AlowareTemplate.advance_to_next: no next-call button found")

        return bool(clicked)

    def get_mutation_observer_js(self) -> str:
        """
        Returns JavaScript that installs a MutationObserver on the Aloware SPA.

        Detection strategy (in priority order):
          1. Disposition/wrap-up panel becomes visible → call ended
          2. The end-call / hang-up button disappears after being present → call ended
          3. Call status text changes to a known ended state

        window.__rearview_call_ended() is called exactly once per call.
        window.__rearview_reset_observer() resets state for the next call.
        """
        return r"""
(function() {
    if (window.__rearview_observer_active) {
        return;
    }
    window.__rearview_observer_active = true;
    window.__rearview_call_ended_fired = false;
    window.__rearview_was_active = false;

    var DISPOSITION_SELECTORS = [
        '.disposition-panel',
        '.dispositions-panel',
        '.call-wrap-up',
        '.post-call-panel',
        '[data-testid="disposition-panel"]'
    ];

    var ACTIVE_CALL_SELECTORS = [
        '.active-call-card',
        '.call-timer',
        '[data-testid="active-call"]',
        '.dialer-call-active',
        '.in-call-panel'
    ];

    var HANGUP_SELECTORS = [
        '.end-call-btn',
        '.hangup-btn',
        'button.end-call',
        '[data-testid="end-call"]',
        '.btn-end-call',
        '.hang-up'
    ];

    var ENDED_STATUS_TEXTS = [
        'call ended',
        'call completed',
        'call disconnected',
        'hung up'
    ];

    function isVisible(el) {
        return el && el.offsetParent !== null && !el.hidden &&
               getComputedStyle(el).display !== 'none' &&
               getComputedStyle(el).visibility !== 'hidden';
    }

    function queryAny(selectors) {
        for (var i = 0; i < selectors.length; i++) {
            try {
                var el = document.querySelector(selectors[i]);
                if (el) return el;
            } catch(e) {}
        }
        return null;
    }

    function queryAllVisible(selectors) {
        for (var i = 0; i < selectors.length; i++) {
            try {
                var el = document.querySelector(selectors[i]);
                if (el && isVisible(el)) return el;
            } catch(e) {}
        }
        return null;
    }

    function fireCallEnded() {
        if (!window.__rearview_call_ended_fired) {
            window.__rearview_call_ended_fired = true;
            if (typeof window.__rearview_call_ended === 'function') {
                window.__rearview_call_ended();
            }
        }
    }

    function checkState() {
        // Priority 1: disposition / wrap-up panel appeared
        var dispositionEl = queryAllVisible(DISPOSITION_SELECTORS);
        if (dispositionEl) {
            fireCallEnded();
            return;
        }

        // Priority 2: track active call presence
        var activeEl = queryAllVisible(ACTIVE_CALL_SELECTORS);
        if (activeEl) {
            window.__rearview_was_active = true;
            // Also track hang-up button presence while in call
            var hangupEl = queryAllVisible(HANGUP_SELECTORS);
            window.__rearview_had_hangup = !!hangupEl;
        } else if (window.__rearview_was_active) {
            // Active call element gone — call ended
            fireCallEnded();
            return;
        }

        // Priority 3: hang-up button was present and disappeared
        if (window.__rearview_had_hangup) {
            var hangupNow = queryAllVisible(HANGUP_SELECTORS);
            if (!hangupNow) {
                fireCallEnded();
                return;
            }
        }

        // Priority 4: call status text changed to ended state
        var statusEl = document.querySelector(
            '.call-status, .dialer-status, .call-state, [data-call-status]'
        );
        if (statusEl) {
            var statusText = (statusEl.innerText || statusEl.textContent || '').trim().toLowerCase();
            for (var i = 0; i < ENDED_STATUS_TEXTS.length; i++) {
                if (statusText.indexOf(ENDED_STATUS_TEXTS[i]) !== -1) {
                    fireCallEnded();
                    return;
                }
            }
        }
    }

    var observer = new MutationObserver(function(mutations) {
        checkState();
    });

    var root = document.body || document.documentElement;
    observer.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['class', 'style', 'hidden', 'data-call-status']
    });

    // Reset for next call (called by Python side between calls)
    window.__rearview_reset_observer = function() {
        window.__rearview_call_ended_fired = false;
        window.__rearview_was_active = false;
        window.__rearview_had_hangup = false;
    };

    // Run once immediately in case the page is already in a post-call state
    checkState();
})();
"""
