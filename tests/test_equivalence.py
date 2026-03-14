"""Tests for response equivalence scoring."""

from ccm.equivalence import compare, _extract_key_terms, _key_term_overlap


def _response(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
    }


class TestEquivalenceCompare:
    def test_identical_responses_are_equivalent(self):
        r = _response("Hello, this is a test response about Python decorators.")
        eq = compare(r, r)
        assert eq.equivalent is True
        assert eq.score >= 0.9
        assert eq.length_ratio == 1.0
        assert eq.key_overlap == 1.0

    def test_similar_responses_are_equivalent(self):
        cheap = _response(
            "A Python decorator wraps a function to modify its behavior. "
            "Use the @syntax to apply decorators."
        )
        expensive = _response(
            "Python decorators are a design pattern that allows you to modify "
            "the behavior of a function. Apply them using the @decorator syntax."
        )
        eq = compare(cheap, expensive)
        assert eq.equivalent is True
        assert eq.key_overlap > 0.3

    def test_divergent_responses(self):
        cheap = _response("I don't know the answer to that question.")
        expensive = _response(
            "Here's a comprehensive implementation of the authentication system:\n"
            "```python\ndef authenticate(user, password):\n    ...\n```"
        )
        eq = compare(cheap, expensive)
        assert eq.equivalent is False
        assert eq.score < 0.6

    def test_code_match_both_have_code(self):
        cheap = _response("```python\ndef foo(): pass\n```")
        expensive = _response("```python\ndef bar(): pass\n```")
        eq = compare(cheap, expensive)
        assert eq.code_match is True

    def test_code_match_one_missing(self):
        cheap = _response("Here's how you do it: just call foo()")
        expensive = _response("```python\ndef foo(): pass\n```")
        eq = compare(cheap, expensive)
        assert eq.code_match is False

    def test_truncated_response_flagged(self):
        cheap = _response("Starting to explain...", stop_reason="max_tokens")
        expensive = _response("Full explanation of the concept.")
        eq = compare(cheap, expensive)
        assert eq.both_completed is False

    def test_empty_responses(self):
        cheap = _response("")
        expensive = _response("")
        eq = compare(cheap, expensive)
        assert eq.equivalent is True  # both empty = equivalent

    def test_length_ratio_calculation(self):
        cheap = _response("Short.")
        expensive = _response("This is a much longer response with more detail.")
        eq = compare(cheap, expensive)
        assert eq.length_ratio < 0.5

    def test_custom_threshold(self):
        cheap = _response("Brief answer about Python.")
        expensive = _response("Detailed answer about Python programming language and its features.")
        # With very high threshold, even decent matches fail
        eq_strict = compare(cheap, expensive, threshold=0.95)
        assert eq_strict.equivalent is False
        # With low threshold, same comparison passes
        eq_lenient = compare(cheap, expensive, threshold=0.2)
        assert eq_lenient.equivalent is True


class TestKeyTermExtraction:
    def test_extracts_identifiers(self):
        terms = _extract_key_terms("The function authenticate_user calls validate_token")
        assert "authenticate_user" in terms
        assert "validate_token" in terms
        assert "function" in terms

    def test_removes_stopwords(self):
        terms = _extract_key_terms("the quick brown fox and the lazy dog")
        assert "the" not in terms
        assert "and" not in terms

    def test_overlap_identical(self):
        text = "Python decorator pattern usage"
        assert _key_term_overlap(text, text) == 1.0

    def test_overlap_zero(self):
        overlap = _key_term_overlap("alpha bravo charlie", "delta echo foxtrot")
        assert overlap == 0.0

    def test_overlap_partial(self):
        overlap = _key_term_overlap(
            "Python decorator function wrapping",
            "Python decorator pattern usage",
        )
        assert 0.0 < overlap < 1.0
