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
        server.STORE.data = {"version": 2, "settings": {"wallet": "", "autoSync": True, "connections": {}}, "pools": [], "lastSync": None, "lastError": None}

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

    def test_orca_import_keeps_separate_schedule_and_tracks_new_fees(self):
        position = {"externalId": "orca-position", "poolAddress": "whirlpool", "name": "SOL/USDC", "token0": "SOL", "token1": "USDC", "currentValue": 1000, "rangeMin": 100, "rangeMax": 200, "currentPrice": 150, "feesRaw": 7, "openedAt": None, "reportedApr": None, "pnl": None, "pnlPercent": None}
        wallet = "6BYJDhDgA73eGbLQCPvkvwrJLLi5w1yvBeqzCAnJRmfw"
        with patch.object(server, "now_iso", return_value="2026-09-10T10:00:00+00:00"), patch.object(server, "orca_positions", return_value=[position]):
            server.STORE.sync_dex("orca", wallet)
        pool = server.STORE.data["pools"][0]
        self.assertEqual(pool["source"], "orca")
        self.assertEqual(pool["snapshots"][0]["fees"], 7)
        self.assertEqual(pool["nextSnapshotAt"], "2026-09-11T10:00:00+00:00")
        self.assertEqual(server.STORE.data["settings"]["connections"]["orca"], wallet)

        newer = {**position, "currentValue": 1020, "feesRaw": 10}
        with patch.object(server, "now_iso", return_value="2026-09-11T10:00:00+00:00"), patch.object(server, "orca_positions", return_value=[newer]):
            server.STORE.sync_dex("orca", wallet, automatic=True)
        self.assertEqual(pool["snapshots"][-1]["fees"], 10)
        self.assertEqual(pool["nextSnapshotAt"], "2026-09-12T10:00:00+00:00")

    def test_raydium_uses_nft_as_saved_connection(self):
        position = {"externalId": "ray-position", "poolAddress": "pool", "name": "AAPLX/USDC", "token0": "AAPLX", "token1": "USDC", "currentValue": 500, "rangeMin": 100, "rangeMax": 200, "currentPrice": 150, "feesRaw": 1, "openedAt": None, "reportedApr": None, "pnl": None, "pnlPercent": None}
        nft = "6BYJDhDgA73eGbLQCPvkvwrJLLi5w1yvBeqzCAnJRmfw"
        with patch.object(server, "raydium_positions", return_value=[position]):
            result = server.STORE.sync_dex("raydium", nft)
        self.assertEqual(result["created"], 1)
        self.assertEqual(server.STORE.data["settings"]["connections"]["raydium"], nft)

    def test_raydium_position_decodes_liquidity_range_and_fees(self):
        nft = "6cHCWbDnkHehmYh8LcfwKTDdq9ncHGnVuTAVNAQ5kPEw"
        pool = "GYqHjuDzTiw7i52Xv1qohDE6eJr6eSZpsrBVikGZyaFV"
        mint_a = "XsueG8BtpquVJX9LVLLEGuViXUungE6WmK5YZ3p3bd1"
        mint_b = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        position = bytearray(300)
        position[9:41], position[41:73] = server.base58_decode(nft), server.base58_decode(pool)
        position[73:77], position[77:81] = (44480).to_bytes(4, "little", signed=True), (46480).to_bytes(4, "little", signed=True)
        position[81:97] = (10**12).to_bytes(16, "little")
        position[137:145] = (2_000_000).to_bytes(8, "little")
        pool_data = bytearray(300)
        pool_data[73:105], pool_data[105:137] = server.base58_decode(mint_a), server.base58_decode(mint_b)
        pool_data[233] = pool_data[234] = 6
        pool_data[253:269] = int((95**0.5) * 2**64).to_bytes(16, "little")
        with patch.object(server, "solana_account", side_effect=[bytes(position), bytes(pool_data)]):
            result = server.concentrated_position("raydium", nft)
        self.assertEqual(result["name"], "CRCLX/USDC")
        self.assertAlmostEqual(result["feesRaw"], 2)
        self.assertGreater(result["currentValue"], 0)

    def test_orca_position_decodes_official_layout(self):
        nft = "6cHCWbDnkHehmYh8LcfwKTDdq9ncHGnVuTAVNAQ5kPEw"
        pool = "GYqHjuDzTiw7i52Xv1qohDE6eJr6eSZpsrBVikGZyaFV"
        mint_a = "XsueG8BtpquVJX9LVLLEGuViXUungE6WmK5YZ3p3bd1"
        mint_b = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        position = bytearray(216)
        position[:8] = server.ORCA_POSITION_DISCRIMINATOR
        position[8:40], position[40:72] = server.base58_decode(pool), server.base58_decode(nft)
        position[72:88] = (10**12).to_bytes(16, "little")
        position[88:92], position[92:96] = (44480).to_bytes(4, "little", signed=True), (46480).to_bytes(4, "little", signed=True)
        position[136:144] = (3_000_000).to_bytes(8, "little")
        pool_data = bytearray(653)
        pool_data[41:43] = (8).to_bytes(2, "little")
        pool_data[65:81] = int((95**0.5) * 2**64).to_bytes(16, "little")
        pool_data[81:85] = (45480).to_bytes(4, "little", signed=True)
        pool_data[101:133], pool_data[181:213] = server.base58_decode(mint_a), server.base58_decode(mint_b)
        lower_array = bytearray(9988)
        lower_array[:8] = server.ORCA_TICK_ARRAY_DISCRIMINATOR
        lower_array[8:12] = (44352).to_bytes(4, "little", signed=True)
        upper_array = bytearray(9988)
        upper_array[:8] = server.ORCA_TICK_ARRAY_DISCRIMINATOR
        upper_array[8:12] = (46464).to_bytes(4, "little", signed=True)
        mint_a_data, mint_b_data = bytearray(82), bytearray(82)
        mint_a_data[44] = mint_b_data[44] = 6
        with patch.object(server, "solana_account", side_effect=[bytes(pool_data), bytes(lower_array), bytes(upper_array), bytes(mint_a_data), bytes(mint_b_data)]):
            result = server.concentrated_position("orca", nft, bytes(position), server.ORCA_WHIRLPOOL_PROGRAM)
        self.assertEqual(result["name"], "CRCLX/USDC")
        self.assertAlmostEqual(result["feesRaw"], 3)

    def test_orca_pending_fees_include_unupdated_growth(self):
        position = bytearray(216)
        position[72:88] = (10).to_bytes(16, "little")
        position[88:92], position[92:96] = (0).to_bytes(4, "little", signed=True), (8).to_bytes(4, "little", signed=True)
        position[96:112] = (2 << 64).to_bytes(16, "little")
        position[112:120] = (3).to_bytes(8, "little")
        position[120:136] = (4 << 64).to_bytes(16, "little")
        position[136:144] = (5).to_bytes(8, "little")
        pool = bytearray(653)
        pool[41:43] = (8).to_bytes(2, "little")
        pool[81:85] = (4).to_bytes(4, "little", signed=True)
        pool[165:181] = (7 << 64).to_bytes(16, "little")
        pool[245:261] = (10 << 64).to_bytes(16, "little")
        ticks = bytearray(9988)
        ticks[:8] = server.ORCA_TICK_ARRAY_DISCRIMINATOR
        ticks[8:12] = (0).to_bytes(4, "little", signed=True)
        lower, upper = 12, 12 + server.ORCA_TICK_SIZE
        ticks[lower] = ticks[upper] = 1
        ticks[lower + 33:lower + 49] = (1 << 64).to_bytes(16, "little")
        ticks[lower + 49:lower + 65] = (2 << 64).to_bytes(16, "little")
        ticks[upper + 33:upper + 49] = (1 << 64).to_bytes(16, "little")
        ticks[upper + 49:upper + 65] = (1 << 64).to_bytes(16, "little")
        self.assertEqual(server.orca_pending_fees(bytes(position), bytes(pool), bytes(ticks), bytes(ticks)), (33, 35))

    def test_orca_dynamic_tick_array_growth(self):
        ticks = bytearray(60 + 2 * server.ORCA_TICK_SIZE + 86)
        ticks[:8] = server.ORCA_DYNAMIC_TICK_ARRAY_DISCRIMINATOR
        ticks[8:12] = (0).to_bytes(4, "little", signed=True)
        ticks[44:60] = ((1 << 1) | (1 << 4)).to_bytes(16, "little")
        second_initialized = 60 + 1 + server.ORCA_TICK_SIZE + 2
        ticks[second_initialized] = 1
        ticks[second_initialized + 33:second_initialized + 49] = (123).to_bytes(16, "little")
        ticks[second_initialized + 49:second_initialized + 65] = (456).to_bytes(16, "little")
        self.assertEqual(server.orca_tick_growth(bytes(ticks), 32, 8), (True, 123, 456))


if __name__ == "__main__":
    unittest.main()
