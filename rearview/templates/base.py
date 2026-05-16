from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Contact:
    name: str = ""
    company: str = ""
    description: str = ""
    phone: str = ""
    raw: dict = field(default_factory=dict)


class BaseTemplate(ABC):
    # Subclasses override these selectors
    CALL_ACTIVE_SELECTOR: str = ""
    CALL_ENDED_SELECTOR: str = ""
    CONTACT_NAME_SELECTOR: str = ""
    CONTACT_COMPANY_SELECTOR: str = ""
    CONTACT_DESCRIPTION_SELECTOR: str = ""
    CONTACT_PHONE_SELECTOR: str = ""
    DISPOSITION_CONTAINER_SELECTOR: str = ""
    DISPOSITION_BUTTON_SELECTOR: str = ""  # individual button within container
    NEXT_CALL_BUTTON_SELECTOR: str = ""
    CALL_END_API_PATTERN: str = ""  # URL substring to watch for in network requests

    @abstractmethod
    async def scrape_contact(self, page) -> Contact: ...

    @abstractmethod
    async def set_disposition(self, page, label: str) -> bool: ...

    @abstractmethod
    async def advance_to_next(self, page) -> bool: ...

    async def on_call_start(self, page, contact: Contact) -> None:
        pass

    async def on_call_end(self, page, contact: Contact) -> None:
        pass

    async def is_call_active(self, page) -> bool:
        if not self.CALL_ACTIVE_SELECTOR:
            return False
        element = await page.query_selector(self.CALL_ACTIVE_SELECTOR)
        if element is None:
            return False
        return await element.is_visible()

    async def is_call_ended(self, page) -> bool:
        if not self.CALL_ENDED_SELECTOR:
            return False
        element = await page.query_selector(self.CALL_ENDED_SELECTOR)
        if element is None:
            return False
        return await element.is_visible()

    def get_mutation_observer_js(self) -> str:
        call_ended_selector = self.CALL_ENDED_SELECTOR or ""
        call_active_selector = self.CALL_ACTIVE_SELECTOR or ""
        return f"""
(function() {{
    if (window.__rearview_observer_active) {{
        return;
    }}
    window.__rearview_observer_active = true;
    window.__rearview_call_ended_fired = false;

    function fireCallEnded() {{
        if (!window.__rearview_call_ended_fired) {{
            window.__rearview_call_ended_fired = true;
            if (typeof window.__rearview_call_ended === 'function') {{
                window.__rearview_call_ended();
            }}
        }}
    }}

    function checkCallEnded() {{
        var endedSel = {repr(call_ended_selector)};
        var activeSel = {repr(call_active_selector)};

        if (endedSel) {{
            var endedEl = document.querySelector(endedSel);
            if (endedEl && endedEl.offsetParent !== null) {{
                fireCallEnded();
                return;
            }}
        }}

        if (activeSel) {{
            var activeEl = document.querySelector(activeSel);
            if (!activeEl || activeEl.offsetParent === null) {{
                // active element gone — call likely ended
                // only fire if we were previously in an active state
                if (window.__rearview_was_active) {{
                    fireCallEnded();
                    return;
                }}
            }} else {{
                window.__rearview_was_active = true;
            }}
        }}
    }}

    var observer = new MutationObserver(function(mutations) {{
        checkCallEnded();
    }});

    observer.observe(document.body || document.documentElement, {{
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['class', 'style', 'hidden']
    }});

    // Allow resetting for next call
    window.__rearview_reset_observer = function() {{
        window.__rearview_call_ended_fired = false;
        window.__rearview_was_active = false;
    }};
}})();
"""
