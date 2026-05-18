import asyncio
import logging

from pynput.mouse import Controller as _MouseController, Button as _Button

from rearview.click_store import ClickChain, ClickDot

logger = logging.getLogger(__name__)

_mouse = _MouseController()


def _do_click(x: int, y: int) -> None:
    _mouse.position = (x, y)
    _mouse.click(_Button.left, 1)


class ClickExecutor:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._cancelled: bool = False
        self._running: bool = False

    async def run_chain(
        self,
        chain: ClickChain,
        dots: dict[str, ClickDot],
    ) -> None:
        self._running = True
        self._cancelled = False
        try:
            for step in chain.steps:
                if self._cancelled:
                    return
                await asyncio.sleep(step.delay_before)
                if self._cancelled:
                    return
                dot = dots.get(step.dot_id)
                if dot is None:
                    logger.warning(
                        "ClickExecutor: [%s] dot_id %r not found, skipping",
                        chain.name,
                        step.dot_id,
                    )
                    continue
                abs_x = int(dot.rx)
                abs_y = int(dot.ry)
                # Move mouse and click at absolute screen coords in a thread
                # so we don't block the event loop
                await asyncio.get_event_loop().run_in_executor(
                    None, _do_click, abs_x, abs_y
                )
                logger.info(
                    "ClickExecutor: [%s] clicked %s at (%d,%d)",
                    chain.name,
                    dot.label,
                    abs_x,
                    abs_y,
                )
        finally:
            self._cancelled = False
            self._running = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def running(self) -> bool:
        return self._running


_executor: ClickExecutor | None = None


def get_click_executor(loop: asyncio.AbstractEventLoop) -> ClickExecutor:
    global _executor
    if _executor is None:
        _executor = ClickExecutor(loop)
    return _executor
