import asyncio
import logging

from rearview.click_store import ClickChain, ClickDot
from rearview.controller import get_controller
from rearview.region_mapper import Region

logger = logging.getLogger(__name__)


class ClickExecutor:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._cancelled: bool = False
        self._running: bool = False

    async def run_chain(
        self,
        chain: ClickChain,
        region: Region,
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
                abs_x = region.x + int(dot.rx * region.w)
                abs_y = region.y + int(dot.ry * region.h)
                await get_controller().background_click(abs_x, abs_y)
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
