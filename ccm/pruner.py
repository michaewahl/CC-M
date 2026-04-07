"""
Skill Pruner — tier-aware tool array stripping.

Before forwarding a request to Anthropic, the pruner removes tools that are
inappropriate for the selected model tier:

  SIMPLE  → read-only / safe tools only  (strip execution, agent, MCP heavy tools)
  MEDIUM  → full set minus agent-spawning tools
  COMPLEX → full set, nothing stripped

The allowlist/blocklist is config-driven so operators can tune without code changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("ccm.pruner")

# Default tool names considered "high-risk" for smaller models.
# These are stripped when routing to SIMPLE or MEDIUM tiers.
_DEFAULT_COMPLEX_ONLY: frozenset[str] = frozenset(
    {
        # Agent / sub-process spawning
        "agent",
        "computer_use",
        # Destructive bash / shell execution
        "bash",
        "shell",
        "execute",
        "run_command",
        "computer",
        # File mutation
        "write_file",
        "file_write",
        "edit_file",
        "create_file",
        "delete_file",
        "patch_file",
    }
)

# Tools always stripped from SIMPLE tier even if they'd pass MEDIUM.
_DEFAULT_MEDIUM_BLOCKED: frozenset[str] = frozenset(
    {
        # Heavy MCP / external integrations belong on capable models
        "mcp__magic__21st_magic_component_builder",
        "mcp__magic__21st_magic_component_refiner",
        # Any tool that rewrites code at scale
        "notebook_edit",
        "multi_edit",
    }
)


@dataclass
class PruneResult:
    tools: list[dict]            # pruned tool list (may be same object if nothing removed)
    removed_names: list[str]     # names of tools that were stripped
    original_count: int
    pruned_count: int


def prune(
    tools: list[dict],
    tier: str,
    *,
    complex_only_names: Optional[frozenset[str]] = None,
    medium_blocked_names: Optional[frozenset[str]] = None,
    extra_blocked_names: Optional[frozenset[str]] = None,
) -> PruneResult:
    """Return a (possibly trimmed) tool list appropriate for *tier*.

    Args:
        tools: The raw tools array from the request body.
        tier: "SIMPLE", "MEDIUM", or "COMPLEX" (also accepts "OVERRIDE"/"FORCED"/"TOOL_RESULT").
        complex_only_names: Tools only allowed on COMPLEX tier. Defaults to _DEFAULT_COMPLEX_ONLY.
        medium_blocked_names: Additional tools blocked on SIMPLE tier. Defaults to _DEFAULT_MEDIUM_BLOCKED.
        extra_blocked_names: Operator-supplied additional names to always strip (from config).
    """
    if not tools:
        return PruneResult(tools=[], removed_names=[], original_count=0, pruned_count=0)

    complex_only = complex_only_names if complex_only_names is not None else _DEFAULT_COMPLEX_ONLY
    medium_blocked = medium_blocked_names if medium_blocked_names is not None else _DEFAULT_MEDIUM_BLOCKED
    extra_blocked = extra_blocked_names or frozenset()

    # COMPLEX (and overrides) get the full set — nothing stripped.
    if tier in ("COMPLEX", "OVERRIDE", "FORCED"):
        return PruneResult(
            tools=tools,
            removed_names=[],
            original_count=len(tools),
            pruned_count=len(tools),
        )

    blocked: set[str] = set(complex_only) | set(extra_blocked)
    if tier == "SIMPLE":
        blocked |= set(medium_blocked)

    kept: list[dict] = []
    removed: list[str] = []

    for tool in tools:
        name = (tool.get("name") or "").lower()
        if name in blocked:
            removed.append(tool.get("name", name))
        else:
            kept.append(tool)

    if removed:
        log.info(
            "Skill pruner: tier=%s stripped %d tool(s): %s",
            tier, len(removed), removed,
        )

    return PruneResult(
        tools=kept,
        removed_names=removed,
        original_count=len(tools),
        pruned_count=len(kept),
    )
