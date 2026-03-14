"""
Governance and visibility endpoints.

Provides /usage endpoints for team-level spend visibility,
user breakdowns, and model distribution analysis.
"""

from fastapi import APIRouter, Query

from ccm.cost import CostTracker
from ccm.config import settings

router = APIRouter(tags=["governance"])


def _get_tracker() -> CostTracker:
    return CostTracker(settings.store_path)


@router.get("/usage")
async def usage(
    user: str = Query("", description="Filter by user_id"),
    team: str = Query("", description="Filter by team_id"),
    days: int = Query(7, ge=1, le=90, description="Lookback period in days"),
    group_by: str = Query("user", description="Group results by: user, team, model, tier, day"),
):
    """Primary governance query — who's spending what, grouped by any dimension."""
    if group_by not in ("user", "team", "model", "tier", "day"):
        group_by = "user"
    tracker = _get_tracker()
    return tracker.get_usage(user=user, team=team, days=days, group_by=group_by)


@router.get("/usage/user/{user_id}")
async def usage_by_user(
    user_id: str,
    days: int = Query(7, ge=1, le=90),
):
    """Single user daily breakdown."""
    tracker = _get_tracker()
    return tracker.get_usage(user=user_id, days=days, group_by="day")


@router.get("/usage/teams")
async def usage_by_teams(
    days: int = Query(7, ge=1, le=90),
):
    """Team summary — alias for /usage?group_by=team."""
    tracker = _get_tracker()
    return tracker.get_usage(days=days, group_by="team")
