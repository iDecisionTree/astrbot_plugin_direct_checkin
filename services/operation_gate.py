"""重置优先的进程内维护屏障，正常请求可并发。"""

import asyncio
from contextlib import asynccontextmanager


class MaintenanceError(RuntimeError):
    pass


class OperationGate:
    def __init__(self):
        self._condition = asyncio.Condition()
        self._active = 0
        self.maintenance = False
        self.blocked = False

    @asynccontextmanager
    async def enter(self, exclusive=False):
        async with self._condition:
            if self.maintenance or (self.blocked and not exclusive):
                raise MaintenanceError("维护中")
            if exclusive:
                self.maintenance = True
                try:
                    await self._condition.wait_for(lambda: self._active == 0)
                except BaseException:
                    self.maintenance = False
                    self._condition.notify_all()
                    raise
            else:
                self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                if exclusive:
                    self.maintenance = False
                else:
                    self._active -= 1
                self._condition.notify_all()
