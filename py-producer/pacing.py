import asyncio
import time

# Upper bound on messages sent but not yet acknowledged. Plays the role of the
# 10_000-slot confirmation channel in the Go RabbitMQ producer: it gives
# backpressure when the broker is slower than the send loop.
MAX_IN_FLIGHT = 10_000


class Pacer:
    """Fixed-rate pacing for the send loop. rate=0 means unlimited."""

    def __init__(self, rate: int, clock=time.monotonic, sleep=asyncio.sleep):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0

    async def wait(self) -> None:
        if not self._interval:
            # Unlimited: still yield once per message. asyncio only switches tasks
            # at an await, so without this the ack callbacks would never run.
            await self._sleep(0)
            return
        now = self._clock()
        # max() drops missed slots after a stall instead of bursting to catch up,
        # like a Go ticker dropping ticks for a slow receiver.
        slot = max(self._next, now)
        self._next = slot + self._interval
        await self._sleep(slot - now)
