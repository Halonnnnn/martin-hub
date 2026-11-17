import asyncio


class WakeBus:
    """进程内 SSE 连接注册表。多进程部署时跨进程事件由派发器兜底扫描补发。"""

    def __init__(self, queue_size: int = 100):
        self._queue_size = queue_size
        self._subs: dict[int, set[asyncio.Queue]] = {}
        self._inbox_event = asyncio.Event()

    def notify_inbox(self) -> None:
        """叫醒所有在 /inbox 长轮询的等待者；醒来后重新探针是廉价的。"""
        self._inbox_event.set()
        self._inbox_event = asyncio.Event()

    async def wait_inbox_change(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._inbox_event.wait(), timeout)
        except TimeoutError:
            pass

    def is_connected(self, member_id: int) -> bool:
        return bool(self._subs.get(member_id))

    def subscribe(self, member_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.setdefault(member_id, set()).add(q)
        return q

    def unsubscribe(self, member_id: int, q: asyncio.Queue) -> None:
        subs = self._subs.get(member_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(member_id, None)

    def publish(self, member_id: int, event_id: int) -> None:
        for q in self._subs.get(member_id, ()):
            try:
                q.put_nowait(event_id)
            except asyncio.QueueFull:
                pass
