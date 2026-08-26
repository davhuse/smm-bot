import unittest
from unittest.mock import patch

import smm_reklam


class BlastStateTests(unittest.TestCase):
    def test_legacy_per_message_marker_is_cleared_once(self):
        state = {smm_reklam.ACCOUNT_LAST_BLAST_KEY: 1234.0}

        self.assertTrue(smm_reklam.migrate_blast_completion_state(state))
        self.assertNotIn(smm_reklam.ACCOUNT_LAST_BLAST_KEY, state)
        self.assertEqual(
            state[smm_reklam.BLAST_STATE_VERSION_KEY],
            smm_reklam.BLAST_STATE_VERSION,
        )
        self.assertFalse(smm_reklam.migrate_blast_completion_state(state))

    def test_interrupted_cycle_does_not_start_hourly_wait(self):
        state = {smm_reklam.BLAST_STATE_VERSION_KEY: smm_reklam.BLAST_STATE_VERSION}

        self.assertFalse(smm_reklam.finish_blast_cycle(state, interrupted=True, now=2000.0))
        self.assertNotIn(smm_reklam.ACCOUNT_LAST_BLAST_KEY, state)

    def test_completed_cycle_starts_hourly_wait(self):
        state = {smm_reklam.BLAST_STATE_VERSION_KEY: smm_reklam.BLAST_STATE_VERSION}

        self.assertTrue(smm_reklam.finish_blast_cycle(state, interrupted=False, now=2000.0))
        with patch.object(smm_reklam, "BLAST_INTERVAL_SECONDS", 3600):
            self.assertEqual(smm_reklam.last_blast_remaining(state, now=2300.0), 3300)

    def test_send_cycle_resumes_after_last_success(self):
        state = {}
        order, start, created = smm_reklam.ensure_send_cycle(
            state, ["group-a", "group-b", "group-c"]
        )
        self.assertTrue(created)
        self.assertEqual((order, start), (["group-a", "group-b", "group-c"], 0))

        smm_reklam.advance_send_cycle(state, 2)
        order, start, created = smm_reklam.ensure_send_cycle(
            state, ["group-a", "group-b", "group-c"]
        )
        self.assertFalse(created)
        self.assertEqual((order, start), (["group-a", "group-b", "group-c"], 2))

    def test_completed_cycle_rotates_next_start(self):
        state = {}
        smm_reklam.ensure_send_cycle(state, ["group-a", "group-b", "group-c"])
        smm_reklam.complete_send_cycle(state)

        order, start, created = smm_reklam.ensure_send_cycle(
            state, ["group-a", "group-b", "group-c"]
        )
        self.assertTrue(created)
        self.assertEqual(start, 0)
        self.assertEqual(order, ["group-b", "group-c", "group-a"])


if __name__ == "__main__":
    unittest.main()
