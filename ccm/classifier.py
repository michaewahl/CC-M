"""
Prompt complexity classifier.

Scores the incoming messages array to determine which Claude model tier
can handle the request adequately. Signals are additive — more complexity
indicators push the score higher.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger("ccm.classifier")

# --- Task-type keyword patterns ---

_COMPLEX_KEYWORDS = re.compile(
    r"\b(refactor\w*|restructur\w*|redesign\w*|architect\w*|design\s+\w*\s*system|"
    r"migrat\w*|integrat\w*|trade-?off|compare\s+approach|security\s+review|"
    r"performance\s+optim\w*|debug.{0,20}(complex|subtle|race|concurren))",
    re.IGNORECASE,
)

_MEDIUM_KEYWORDS = re.compile(
    r"\b(fix\w*|bug|error|implement\w*|write|create|generat\w*|add\s+feature|"
    r"build\w*|updat\w*|modif\w*|chang\w*|test\w*|debug\w*)\b",
    re.IGNORECASE,
)

_SIMPLE_KEYWORDS = re.compile(
    r"\b(explain|what\s+(does|is)|describe|summarize|format|rename|"
    r"sort|lint|clean\s*up|list|show|print|log|comment)\b",
    re.IGNORECASE,
)

# Patterns that suggest file path references
_FILE_REF = re.compile(
    r"(?:[\w./\\-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|rb|cpp|c|h|css|html|json|yaml|yml|toml|md))\b"
)


class ComplexityTier(str, Enum):
    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"


@dataclass
class ClassificationResult:
    tier: ComplexityTier
    score: float
    task_type: str  # detected keyword category


def _extract_text(messages: list[dict]) -> str:
    """Concatenate all user message text content."""
    parts: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)


def _count_user_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def _has_image_content(messages: list[dict]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
    return False


def _has_code_blocks(text: str) -> bool:
    return "```" in text


def _count_file_references(text: str) -> int:
    return len(set(_FILE_REF.findall(text)))


def _detect_task_type(text: str) -> tuple[str, float]:
    """Return (task_type, score_bonus) based on keyword matching.

    Scores are calibrated so a single keyword can anchor the tier:
    - simple: 0.0  (stays below medium threshold 1.5)
    - medium: 1.5  (meets medium threshold exactly)
    - complex: 3.5 (meets complex threshold exactly)
    """
    if _COMPLEX_KEYWORDS.search(text):
        return "complex", 3.5
    if _MEDIUM_KEYWORDS.search(text):
        return "medium", 1.5
    if _SIMPLE_KEYWORDS.search(text):
        return "simple", 0.0
    return "unknown", 0.5


def _estimate_token_count(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English/code."""
    return len(text) // 4


def classify(
    messages: list[dict],
    tools: list | None = None,
    threshold_medium: float = 1.5,
    threshold_complex: float = 3.5,
) -> ClassificationResult:
    """Score prompt complexity and return a tier classification."""
    score = 0.0
    user_text = _extract_text(messages)

    # Token volume
    tokens = _estimate_token_count(user_text)
    if tokens > 4000:
        score += 2.0
    elif tokens > 1500:
        score += 1.0

    # Multi-turn depth
    user_turns = _count_user_turns(messages)
    if user_turns > 4:
        score += 1.5
    elif user_turns > 2:
        score += 0.5

    # Code blocks
    if _has_code_blocks(user_text):
        score += 0.5

    # File references
    file_refs = _count_file_references(user_text)
    if file_refs > 3:
        score += 2.0
    elif file_refs > 1:
        score += 1.0

    # Tool definitions (agentic = needs capable model)
    if tools:
        score += 3.5

    # Image content (vision)
    if _has_image_content(messages):
        score += 1.5

    # Task type keywords
    task_type, keyword_bonus = _detect_task_type(user_text)
    score += keyword_bonus

    # Determine tier
    if score >= threshold_complex:
        tier = ComplexityTier.COMPLEX
    elif score >= threshold_medium:
        tier = ComplexityTier.MEDIUM
    else:
        tier = ComplexityTier.SIMPLE

    return ClassificationResult(tier=tier, score=round(score, 1), task_type=task_type)
