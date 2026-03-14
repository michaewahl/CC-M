"""Tests for plugin discovery and registration."""

from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ccm.plugins import (
    CCMPlugin,
    PluginContext,
    PluginInfo,
    discover_plugins,
    get_plugins,
)


# --- Fake plugin for testing ---


class FakePlugin:
    """Minimal plugin that satisfies the CCMPlugin protocol."""

    def info(self) -> PluginInfo:
        return PluginInfo(
            name="fake-enterprise",
            version="0.1.0",
            tier="enterprise",
            features=["policy", "analytics"],
        )

    def register(self, app, ctx) -> None:
        @app.get("/enterprise/test")
        async def _test():
            return {"plugin": "works"}


class TestPluginProtocol:
    def test_fake_plugin_satisfies_protocol(self):
        plugin = FakePlugin()
        assert isinstance(plugin, CCMPlugin)

    def test_missing_method_fails_protocol(self):
        class BadPlugin:
            def info(self):
                return PluginInfo(name="bad", version="0.0.0")

        assert not isinstance(BadPlugin(), CCMPlugin)


class TestPluginDiscovery:
    def test_no_plugins_when_import_blocked(self):
        """When ccm_enterprise is not importable, discover returns empty."""
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "ccm_enterprise":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            plugins = discover_plugins()
            assert plugins == []

    def test_get_plugins_returns_discovered(self):
        import ccm.plugins as mod

        fake = FakePlugin()
        with patch.object(mod, "_plugins", [fake]):
            assert get_plugins() == [fake]


class TestPluginRegistration:
    def test_plugin_mounts_routes(self):
        app = FastAPI()
        plugin = FakePlugin()
        ctx = PluginContext(settings=MagicMock(), require_admin=MagicMock())
        plugin.register(app, ctx)

        client = TestClient(app)
        resp = client.get("/enterprise/test")
        assert resp.status_code == 200
        assert resp.json() == {"plugin": "works"}


class TestLicenseEndpoint:
    def test_community_mode(self):
        """No enterprise plugin installed -> community edition."""
        import ccm.plugins as plugins_mod

        with patch.object(plugins_mod, "_plugins", []), \
             patch("ccm.main._tracker", MagicMock(get_stats=MagicMock(return_value={}))), \
             patch("ccm.main._shadow", MagicMock()), \
             patch("ccm.main.settings") as mock_settings:
            mock_settings.license_key = ""
            mock_settings.admin_token = ""
            from ccm.main import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/license")
            assert resp.status_code == 200
            data = resp.json()
            assert data["edition"] == "community"
            assert data["features"] == []
            assert data["license_configured"] is False

    def test_enterprise_mode(self):
        """Enterprise plugin loaded -> enterprise edition."""
        import ccm.plugins as plugins_mod

        fake = FakePlugin()
        with patch.object(plugins_mod, "_plugins", [fake]), \
             patch("ccm.main._tracker", MagicMock(get_stats=MagicMock(return_value={}))), \
             patch("ccm.main._shadow", MagicMock()), \
             patch("ccm.main.settings") as mock_settings:
            mock_settings.license_key = "lic_live_test123"
            mock_settings.admin_token = ""
            from ccm.main import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/license")
            assert resp.status_code == 200
            data = resp.json()
            assert data["edition"] == "enterprise"
            assert data["plugin"] == "fake-enterprise"
            assert data["version"] == "0.1.0"
            assert "policy" in data["features"]
            assert data["license_configured"] is True
