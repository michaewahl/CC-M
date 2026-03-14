"""
Plugin system for CC-M extensions.

Discovers and loads plugins that implement the CCMPlugin protocol.
The primary consumer is ccm-enterprise, but the interface is generic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("ccm.plugins")


@dataclass
class PluginContext:
    """Shared resources passed to plugins during registration."""

    settings: object
    require_admin: object


@dataclass
class PluginInfo:
    """Metadata returned by a loaded plugin."""

    name: str
    version: str
    tier: str = "community"
    features: list[str] = field(default_factory=list)


@runtime_checkable
class CCMPlugin(Protocol):
    """Protocol that all CC-M plugins must satisfy."""

    def info(self) -> PluginInfo: ...

    def register(self, app: FastAPI, ctx: PluginContext) -> None: ...


_plugins: list[CCMPlugin] = []


def discover_plugins() -> list[CCMPlugin]:
    """Auto-discover installed plugins."""
    global _plugins
    _plugins = []

    try:
        from ccm_enterprise import create_plugin  # type: ignore[import-not-found]

        plugin = create_plugin()
        if isinstance(plugin, CCMPlugin):
            _plugins.append(plugin)
            info = plugin.info()
            log.info("Plugin loaded: %s v%s [%s]", info.name, info.version, info.tier)
        else:
            log.warning("ccm_enterprise.create_plugin() did not return a valid CCMPlugin")
    except ImportError:
        log.debug("ccm_enterprise not installed (community mode)")
    except Exception as exc:
        log.error("Failed to load ccm_enterprise: %s", exc)

    return _plugins


def get_plugins() -> list[CCMPlugin]:
    """Return currently loaded plugins."""
    return _plugins
