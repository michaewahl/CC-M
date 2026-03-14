"""
Cost tracking and savings calculation.

Logs every request to SQLite with model used, tokens, actual cost,
and what it would have cost with Opus (the baseline for savings).
"""

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("ccm.cost")

# Anthropic pricing per million tokens (as of 2026-03)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
}

# Fallback for unknown models — use Opus pricing (conservative)
_DEFAULT_PRICING = {"input": 5.00, "output": 25.00}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    model_used TEXT NOT NULL,
    complexity_tier TEXT NOT NULL,
    complexity_score REAL NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    actual_cost_usd REAL NOT NULL,
    opus_baseline_usd REAL NOT NULL,
    savings_usd REAL NOT NULL
);
"""


@dataclass
class CostRecord:
    model_used: str
    complexity_tier: str
    complexity_score: float
    input_tokens: int
    output_tokens: int
    actual_cost_usd: float
    opus_baseline_usd: float
    savings_usd: float


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class CostTracker:
    def __init__(self, db_path: str):
        resolved = Path(db_path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(resolved)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def log_request(
        self,
        model_used: str,
        complexity_tier: str,
        complexity_score: float,
        input_tokens: int,
        output_tokens: int,
    ) -> CostRecord:
        actual = calculate_cost(model_used, input_tokens, output_tokens)

        # What would Opus have cost for the same tokens?
        opus_model = "claude-opus-4-6"
        opus_baseline = calculate_cost(opus_model, input_tokens, output_tokens)

        savings = opus_baseline - actual

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO request_log
                   (model_used, complexity_tier, complexity_score,
                    input_tokens, output_tokens,
                    actual_cost_usd, opus_baseline_usd, savings_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (model_used, complexity_tier, complexity_score,
                 input_tokens, output_tokens, actual, opus_baseline, savings),
            )

        record = CostRecord(
            model_used=model_used,
            complexity_tier=complexity_tier,
            complexity_score=complexity_score,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            actual_cost_usd=round(actual, 6),
            opus_baseline_usd=round(opus_baseline, 6),
            savings_usd=round(savings, 6),
        )
        log.info("Cost: $%.4f (saved $%.4f vs Opus) model=%s tier=%s",
                 actual, savings, model_used, complexity_tier)
        return record

    def get_stats(self) -> dict:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row

            summary = conn.execute("""
                SELECT
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN complexity_tier = 'SIMPLE' THEN 1 ELSE 0 END) as haiku_count,
                    SUM(CASE WHEN complexity_tier = 'MEDIUM' THEN 1 ELSE 0 END) as sonnet_count,
                    SUM(CASE WHEN complexity_tier = 'COMPLEX' THEN 1 ELSE 0 END) as opus_count,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                    COALESCE(SUM(actual_cost_usd), 0) as total_cost_usd,
                    COALESCE(SUM(opus_baseline_usd), 0) as total_opus_baseline_usd,
                    COALESCE(SUM(savings_usd), 0) as total_savings_usd
                FROM request_log
            """).fetchone()

            recent = conn.execute("""
                SELECT model_used, complexity_tier, complexity_score,
                       input_tokens, output_tokens,
                       actual_cost_usd, savings_usd, timestamp
                FROM request_log
                ORDER BY id DESC
                LIMIT 10
            """).fetchall()

        total = summary["total_requests"]
        return {
            "total_requests": total,
            "model_distribution": {
                "haiku": summary["haiku_count"],
                "sonnet": summary["sonnet_count"],
                "opus": summary["opus_count"],
            },
            "tokens": {
                "total_input": summary["total_input_tokens"],
                "total_output": summary["total_output_tokens"],
            },
            "cost": {
                "total_actual_usd": round(summary["total_cost_usd"], 4),
                "total_opus_baseline_usd": round(summary["total_opus_baseline_usd"], 4),
                "total_savings_usd": round(summary["total_savings_usd"], 4),
                "savings_percent": (
                    round(summary["total_savings_usd"] / summary["total_opus_baseline_usd"] * 100, 1)
                    if summary["total_opus_baseline_usd"] > 0 else 0
                ),
            },
            "recent_requests": [dict(r) for r in recent],
        }
