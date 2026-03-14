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
