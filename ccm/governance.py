"""
Governance and visibility endpoints.

Provides /usage endpoints for team-level spend visibility,
user breakdowns, and model distribution analysis.
"""

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["governance"])


def _tracker_from_request(request: Request):
    """Return the shared CostTracker singleton from app state."""
    import ccm.main as _main
    return _main._tracker


@router.get("/usage")
async def usage(
    request: Request,
    user: str = Query("", description="Filter by user_id"),
    team: str = Query("", description="Filter by team_id"),
    days: int = Query(7, ge=1, le=90, description="Lookback period in days"),
    group_by: str = Query("user", description="Group results by: user, team, model, tier, day"),
):
    """Primary governance query — who's spending what, grouped by any dimension."""
    if group_by not in ("user", "team", "model", "tier", "day"):
        group_by = "user"
    return _tracker_from_request(request).get_usage(user=user, team=team, days=days, group_by=group_by)


@router.get("/usage/user/{user_id}")
async def usage_by_user(
    user_id: str,
    request: Request,
    days: int = Query(7, ge=1, le=90),
):
    """Single user daily breakdown."""
    return _tracker_from_request(request).get_usage(user=user_id, days=days, group_by="day")


@router.get("/usage/teams")
async def usage_by_teams(
    request: Request,
    days: int = Query(7, ge=1, le=90),
):
    """Team summary — alias for /usage?group_by=team."""
    return _tracker_from_request(request).get_usage(days=days, group_by="team")
