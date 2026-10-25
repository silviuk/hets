import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "hets_sync", Path(__file__).resolve().parents[1] / "hets" / "sync.py"
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class StopLoop(Exception):
    pass


class RenewalTimingTests(unittest.TestCase):
    def run_loop(self, active, latest, sleep_count=1, applied=True):
        now = [1_800_000_000]
        sleeps = []
        fetch_times = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
            if len(sleeps) >= sleep_count:
                raise StopLoop

        def fetch():
            fetch_times.append(now[0])
            return latest

        with (
            patch.object(sync, "detect_host_lan_ips", return_value=[]),
            patch.object(sync, "read_existing_token_from_disk", return_value=active),
            patch.object(sync, "fetch_latest_token", side_effect=fetch),
            patch.object(sync, "apply_token_to_evcc", return_value=applied) as apply,
            patch.object(sync, "send_ha_notification"),
            patch.object(sync.time, "time", side_effect=lambda: now[0]),
            patch.object(sync.time, "sleep", side_effect=sleep),
        ):
            with self.assertRaises(StopLoop):
                sync.main()
        return sleeps, fetch_times, apply.call_count

    def test_waits_until_one_hour_before_expiry_including_startup(self):
        expiry = 1_800_000_000 + 86400
        sleeps, fetches, applies = self.run_loop(
            ("current", expiry), ("current", expiry), sleep_count=2
        )
        self.assertEqual(sleeps, [82800, 120])
        self.assertEqual(fetches, [expiry - 3600])
        self.assertEqual(applies, 0)

    def test_checks_immediately_at_and_inside_renewal_window(self):
        for remaining in (3600, 3599, 300, 0, -900):
            with self.subTest(remaining=remaining):
                expiry = 1_800_000_000 + remaining
                sleeps, fetches, _ = self.run_loop(
                    ("current", expiry), ("current", expiry)
                )
                self.assertEqual(fetches, [1_800_000_000])
                self.assertEqual(sleeps, [120])

    def test_retains_post_expiry_backoff(self):
        for remaining, delay in ((-901, 600), (-3600, 600), (-3601, 1800)):
            with self.subTest(remaining=remaining):
                expiry = 1_800_000_000 + remaining
                sleeps, _, _ = self.run_loop(
                    ("current", expiry), ("current", expiry)
                )
                self.assertEqual(sleeps, [delay])

    def test_new_token_resets_wait_to_its_renewal_window(self):
        expiry = 1_800_000_000 + 86400
        sleeps, fetches, applies = self.run_loop(
            ("old", 1_800_000_000 + 3600), ("new", expiry)
        )
        self.assertEqual(sleeps, [82800])
        self.assertEqual(fetches, [1_800_000_000])
        self.assertEqual(applies, 1)

    def test_missing_or_unknown_token_checks_immediately(self):
        for active in ((None, 0), ("current", 0)):
            with self.subTest(active=active):
                sleeps, fetches, _ = self.run_loop(active, (None, 0))
                self.assertEqual(fetches, [1_800_000_000])
                self.assertEqual(sleeps, [120])

    def test_failed_application_retries_in_one_minute(self):
        sleeps, _, applies = self.run_loop(
            ("old", 1_800_000_000), ("new", 1_800_000_000 + 86400),
            applied=False,
        )
        self.assertEqual(sleeps, [60])
        self.assertEqual(applies, 1)


if __name__ == "__main__":
    unittest.main()

