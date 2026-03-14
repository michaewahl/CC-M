"""Tests for cost calculation and tracking."""

import tempfile
from pathlib import Path

from ccm.cost import CostTracker, calculate_cost


class TestCostCalculation:
    def test_haiku_pricing(self):
        # 1K input + 1K output with Haiku
        cost = calculate_cost("claude-haiku-4-5-20251001", 1000, 1000)
        expected = (1000 * 1.0 + 1000 * 5.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_sonnet_pricing(self):
        cost = calculate_cost("claude-sonnet-4-6", 1000, 1000)
        expected = (1000 * 3.0 + 1000 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_opus_pricing(self):
        cost = calculate_cost("claude-opus-4-6", 1000, 1000)
        expected = (1000 * 5.0 + 1000 * 25.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_unknown_model_uses_opus_pricing(self):
        cost = calculate_cost("unknown-model", 1000, 1000)
        opus_cost = calculate_cost("claude-opus-4-6", 1000, 1000)
        assert cost == opus_cost

    def test_zero_tokens(self):
        assert calculate_cost("claude-haiku-4-5-20251001", 0, 0) == 0.0

    def test_opus_is_most_expensive(self):
        haiku = calculate_cost("claude-haiku-4-5-20251001", 10000, 5000)
        sonnet = calculate_cost("claude-sonnet-4-6", 10000, 5000)
        opus = calculate_cost("claude-opus-4-6", 10000, 5000)
        assert haiku < sonnet < opus


class TestCostTracker:
    def _make_tracker(self, tmp_path: Path) -> CostTracker:
        return CostTracker(str(tmp_path / "test.db"))

    def test_log_and_stats(self, tmp_path):
        tracker = self._make_tracker(tmp_path)

        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500)
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 3.0, 2000, 1000)

        stats = tracker.get_stats()
        assert stats["total_requests"] == 2
        assert stats["model_distribution"]["haiku"] == 1
        assert stats["model_distribution"]["sonnet"] == 1
        assert stats["model_distribution"]["opus"] == 0

    def test_savings_are_positive_for_cheap_models(self, tmp_path):
        tracker = self._make_tracker(tmp_path)

        record = tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 10000, 5000)
        assert record.savings_usd > 0
        assert record.actual_cost_usd < record.opus_baseline_usd

    def test_savings_are_zero_for_opus(self, tmp_path):
        tracker = self._make_tracker(tmp_path)

        record = tracker.log_request("claude-opus-4-6", "COMPLEX", 5.0, 10000, 5000)
        assert record.savings_usd == 0.0

    def test_stats_empty_db(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        stats = tracker.get_stats()
        assert stats["total_requests"] == 0
        assert stats["cost"]["total_savings_usd"] == 0

    def test_recent_requests_limited(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        for i in range(15):
            tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 100, 50)

        stats = tracker.get_stats()
        assert stats["total_requests"] == 15
        assert len(stats["recent_requests"]) == 10  # capped at 10

    def test_savings_percent(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 10000, 5000)

        stats = tracker.get_stats()
        assert stats["cost"]["savings_percent"] > 0


class TestCostTrackerIdentity:
    """Tests for governance identity columns."""

    def _make_tracker(self, tmp_path: Path) -> CostTracker:
        return CostTracker(str(tmp_path / "test.db"))

    def test_log_with_user_id(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        record = tracker.log_request(
            "claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500,
            user_id="mike", team_id="platform", api_key_fingerprint="key:a1b2c3d4",
        )
        assert record.user_id == "mike"
        assert record.team_id == "platform"
        assert record.api_key_fingerprint == "key:a1b2c3d4"

    def test_default_identity(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        record = tracker.log_request(
            "claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500,
        )
        assert record.user_id == "anonymous"
        assert record.team_id == ""
        assert record.api_key_fingerprint == ""

    def test_get_usage_group_by_user(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500, user_id="alice")
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500, user_id="alice")
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000, user_id="bob")

        usage = tracker.get_usage(group_by="user", days=7)
        assert usage["total"]["requests"] == 3
        assert len(usage["breakdown"]) == 2
        # Breakdown is ordered by cost descending
        users = {row["user"] for row in usage["breakdown"]}
        assert users == {"alice", "bob"}

    def test_get_usage_filter_by_user(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500, user_id="alice")
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000, user_id="bob")

        usage = tracker.get_usage(user="alice", days=7)
        assert usage["total"]["requests"] == 1

    def test_get_usage_filter_by_team(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500,
                            user_id="alice", team_id="eng")
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000,
                            user_id="bob", team_id="support")

        usage = tracker.get_usage(team="eng", days=7)
        assert usage["total"]["requests"] == 1

    def test_get_usage_group_by_team(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500, team_id="eng")
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000, team_id="eng")
        tracker.log_request("claude-opus-4-6", "COMPLEX", 4.0, 3000, 1500, team_id="ml")

        usage = tracker.get_usage(group_by="team", days=7)
        assert len(usage["breakdown"]) == 2
        teams = {row["team"] for row in usage["breakdown"]}
        assert teams == {"eng", "ml"}

    def test_get_usage_group_by_model(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500)
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000)

        usage = tracker.get_usage(group_by="model", days=7)
        assert len(usage["breakdown"]) == 2

    def test_get_usage_model_distribution(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500, user_id="alice")
        tracker.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000, user_id="alice")
        tracker.log_request("claude-opus-4-6", "COMPLEX", 4.0, 3000, 1500, user_id="alice")

        usage = tracker.get_usage(user="alice", group_by="user", days=7)
        row = usage["breakdown"][0]
        assert row["model_distribution"]["haiku"] == 1
        assert row["model_distribution"]["sonnet"] == 1
        assert row["model_distribution"]["opus"] == 1

    def test_schema_migration_idempotent(self, tmp_path):
        """Creating tracker twice on same DB should not fail."""
        db_path = str(tmp_path / "test.db")
        t1 = CostTracker(db_path)
        t1.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 100, 50, user_id="mike")
        t2 = CostTracker(db_path)  # re-open — migration runs again
        stats = t2.get_stats()
        assert stats["total_requests"] == 1

    def test_get_usage_days_capped_at_90(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.log_request("claude-haiku-4-5-20251001", "SIMPLE", 1.0, 1000, 500)
        usage = tracker.get_usage(days=365)
        assert usage["period"]["days"] == 90
