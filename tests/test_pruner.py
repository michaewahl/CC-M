"""Tests for ccm/pruner.py — tier-aware tool stripping."""

import pytest
from ccm.pruner import prune, _DEFAULT_COMPLEX_ONLY, _DEFAULT_MEDIUM_BLOCKED


def _tools(*names):
    return [{"name": n} for n in names]


# ---------------------------------------------------------------------------
# SIMPLE tier
# ---------------------------------------------------------------------------

def test_simple_strips_bash_and_agent():
    result = prune(_tools("Bash", "grep", "agent"), "SIMPLE")
    assert [t["name"] for t in result.tools] == ["grep"]
    assert set(result.removed_names) == {"Bash", "agent"}


def test_simple_strips_write_file():
    result = prune(_tools("write_file", "read_file"), "SIMPLE")
    assert [t["name"] for t in result.tools] == ["read_file"]


def test_simple_strips_medium_blocked_tools():
    result = prune(_tools("notebook_edit", "grep"), "SIMPLE")
    assert [t["name"] for t in result.tools] == ["grep"]
    assert "notebook_edit" in result.removed_names


def test_simple_all_removed_returns_empty_list():
    result = prune(_tools("bash", "agent"), "SIMPLE")
    assert result.tools == []
    assert result.original_count == 2
    assert result.pruned_count == 0


# ---------------------------------------------------------------------------
# MEDIUM tier
# ---------------------------------------------------------------------------

def test_medium_strips_complex_only_keeps_medium_blocked():
    # notebook_edit is in medium_blocked, not complex_only → should survive MEDIUM
    result = prune(_tools("bash", "notebook_edit", "grep"), "MEDIUM")
    kept = [t["name"] for t in result.tools]
    assert "bash" not in kept
    assert "notebook_edit" in kept
    assert "grep" in kept


def test_medium_strips_agent():
    result = prune(_tools("agent", "grep"), "MEDIUM")
    assert [t["name"] for t in result.tools] == ["grep"]


# ---------------------------------------------------------------------------
# COMPLEX tier — nothing stripped
# ---------------------------------------------------------------------------

def test_complex_keeps_everything():
    tools = _tools("Bash", "agent", "write_file", "notebook_edit", "grep")
    result = prune(tools, "COMPLEX")
    assert result.tools is tools  # same object, untouched
    assert result.removed_names == []


def test_override_keeps_everything():
    tools = _tools("Bash", "agent")
    result = prune(tools, "OVERRIDE")
    assert result.tools is tools
    assert result.removed_names == []


def test_forced_keeps_everything():
    tools = _tools("Bash", "agent")
    result = prune(tools, "FORCED")
    assert result.tools is tools
    assert result.removed_names == []


# ---------------------------------------------------------------------------
# Empty / no tools
# ---------------------------------------------------------------------------

def test_empty_tools_returns_empty():
    result = prune([], "SIMPLE")
    assert result.tools == []
    assert result.removed_names == []
    assert result.original_count == 0


def test_none_tools_graceful():
    # prune is only called when body.get("tools") is truthy, but defensive test
    result = prune([], "MEDIUM")
    assert result.pruned_count == 0


# ---------------------------------------------------------------------------
# Extra blocked (operator config)
# ---------------------------------------------------------------------------

def test_extra_blocked_applied_on_medium():
    extra = frozenset({"my_custom_tool"})
    result = prune(_tools("my_custom_tool", "grep"), "MEDIUM", extra_blocked_names=extra)
    assert [t["name"] for t in result.tools] == ["grep"]
    assert "my_custom_tool" in result.removed_names


def test_extra_blocked_not_applied_on_complex():
    extra = frozenset({"my_custom_tool"})
    tools = _tools("my_custom_tool", "grep")
    result = prune(tools, "COMPLEX", extra_blocked_names=extra)
    assert result.tools is tools  # COMPLEX still untouched


# ---------------------------------------------------------------------------
# Case-insensitive matching
# ---------------------------------------------------------------------------

def test_case_insensitive_bash():
    result = prune(_tools("BASH", "Grep"), "SIMPLE")
    kept = [t["name"] for t in result.tools]
    assert "BASH" not in kept
    assert "Grep" in kept


def test_case_insensitive_agent():
    result = prune(_tools("Agent", "read"), "MEDIUM")
    kept = [t["name"] for t in result.tools]
    assert "Agent" not in kept


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def test_counts_are_accurate():
    tools = _tools("bash", "agent", "grep", "read_file")
    result = prune(tools, "SIMPLE")
    assert result.original_count == 4
    assert result.pruned_count == len(result.tools)
    assert result.original_count == result.pruned_count + len(result.removed_names)
