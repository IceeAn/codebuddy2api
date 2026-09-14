
import tests  # 在生产模块导入前隔离测试数据目录。
import asyncio
import unittest

from src.background_request_pacer import BackgroundRequestPacer


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class BackgroundRequestPacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_actual_starts_consume_uniform_random_intervals(self):
        clock = _FakeClock()
        delays = iter((5.0, 20.0, 7.0))
        pacer = BackgroundRequestPacer(
            5,
            20,
            uniform_factory=lambda _minimum, _maximum: next(delays),
            monotonic_factory=clock.monotonic,
            sleep=clock.sleep,
        )
        starts = []

        async def start_request():
            async with pacer.turn() as mark_started:
                mark_started()
                starts.append(clock.now)

        async def skip_request():
            async with pacer.turn():
                pass

        await asyncio.gather(
            start_request(),
            skip_request(),
            start_request(),
            start_request(),
        )

        self.assertEqual(starts, [0.0, 5.0, 25.0])
        self.assertEqual(clock.sleeps, [5.0, 20.0])

    async def test_elapsed_time_and_reset_allow_immediate_start(self):
        clock = _FakeClock()
        pacer = BackgroundRequestPacer(
            5,
            20,
            uniform_factory=lambda _minimum, _maximum: 20.0,
            monotonic_factory=clock.monotonic,
            sleep=clock.sleep,
        )

        async with pacer.turn() as mark_started:
            mark_started()
        clock.now = 30.0
        async with pacer.turn() as mark_started:
            mark_started()
        self.assertEqual(clock.sleeps, [])

        pacer.reset()
        clock.now = 31.0
        async with pacer.turn() as mark_started:
            mark_started()
        self.assertEqual(clock.sleeps, [])

    async def test_cancelled_waiter_releases_turn_lock_and_double_start_fails(self):
        now = 0.0
        sleep_started = asyncio.Event()
        release_sleep = asyncio.Event()

        async def blocking_sleep(_delay):
            sleep_started.set()
            await release_sleep.wait()

        pacer = BackgroundRequestPacer(
            10,
            10,
            uniform_factory=lambda _minimum, _maximum: 10.0,
            monotonic_factory=lambda: now,
            sleep=blocking_sleep,
        )
        async with pacer.turn() as mark_started:
            mark_started()
            with self.assertRaises(RuntimeError):
                mark_started()

        async def wait_for_turn():
            async with pacer.turn() as mark_started:
                mark_started()

        waiting = asyncio.create_task(wait_for_turn())
        await sleep_started.wait()
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting

        now = 10.0
        await asyncio.wait_for(wait_for_turn(), timeout=0.1)

    async def test_unmarked_callback_expires_when_turn_exits(self):
        pacer = BackgroundRequestPacer(0, 0)
        async with pacer.turn() as expired_mark_started:
            pass

        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()

        async def hold_next_turn():
            async with pacer.turn():
                holder_entered.set()
                await release_holder.wait()

        holder = asyncio.create_task(hold_next_turn())
        await holder_entered.wait()
        try:
            with self.assertRaises(RuntimeError):
                expired_mark_started()
            self.assertTrue(pacer._lock.locked())
        finally:
            release_holder.set()
            await asyncio.gather(holder, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
