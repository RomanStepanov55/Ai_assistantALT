import unittest
from brain.resource_governor import ResourceGovernor


class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


class TestGovernor(unittest.TestCase):
    def test_burst_then_block(self):
        c = FakeClock()
        g = ResourceGovernor(target_hz=10.0, burst=2.0, clock=c)
        self.assertTrue(g.acquire())
        self.assertTrue(g.acquire())
        self.assertFalse(g.acquire())          # бакет пуст

    def test_refill_over_time(self):
        c = FakeClock()
        g = ResourceGovernor(target_hz=10.0, burst=2.0, clock=c)
        g.acquire(); g.acquire()
        c.advance(0.5)                          # +5 токенов, кап 2
        self.assertTrue(g.acquire())

    def test_sleep_grows_with_cpu(self):
        g = ResourceGovernor()
        self.assertGreater(g.recommended_sleep(cpu_percent=90.0, base=0.05),
                           g.recommended_sleep(cpu_percent=20.0, base=0.05))

    def test_sleep_when_idle_cpu(self):
        g = ResourceGovernor()
        self.assertLessEqual(g.recommended_sleep(cpu_percent=10.0, base=0.05), 0.05)

    def test_schedule_overloaded_grows_interval(self):
        g = ResourceGovernor()
        self.assertEqual(g.schedule(0, 10.0, 100.0, overloaded=True), 15.0)

    def test_schedule_caps_at_300(self):
        g = ResourceGovernor()
        self.assertEqual(g.schedule(0, 250.0, 100.0, overloaded=True), 300.0)

    def test_sleep_capped(self):
        g = ResourceGovernor(target_hz=0.1)
        self.assertLessEqual(g.recommended_sleep(base=0.05), 1.5)


if __name__ == "__main__":
    unittest.main()
