"""Tests for prompt complexity classifier."""

from ccm.classifier import ClassificationResult, ComplexityTier, classify


def _user_msg(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _multi_turn(texts: list[str]) -> list[dict]:
    msgs = []
    for i, t in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": t})
    return msgs


class TestSimpleClassification:
    def test_explain_question(self):
        result = classify(_user_msg("What does this function do?"))
        assert result.tier == ComplexityTier.SIMPLE

    def test_rename_task(self):
        result = classify(_user_msg("Rename this variable to snake_case"))
        assert result.tier == ComplexityTier.SIMPLE

    def test_format_task(self):
        result = classify(_user_msg("Format this code and sort the imports"))
        assert result.tier == ComplexityTier.SIMPLE

    def test_short_explain(self):
        result = classify(_user_msg("Explain what a decorator is in Python"))
        assert result.tier == ComplexityTier.SIMPLE
        assert result.task_type == "simple"


class TestMediumClassification:
    def test_fix_bug(self):
        result = classify(_user_msg("Fix the bug in the login handler"))
        assert result.tier == ComplexityTier.MEDIUM

    def test_implement_feature(self):
        result = classify(_user_msg("Implement a retry mechanism for the API client"))
        assert result.tier == ComplexityTier.MEDIUM

    def test_code_block_with_fix(self):
        result = classify(_user_msg(
            "Fix this error:\n```python\ndef foo():\n    return bar\n```"
        ))
        assert result.tier == ComplexityTier.MEDIUM

    def test_multi_file_references(self):
        result = classify(_user_msg(
            "Update the handler in routes.py and the model in models.py"
        ))
        assert result.tier == ComplexityTier.MEDIUM


class TestComplexClassification:
    def test_refactor(self):
        result = classify(_user_msg(
            "Refactor the authentication module to use JWT tokens, "
            "update the middleware in auth.py, routes.py, models.py, and tests.py"
        ))
        assert result.tier == ComplexityTier.COMPLEX

    def test_architect(self):
        result = classify(_user_msg(
            "Design a system architecture for real-time notifications"
        ))
        assert result.tier == ComplexityTier.COMPLEX

    def test_tools_present(self):
        """Tool definitions signal agentic workflow — always complex."""
        tools = [{"name": "read_file", "description": "Read a file"}]
        result = classify(_user_msg("Read the config"), tools=tools)
        assert result.tier == ComplexityTier.COMPLEX

    def test_many_turns(self):
        msgs = _multi_turn([
            "First question", "Answer 1",
            "Follow up", "Answer 2",
            "Another question", "Answer 3",
            "Yet another", "Answer 4",
            "Final deep question about refactoring",
        ])
        result = classify(msgs)
        assert result.tier == ComplexityTier.COMPLEX

    def test_image_content(self):
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "..."}},
            {"type": "text", "text": "What's in this screenshot?"},
        ]}]
        result = classify(msgs)
        # Image (1.5) + simple keyword (0.0) = 1.5 → SIMPLE
        # But if combined with other signals, can push higher
        assert result.score >= 1.5


class TestScoring:
    def test_score_is_additive(self):
        simple = classify(_user_msg("explain this"))
        complex_ = classify(
            _user_msg("refactor auth.py, routes.py, models.py, tests.py, utils.py"),
            tools=[{"name": "t"}],
        )
        assert complex_.score > simple.score

    def test_custom_thresholds(self):
        result = classify(
            _user_msg("Fix the bug"),
            threshold_medium=0.5,
            threshold_complex=1.0,
        )
        # "fix" keyword (1.0) >= complex threshold (1.0)
        assert result.tier == ComplexityTier.COMPLEX

    def test_result_has_all_fields(self):
        result = classify(_user_msg("hello"))
        assert isinstance(result, ClassificationResult)
        assert isinstance(result.tier, ComplexityTier)
        assert isinstance(result.score, float)
        assert isinstance(result.task_type, str)
