"""Tests for security fixes (H1 admin auth, H2 model override validation)."""

import tempfile
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from ccm.cost import MODEL_PRICING, CostTracker
from ccm.shadow import ShadowRunner


@pytest.fixture
def app_with_token(tmp_path):
    """App with admin_token set and tracker/shadow initialized."""
    from ccm.main import app
    import ccm.main as main_mod

    tracker = CostTracker(str(tmp_path / "test.db"))
    shadow = ShadowRunner(str(tmp_path / "test.db"))

    with patch.object(main_mod, "settings") as mock_settings, \
         patch.object(main_mod, "_tracker", tracker), \
         patch.object(main_mod, "_shadow", shadow):
        mock_settings.admin_token = "secret123"
        mock_settings.governance_enabled = True
        mock_settings.calibration_enabled = False
        mock_settings.force_model = ""
        mock_settings.anthropic_api_key = "sk-ant-test1234567890"
        mock_settings.anthropic_base_url = "https://api.anthropic.com"
        mock_settings.request_timeout = 120.0
        mock_settings.port = 8082
        mock_settings.log_classifications = False
        mock_settings.store_path = str(tmp_path / "test.db")
        yield TestClient(app, raise_server_exceptions=False)


class TestModelOverrideValidation:
    """H2 fix: X-CCM-Model-Override must be a known model ID."""

    def test_valid_override_accepted(self, app_with_token):
        """Valid model IDs should be accepted (would fail at Anthropic, but passes validation)."""
        for model in MODEL_PRICING:
            resp = app_with_token.post(
                "/v1/messages",
                json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
                headers={
                    "x-api-key": "sk-ant-test1234567890",
                    "x-ccm-model-override": model,
                },
            )
            # Should NOT be 400 (may fail at Anthropic, that's fine)
            assert resp.status_code != 400, f"Valid model {model} was rejected"

    def test_invalid_override_rejected(self, app_with_token):
        resp = app_with_token.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "x-api-key": "sk-ant-test1234567890",
                "x-ccm-model-override": "gpt-4o-evil-model",
            },
        )
        assert resp.status_code == 400
        assert "Invalid model override" in resp.json()["error"]

    def test_empty_override_ignored(self, app_with_token):
        """Empty override header should fall through to classifier."""
        resp = app_with_token.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "x-api-key": "sk-ant-test1234567890",
                "x-ccm-model-override": "",
            },
        )
        # Should not be 400 — empty string is not an override
        assert resp.status_code != 400


class TestStatsAuthProtection:
    """H1 fix: /stats and /calibration require admin token."""

    def test_stats_rejected_without_token(self, app_with_token):
        resp = app_with_token.get("/stats")
        assert resp.status_code == 401

    def test_stats_accepted_with_token(self, app_with_token):
        resp = app_with_token.get("/stats", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200

    def test_calibration_rejected_without_token(self, app_with_token):
        resp = app_with_token.get("/calibration")
        assert resp.status_code == 401

    def test_health_no_auth_required(self, app_with_token):
        """Health endpoint should always be open."""
        resp = app_with_token.get("/health")
        assert resp.status_code == 200
