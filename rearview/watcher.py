from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from rearview.config import get_config
from rearview.templates import get_template
from rearview.templates.base import BaseTemplate, Contact

logger = logging.getLogger(__name__)

_RECONNECT_INTERVAL = 30  # seconds between reconnect attempts
_REINJECT_INTERVAL = 30   # seconds between observer re-injections


class CallWatcher:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._template: Optional[BaseTemplate] = None
        self._running: bool = False
        self._handling_call_end: bool = False  # debounce: only one handler at a time

        # Callbacks — set by the orchestrator
        self.on_call_end: Optional[callable] = None    # async fn(contact: Contact)
        self.on_call_start: Optional[callable] = None  # async fn(contact: Contact)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to an existing Chromium instance via CDP.

        Returns True on success, False on failure.
        """
        cfg = get_config()
        port = cfg.browser.debug_port
        cdp_url = f"http://localhost:{port}"

        try:
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            logger.info("CallWatcher: connected to Chromium on %s", cdp_url)

            # Reuse the first existing context or create one
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
            else:
                self._context = await self._browser.new_context()

            # Get the first page or open one
            pages = self._context.pages
            if pages:
                self._page = pages[0]
            else:
                self._page = await self._context.new_page()

            # Load the template for this session
            self._template = get_template(cfg.rearview.template)

            # Wire up network interception
            self._page.on("request", self._handle_network_request)

            # Inject the MutationObserver JS
            await self._inject_observer()

            return True

        except Exception as exc:
            logger.error("CallWatcher.connect failed: %s", exc)
            self._browser = None
            self._context = None
            self._page = None
            return False

    async def disconnect(self) -> None:
        """Close the CDP browser connection and release all resources."""
        self._running = False

        if self._page is not None:
            try:
                self._page.remove_listener("request", self._handle_network_request)
            except Exception:
                pass
            self._page = None

        self._context = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.info("CallWatcher: disconnected")

    def set_page(self, page) -> None:
        """Called by the orchestrator when the controller connects to a new page."""
        self._page = page
        # Re-inject observer on the new page
        if page is not None:
            asyncio.ensure_future(self._inject_observer())

    async def watch(self) -> None:
        """Main watch loop.

        Keeps the CDP connection alive and re-injects the MutationObserver
        every _REINJECT_INTERVAL seconds in case the page navigated. Reconnects
        automatically if the connection drops. Exits when self._running is False.
        """
        self._running = True
        seconds_since_inject = 0

        while self._running:
            if not self.is_connected:
                logger.info("CallWatcher: not connected — attempting connect...")
                success = await self.connect()
                if not success:
                    logger.warning(
                        "CallWatcher: connect failed, retrying in %ds", _RECONNECT_INTERVAL
                    )
                    await asyncio.sleep(_RECONNECT_INTERVAL)
                    continue
                seconds_since_inject = 0

            await asyncio.sleep(1)
            seconds_since_inject += 1

            if seconds_since_inject >= _REINJECT_INTERVAL:
                if self.is_connected:
                    try:
                        await self._inject_observer()
                    except Exception as exc:
                        logger.debug("CallWatcher: re-inject failed: %s", exc)
                seconds_since_inject = 0

    async def run_js(self, script: str):
        """Execute arbitrary JavaScript on the connected page and return the result.

        This is the "browser console" feature from the spec.
        Raises RuntimeError if not connected.
        """
        if not self.is_connected:
            raise RuntimeError("CallWatcher: not connected to a browser page")
        return await self._page.evaluate(script)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _inject_observer(self) -> None:
        """Expose the Python call-ended bridge then evaluate the MutationObserver JS."""
        if self._page is None or self._template is None:
            return

        # Expose the Python handler under the well-known name.
        # expose_function raises if already registered — ignore that.
        try:
            await self._page.expose_function(
                "__rearview_call_ended", self._handle_call_ended
            )
        except Exception:
            # Already exposed (e.g. page reused across re-inject cycles)
            pass

        observer_js = self._template.get_mutation_observer_js()
        try:
            await self._page.evaluate(observer_js)
            logger.debug("CallWatcher: MutationObserver injected")
        except Exception as exc:
            logger.warning("CallWatcher: failed to inject observer JS: %s", exc)

    async def _handle_call_ended(self) -> None:
        """Called from JS via the exposed function when the MutationObserver fires."""
        if self._handling_call_end:
            return
        self._handling_call_end = True
        logger.info("CallWatcher: call-ended event received")

        # Scrape contact info before firing the callback
        contact = Contact()
        if self._page is not None and self._template is not None:
            try:
                from rearview.scraper import scrape_contact
                contact = await scrape_contact(self._page)
            except Exception as exc:
                logger.warning("CallWatcher: scrape_contact failed: %s", exc)

        if self.on_call_end is not None:
            try:
                await self.on_call_end(contact)
            except Exception as exc:
                logger.error("CallWatcher: on_call_end callback raised: %s", exc)

        # Reset the JS observer state so it can fire again for the next call
        if self._page is not None:
            try:
                await self._page.evaluate("window.__rearview_reset_observer()")
            except Exception as exc:
                logger.debug("CallWatcher: reset_observer failed: %s", exc)

        self._handling_call_end = False

    async def _handle_network_request(self, request) -> None:
        """Intercept network requests and trigger call-ended on matching API patterns."""
        if self._template is None:
            return

        pattern = self._template.CALL_END_API_PATTERN
        if not pattern:
            return

        url = request.url
        method = request.method.upper()

        # Aloware fires a PATCH/PUT to /calls/:id when a call ends
        if pattern in url and method in ("PATCH", "PUT", "POST"):
            logger.debug(
                "CallWatcher: network trigger — %s %s matches pattern %r",
                method,
                url,
                pattern,
            )
            try:
                await self._handle_call_ended()
            except Exception as exc:
                logger.error("CallWatcher: _handle_call_ended (network) raised: %s", exc)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True when a live CDP browser connection and page are available."""
        return (
            self._browser is not None
            and self._browser.is_connected()
            and self._page is not None
        )
