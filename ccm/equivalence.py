"""
Response equivalence scoring.

Compares two model responses to estimate whether the cheaper model
produced an equivalent result to Opus. Measures divergence, not quality.

Intentionally crude — flags divergence rather than judging correctness.
Low divergence = cheap model was good enough.
"""

import re
from dataclasses import dataclass


@dataclass
class EquivalenceReport:
    equivalent: bool        # overall verdict (score >= threshold)
    score: float            # 0.0–1.0 aggregate
    length_ratio: float     # len(cheap) / len(expensive), clamped to [0, 2]
    code_match: bool        # both have code blocks, or both don't
    key_overlap: float      # 0.0–1.0 shared key terms
    both_completed: bool    # both finished with end_turn


def _extract_text(response: dict) -> str:
    """Pull text from Anthropic Messages API response."""
    content = response.get("content", [])
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _has_code_blocks(text: str) -> bool:
    return "```" in text


def _extract_key_terms(text: str) -> set[str]:
    """Extract meaningful terms for overlap comparison.

    Pulls out:
    - Identifiers (camelCase, snake_case, PascalCase)
    - File paths
    - Technical terms (3+ chars, not common English)
    """
    # Split on whitespace and punctuation, keep meaningful tokens
    tokens = re.findall(r'\b[a-zA-Z_]\w{2,}\b', text)
    # Normalize to lowercase
    terms = {t.lower() for t in tokens}
    # Remove very common English words
    stopwords = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "has", "her", "was", "one", "our", "out", "this", "that", "with",
        "have", "from", "they", "will", "would", "there", "their", "what",
        "about", "which", "when", "make", "like", "been", "could", "into",
        "than", "other", "some", "them", "then", "these", "also", "each",
        "here", "more", "very", "just", "should", "because", "does", "how",
    }
    return terms - stopwords


def _key_term_overlap(text_a: str, text_b: str) -> float:
    """Jaccard similarity of key terms between two responses."""
    terms_a = _extract_key_terms(text_a)
    terms_b = _extract_key_terms(text_b)
    if not terms_a and not terms_b:
        return 1.0  # both empty = equivalent
    if not terms_a or not terms_b:
        return 0.0
    intersection = terms_a & terms_b
    union = terms_a | terms_b
    return len(intersection) / len(union)


def compare(
    cheap_response: dict,
    expensive_response: dict,
    threshold: float = 0.6,
) -> EquivalenceReport:
    """Compare two Anthropic Messages API responses for equivalence.

    Args:
        cheap_response: Response from the cheaper model
        expensive_response: Response from Opus (the reference)
        threshold: Score >= this means "equivalent"
    """
    cheap_text = _extract_text(cheap_response)
    expensive_text = _extract_text(expensive_response)

    # Length ratio (cheap / expensive), clamped
    exp_len = len(expensive_text) or 1
    raw_ratio = len(cheap_text) / exp_len
    length_ratio = min(raw_ratio, 2.0)

    # Length score: 1.0 when similar length, lower when very different
    # Ideal ratio is 0.7–1.3 (some variation is normal)
    if 0.5 <= length_ratio <= 1.5:
        length_score = 1.0
    elif 0.3 <= length_ratio <= 2.0:
        length_score = 0.5
    else:
        length_score = 0.0

    # Code block match
    cheap_has_code = _has_code_blocks(cheap_text)
    exp_has_code = _has_code_blocks(expensive_text)
    code_match = cheap_has_code == exp_has_code
    code_score = 1.0 if code_match else 0.0

    # Key term overlap
    key_overlap = _key_term_overlap(cheap_text, expensive_text)

    # Completion status
    cheap_stop = cheap_response.get("stop_reason", "")
    exp_stop = expensive_response.get("stop_reason", "")
    both_completed = (cheap_stop == "end_turn" and exp_stop == "end_turn")
    completion_score = 1.0 if both_completed else 0.3

    # Weighted aggregate
    score = (
        0.20 * length_score +
        0.15 * code_score +
        0.45 * key_overlap +
        0.20 * completion_score
    )

    return EquivalenceReport(
        equivalent=score >= threshold,
        score=round(score, 3),
        length_ratio=round(length_ratio, 2),
        code_match=code_match,
        key_overlap=round(key_overlap, 3),
        both_completed=both_completed,
    )
