"""
Shadow calibration mode.

When enabled, silently sends a sample of prompts to Opus in the background
while the user gets their response from the cheaper model. Compares the
two responses to build confidence in the classifier's routing decisions.
"""

import asyncio
import json
import logging
import random
import sqlite3
from pathlib import Path

import httpx

from ccm.config import settings
from ccm.cost import calculate_cost
from ccm.equivalence import compare

log = logging.getLogger("ccm.shadow")

_SHADOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    served_model TEXT NOT NULL,
    served_tier TEXT NOT NULL,
    shadow_model TEXT NOT NULL,
    equivalence_score REAL NOT NULL,
    equivalent INTEGER NOT NULL,
    length_ratio REAL NOT NULL,
    code_match INTEGER NOT NULL,
    key_overlap REAL NOT NULL,
    both_completed INTEGER NOT NULL,
    shadow_cost_usd REAL NOT NULL
);
"""


class ShadowRunner:
    """Manages background shadow requests and equivalence logging."""

    def __init__(self, db_path: str):
        resolved = Path(db_path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(resolved)
        self._init_db()
        self._shadow_count = self._get_shadow_count()
        self._lock = asyncio.Lock()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SHADOW_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _get_shadow_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM shadow_log").fetchone()
            return row[0] if row else 0

    def should_shadow(self, tier: str) -> bool:
        """Decide whether to shadow this request.

        Note: the count check here is a best-effort pre-filter only. The
        authoritative cap enforcement happens under _lock in run_shadow.
        """
        if not settings.calibration_enabled:
            return False
        if tier == "COMPLEX":
            return False
        if self._shadow_count >= settings.calibration_max_prompts:
            return False
        return random.random() < settings.calibration_sample_rate

    async def run_shadow(
        self,
        client: httpx.AsyncClient,
        body: dict,
        api_key: str,
        served_model: str,
        served_tier: str,
        served_response_text: str,
    ) -> None:
        """Send the same prompt to Opus in the background, compare, and log."""
        opus_model = settings.model_complex

        shadow_body = {**body, "model": opus_model, "stream": False}

        try:
            # Claim a slot under lock before making the API call to prevent budget overrun
            async with self._lock:
                if self._shadow_count >= settings.calibration_max_prompts:
                    return
                self._shadow_count += 1

            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }

            resp = await client.post(
                f"{settings.anthropic_base_url}/v1/messages",
                json=shadow_body,
                headers=headers,
                timeout=settings.request_timeout,
            )

            if resp.status_code != 200:
                log.debug("Shadow request failed: %d", resp.status_code)
                return

            opus_response = resp.json()

            # Build a mock served response for comparison
            served_response = {
                "content": [{"type": "text", "text": served_response_text}],
                "stop_reason": "end_turn",
            }

            eq = compare(served_response, opus_response)

            # Calculate shadow cost
            usage = opus_response.get("usage", {})
            shadow_cost = calculate_cost(
                opus_model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )

            # Write result to SQLite (slot was already claimed above)
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO shadow_log
                       (served_model, served_tier, shadow_model,
                        equivalence_score, equivalent,
                        length_ratio, code_match, key_overlap,
                        both_completed, shadow_cost_usd)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (served_model, served_tier, opus_model,
                     eq.score, int(eq.equivalent),
                     eq.length_ratio, int(eq.code_match), eq.key_overlap,
                     int(eq.both_completed), shadow_cost),
                )

            log.info(
                "Shadow: %s vs %s → equivalence=%.2f (%s) cost=$%.4f [%d/%d]",
                served_model, opus_model, eq.score,
                "equiv" if eq.equivalent else "DIVERGENT",
                shadow_cost,
                self._shadow_count, settings.calibration_max_prompts,
            )

        except Exception as exc:
            log.debug("Shadow request error (non-fatal): %s", exc)

    def get_calibration_report(self) -> dict:
        """Generate the calibration report from shadow data."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row

            total = conn.execute("SELECT COUNT(*) as n FROM shadow_log").fetchone()["n"]

            if total == 0:
                return {
                    "prompts_shadowed": 0,
                    "equivalence_rate": None,
                    "by_tier": {},
                    "shadow_cost_usd": 0,
                    "status": "no_data",
                    "recommendation": "Enable calibration with CCM_CALIBRATION_ENABLED=true",
                }

            summary = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(equivalent) as equiv_count,
                    COALESCE(SUM(shadow_cost_usd), 0) as total_shadow_cost
                FROM shadow_log
            """).fetchone()

            by_tier = conn.execute("""
                SELECT
                    served_tier,
                    COUNT(*) as count,
                    SUM(equivalent) as equiv_count,
                    AVG(equivalence_score) as avg_score
                FROM shadow_log
                GROUP BY served_tier
            """).fetchall()

            tier_breakdown = {}
            for row in by_tier:
                tier = row["served_tier"]
                count = row["count"]
                equiv = row["equiv_count"]
                tier_breakdown[f"{tier}_vs_OPUS"] = {
                    "count": count,
                    "equivalent": equiv,
                    "rate": round(equiv / count, 2) if count > 0 else 0,
                    "avg_score": round(row["avg_score"], 3),
                }

            equiv_rate = summary["equiv_count"] / summary["total"] if summary["total"] > 0 else 0

            # Generate recommendation
            if equiv_rate >= 0.85:
                rec = "Classifier thresholds are well-calibrated. High equivalence rate."
            elif equiv_rate >= 0.70:
                rec = ("Good equivalence. Consider raising thresholds slightly "
                       "to route more prompts to cheaper models.")
            else:
                rec = ("Equivalence rate is below 70%. Consider lowering thresholds "
                       "to route more prompts to Opus for better quality.")

            return {
                "prompts_shadowed": summary["total"],
                "equivalence_rate": round(equiv_rate, 2),
                "by_tier": tier_breakdown,
                "shadow_cost_usd": round(summary["total_shadow_cost"], 4),
                "status": "complete" if self._shadow_count >= settings.calibration_max_prompts else "in_progress",
                "recommendation": rec,
            }
