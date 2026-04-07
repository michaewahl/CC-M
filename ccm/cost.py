"""
Cost tracking and savings calculation.

Logs every request to SQLite with model used, tokens, actual cost,
and what it would have cost with Opus (the baseline for savings).
Tracks user/team identity for governance visibility.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
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

# Governance columns added in v1.2
_MIGRATION_COLUMNS = [
    ("user_id", "TEXT NOT NULL DEFAULT 'anonymous'"),
    ("team_id", "TEXT NOT NULL DEFAULT ''"),
    ("api_key_fingerprint", "TEXT NOT NULL DEFAULT ''"),
]

_GOVERNANCE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_user_id ON request_log(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_team_id ON request_log(team_id)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON request_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_user_timestamp ON request_log(user_id, timestamp)",
]


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
    user_id: str = "anonymous"
    team_id: str = ""
    api_key_fingerprint: str = ""


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
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add governance columns if missing (backward-compatible)."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(request_log)").fetchall()}
        for col_name, col_def in _MIGRATION_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE request_log ADD COLUMN {col_name} {col_def}")
                log.info("Migrated: added column %s to request_log", col_name)
        for idx_sql in _GOVERNANCE_INDEXES:
            conn.execute(idx_sql)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def log_request(
        self,
        model_used: str,
        complexity_tier: str,
        complexity_score: float,
        input_tokens: int,
        output_tokens: int,
        user_id: str = "anonymous",
        team_id: str = "",
        api_key_fingerprint: str = "",
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
                    actual_cost_usd, opus_baseline_usd, savings_usd,
                    user_id, team_id, api_key_fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (model_used, complexity_tier, complexity_score,
                 input_tokens, output_tokens, actual, opus_baseline, savings,
                 user_id, team_id, api_key_fingerprint),
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
            user_id=user_id,
            team_id=team_id,
            api_key_fingerprint=api_key_fingerprint,
        )
        log.info("Cost: $%.4f (saved $%.4f vs Opus) model=%s tier=%s user=%s",
                 actual, savings, model_used, complexity_tier, user_id)
        return record

    def get_daily_spend(self, user_id: str = "", team_id: str = "") -> float:
        """Return total spend today (UTC) for a user or team."""
        with self._connect() as conn:
            if user_id:
                row = conn.execute("""
                    SELECT COALESCE(SUM(actual_cost_usd), 0)
                    FROM request_log
                    WHERE user_id = ? AND date(timestamp) = date('now')
                """, (user_id,)).fetchone()
            elif team_id:
                row = conn.execute("""
                    SELECT COALESCE(SUM(actual_cost_usd), 0)
                    FROM request_log
                    WHERE team_id = ? AND date(timestamp) = date('now')
                """, (team_id,)).fetchone()
            else:
                return 0.0
        return row[0] if row else 0.0

    def get_usage(
        self,
        user: str = "",
        team: str = "",
        days: int = 7,
        group_by: str = "user",
    ) -> dict:
        """Query usage data with filtering and grouping for governance."""
        _ALLOWED_GROUP_BY = {"user", "team", "model", "tier", "day"}
        if group_by not in _ALLOWED_GROUP_BY:
            raise ValueError(f"Invalid group_by '{group_by}'. Must be one of: {sorted(_ALLOWED_GROUP_BY)}")

        # days is already validated as int by callers; clamp defensively
        days_clamped = min(max(int(days), 1), 90)

        # Static map of validated group-by expressions — never interpolate user input
        _GROUP_EXPRS = {
            "user":  ("user_id",         "user_id"),
            "team":  ("team_id",         "team_id"),
            "model": ("model_used",      "model_used"),
            "tier":  ("complexity_tier", "complexity_tier"),
            "day":   ("date(timestamp)", "date_timestamp"),
        }
        group_expr, group_alias = _GROUP_EXPRS.get(group_by, _GROUP_EXPRS["user"])

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row

            # Build WHERE clause — only parameterized values, no interpolation
            params: list = [days_clamped]
            user_clause = ""
            team_clause = ""
            if user:
                user_clause = "AND user_id = ?"
                params.append(user)
            if team:
                team_clause = "AND team_id = ?"
                params.append(team)

            # Totals for the period — static query with parameterized filters
            totals = conn.execute(f"""
                SELECT
                    COUNT(*) as requests,
                    COALESCE(SUM(actual_cost_usd), 0) as cost_usd,
                    COALESCE(SUM(savings_usd), 0) as savings_usd
                FROM request_log
                WHERE timestamp >= datetime('now', '-' || ? || ' days')
                {user_clause} {team_clause}
            """, params).fetchone()

            # Group-by breakdown — group_expr comes from a hard-coded allowlist above
            breakdown = conn.execute(f"""
                SELECT
                    {group_expr} as group_key,
                    COUNT(*) as requests,
                    COALESCE(SUM(actual_cost_usd), 0) as cost_usd,
                    COALESCE(SUM(savings_usd), 0) as savings_usd,
                    COALESCE(AVG(complexity_score), 0) as avg_complexity_score,
                    SUM(CASE WHEN complexity_tier = 'SIMPLE' THEN 1 ELSE 0 END) as haiku_count,
                    SUM(CASE WHEN complexity_tier = 'MEDIUM' THEN 1 ELSE 0 END) as sonnet_count,
                    SUM(CASE WHEN complexity_tier = 'COMPLEX' THEN 1 ELSE 0 END) as opus_count
                FROM request_log
                WHERE timestamp >= datetime('now', '-' || ? || ' days')
                {user_clause} {team_clause}
                GROUP BY {group_expr}
                ORDER BY cost_usd DESC
            """, params).fetchall()

        return {
            "period": {"days": min(days, 90)},
            "total": {
                "requests": totals["requests"],
                "cost_usd": round(totals["cost_usd"], 4),
                "savings_usd": round(totals["savings_usd"], 4),
            },
            "group_by": group_by,
            "breakdown": [
                {
                    group_by: row["group_key"],
                    "requests": row["requests"],
                    "cost_usd": round(row["cost_usd"], 4),
                    "savings_usd": round(row["savings_usd"], 4),
                    "avg_complexity_score": round(row["avg_complexity_score"], 2),
                    "model_distribution": {
                        "haiku": row["haiku_count"],
                        "sonnet": row["sonnet_count"],
                        "opus": row["opus_count"],
                    },
                }
                for row in breakdown
            ],
        }

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
