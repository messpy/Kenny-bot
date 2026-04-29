from __future__ import annotations

import time
import unittest

from src.kennybot.utils.timers import CountdownTimer, Stopwatch, format_duration_jp


class TimerUtilityTests(unittest.TestCase):
    def test_format_duration_jp(self) -> None:
        self.assertEqual(format_duration_jp(5), "5秒")
        self.assertEqual(format_duration_jp(61), "1分1秒")

    def test_stopwatch_start_stop(self) -> None:
        watch = Stopwatch()
        watch.start()
        time.sleep(0.02)
        watch.stop()
        self.assertGreaterEqual(watch.elapsed_seconds(), 0)
        self.assertFalse(watch.running)

    def test_countdown_timer_progress(self) -> None:
        timer = CountdownTimer(total_seconds=1)
        timer.start()
        self.assertIn(timer.remaining_seconds(), {0, 1})
        time.sleep(1.05)
        self.assertTrue(timer.is_done())


if __name__ == "__main__":
    unittest.main()
