"""Tests for governance visibility endpoints."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ccm.cost import CostTracker


@pytest.fixture
def tracker(tmp_path):
    t = CostTracker(str(tmp_path / "gov.db"))
    # Seed data
    t.log_request("claude-haiku-4-5-20251001", "SIMPLE", 0.5, 1000, 500, user_id="alice", team_id="eng")
    t.log_request("claude-haiku-4-5-20251001", "SIMPLE", 0.8, 1500, 700, user_id="alice", team_id="eng")
    t.log_request("claude-sonnet-4-6", "MEDIUM", 2.5, 2000, 1000, user_id="bob", team_id="eng")
    t.log_request("claude-opus-4-6", "COMPLEX", 4.0, 3000, 1500, user_id="charlie", team_id="ml")
    return t


@pytest.fixture
def client(tracker):
    with patch("ccm.governance._get_tracker", return_value=tracker):
        from ccm.governance import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


class TestUsageEndpoint:
    def test_default_group_by_user(self, client):
        resp = client.get("/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"]["requests"] == 4
        assert data["group_by"] == "user"
        assert len(data["breakdown"]) == 3  # alice, bob, charlie

    def test_filter_by_user(self, client):
        resp = client.get("/usage?user=alice")
        data = resp.json()
        assert data["total"]["requests"] == 2

    def test_filter_by_team(self, client):
        resp = client.get("/usage?team=eng")
        data = resp.json()
        assert data["total"]["requests"] == 3  # alice(2) + bob(1)

    def test_group_by_team(self, client):
        resp = client.get("/usage?group_by=team")
        data = resp.json()
        assert len(data["breakdown"]) == 2  # eng, ml
        teams = {row["team"] for row in data["breakdown"]}
        assert teams == {"eng", "ml"}

    def test_group_by_model(self, client):
        resp = client.get("/usage?group_by=model")
        data = resp.json()
        models = {row["model"] for row in data["breakdown"]}
        assert "claude-haiku-4-5-20251001" in models

    def test_group_by_tier(self, client):
        resp = client.get("/usage?group_by=tier")
        data = resp.json()
        tiers = {row["tier"] for row in data["breakdown"]}
        assert tiers == {"SIMPLE", "MEDIUM", "COMPLEX"}

    def test_invalid_group_by_defaults_to_user(self, client):
        resp = client.get("/usage?group_by=invalid")
        data = resp.json()
        assert data["group_by"] == "user"

    def test_model_distribution_in_breakdown(self, client):
        resp = client.get("/usage?user=alice")
        data = resp.json()
        row = data["breakdown"][0]
        assert row["model_distribution"]["haiku"] == 2
        assert row["model_distribution"]["sonnet"] == 0
        assert row["model_distribution"]["opus"] == 0

    def test_cost_and_savings_present(self, client):
        resp = client.get("/usage")
        data = resp.json()
        assert data["total"]["cost_usd"] > 0
        assert data["total"]["savings_usd"] > 0


class TestUsageByUser:
    def test_user_daily_breakdown(self, client):
        resp = client.get("/usage/user/alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"]["requests"] == 2
        assert data["group_by"] == "day"


class TestUsageByTeams:
    def test_teams_summary(self, client):
        resp = client.get("/usage/teams")
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "team"
        assert len(data["breakdown"]) == 2
