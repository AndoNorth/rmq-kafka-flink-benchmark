import asyncio
import unittest

from pacing import Pacer


class FakeTime:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def clock(self):
        return self.t

    async def sleep(self, d):
        self.sleeps.append(d)
        self.t += d


class PacerTest(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_does_not_drift_when_work_takes_time(self):
        fake = FakeTime()
        pacer = Pacer(10, clock=fake.clock, sleep=fake.sleep)  # one message per 0.1s
        times = []
        for _ in range(5):
            await pacer.wait()
            times.append(fake.t)
            fake.t += 0.03  # simulated per-message work
        for i, t in enumerate(times):
            self.assertAlmostEqual(t, i * 0.1)

    async def test_no_catch_up_burst_after_a_long_stall(self):
        fake = FakeTime()
        pacer = Pacer(10, clock=fake.clock, sleep=fake.sleep)
        await pacer.wait()
        fake.t += 5.0  # long stall
        await pacer.wait()
        self.assertEqual(fake.sleeps[-1], 0)
        await pacer.wait()
        self.assertAlmostEqual(fake.sleeps[-1], 0.1)

    async def test_unlimited_still_yields_to_the_event_loop(self):
        ran = []

        async def other_task():
            ran.append(True)

        task = asyncio.create_task(other_task())
        await Pacer(0).wait()
        self.assertTrue(ran)
        await task


if __name__ == "__main__":
    unittest.main()
