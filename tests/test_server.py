import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATA = tempfile.TemporaryDirectory()
os.environ["NEUTRALIS_POOLS_DATA_DIR"] = TEST_DATA.name

from app import server


class PoolTests(unittest.TestCase):
    def setUp(self):
        server.STORE.data = {"version": 1, "settings": {"wallet": "", "autoSync": True}, "pools": [], "lastSync": None, "lastError": None}

    def tearDown(self):
        try:
            Path(server.DATA_FILE).unlink()
        except FileNotFoundError:
            pass

    def test_manual_pool_records_exact_time(self):
        pool = server.STORE.add_manual({"initialValue": 1000, "token0": "SOL", "token1": "USDC", "startedAt": "2026-09-10T13:37:00+00:00"})
        self.assertEqual(pool["startedAt"], "2026-09-10T13:37:00+00:00")
        self.assertIn("capturedAt", pool["snapshots"][0])

    def test_byreal_import_schedules_24_hours_later(self):
        position = {"externalId": "p1", "poolAddress": "pool", "name": "SOL/USDC", "token0": "SOL", "token1": "USDC", "currentValue": 1200, "initialValue": 1000, "openedAt": "2026-09-01T13:37:00+00:00", "rangeMin": 100, "rangeMax": 200, "currentPrice": 150, "fees": 12, "pnl": 42, "pnlPercent": 4.2, "reportedApr": None}
        with patch.object(server, "now_iso", return_value="2026-09-10T13:37:00+00:00"), patch.object(server, "byreal_positions", return_value=[position]):
            server.STORE.sync_byreal("6BYJDhDgA73eGbLQCPvkvwrJLLi5w1yvBeqzCAnJRmfw")
        pool = server.STORE.data["pools"][0]
        self.assertEqual(pool["nextSnapshotAt"], "2026-09-11T13:37:00+00:00")
        self.assertEqual(pool["feesBaseline"], 0)
        self.assertEqual(pool["initialValue"], 1000)
        self.assertEqual(pool["startedAt"], "2026-09-01T13:37:00+00:00")
        self.assertEqual(pool["snapshots"][0]["fees"], 12)
        self.assertEqual(pool["snapshots"][0]["pnl"], 42)

    def test_close_stops_schedule_and_preserves_history(self):
        pool = server.STORE.add_manual({"initialValue": 100, "token0": "A", "token1": "B"})
        pool["nextSnapshotAt"] = "2026-09-11T13:37:00+00:00"
        before = list(pool["snapshots"])
        server.STORE.close(pool["id"])
        self.assertEqual(pool["status"], "closed")
        self.assertIsNone(pool["nextSnapshotAt"])
        self.assertEqual(pool["snapshots"], before)

    def test_manual_refresh_before_due_updates_live_without_extra_history(self):
        base = {"externalId": "p1", "poolAddress": "pool", "name": "SOL/USDC", "token0": "SOL", "token1": "USDC", "currentValue": 1000, "initialValue": 900, "openedAt": "2026-09-01T10:00:00+00:00", "rangeMin": 100, "rangeMax": 200, "currentPrice": 150, "fees": 20, "pnl": 30, "pnlPercent": 3.3, "reportedApr": None}
        with patch.object(server, "now_iso", return_value="2026-09-10T10:00:00+00:00"), patch.object(server, "byreal_positions", return_value=[base]):
            server.STORE.sync_byreal("6BYJDhDgA73eGbLQCPvkvwrJLLi5w1yvBeqzCAnJRmfw")
        refreshed = {**base, "currentValue": 1010, "initialValue": 1500, "fees": 22, "pnl": 42}
        with patch.object(server, "now_iso", return_value="2026-09-10T11:00:00+00:00"), patch.object(server, "byreal_positions", return_value=[refreshed]):
            server.STORE.sync_byreal("6BYJDhDgA73eGbLQCPvkvwrJLLi5w1yvBeqzCAnJRmfw")
        pool = server.STORE.data["pools"][0]
        self.assertEqual(len(pool["snapshots"]), 1)
        self.assertEqual(pool["live"]["fees"], 22)
        self.assertEqual(pool["initialValue"], 900)
        self.assertEqual(pool["nextSnapshotAt"], "2026-09-11T10:00:00+00:00")

    def test_store_persists(self):
        server.STORE.add_manual({"initialValue": 250, "token0": "ETH", "token1": "USDC"})
        self.assertEqual(server.Store().data["pools"][0]["initialValue"], 250)


if __name__ == "__main__":
    unittest.main()
