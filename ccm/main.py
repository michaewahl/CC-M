"""
CC-M — Claude Model Router

Lightweight Anthropic API proxy that classifies prompt complexity
and routes to the cheapest viable Claude model.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ccm.classifier import classify
from ccm.config import settings
from ccm.cost import CostTracker, MODEL_PRICING
from ccm.governance import router as governance_router
from ccm.plugins import discover_plugins, get_plugins, PluginContext
from ccm.pruner import prune
from ccm.shadow import ShadowRunner

# Valid model IDs for override validation (H2 fix)
_VALID_MODELS = set(MODEL_PRICING.keys())


async def require_admin(request: Request) -> None:
    """Dependency: reject requests without a valid admin token."""
    if not settings.admin_token:
        return  # no token configured → open access (dev mode)
    auth = request.headers.get("authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {settings.admin_token}"):
        raise HTTPException(status_code=401, detail="Admin token required")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ccm")

_client: httpx.AsyncClient | None = None
_tracker: CostTracker | None = None
_shadow: ShadowRunner | None = None

def _tier_to_model(tier_label: str) -> str:
    """Read model mapping from settings at request time (not frozen at import)."""
    return {
        "SIMPLE": settings.model_simple,
        "MEDIUM": settings.model_medium,
        "COMPLEX": settings.model_complex,
    }.get(tier_label, settings.model_complex)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _tracker, _shadow
    _client = httpx.AsyncClient(timeout=settings.request_timeout)
    _tracker = CostTracker(settings.store_path)
    _shadow = ShadowRunner(settings.store_path)
    log.info("CC-M started on port %d", settings.port)
    log.info("  Tiers: SIMPLE=%s  MEDIUM=%s  COMPLEX=%s",
             settings.model_simple, settings.model_medium, settings.model_complex)
    if settings.governance_enabled and not settings.admin_token:
        log.warning(
            "SECURITY WARNING: CCM_ADMIN_TOKEN is not set. "
            "All /stats, /usage, and /calibration endpoints are unauthenticated. "
            "Set CCM_ADMIN_TOKEN in production."
        )
    if settings.force_model:
        log.info("  FORCE_MODEL=%s (classifier bypassed)", settings.force_model)
    if settings.calibration_enabled:
        log.info("  Calibration: ON (sample_rate=%.0f%%, max=%d)",
                 settings.calibration_sample_rate * 100, settings.calibration_max_prompts)

    # Load plugins (e.g., ccm-enterprise)
    ctx = PluginContext(settings=settings, require_admin=require_admin)
    loaded_plugins = discover_plugins()
    for plugin in loaded_plugins:
        try:
            plugin.register(app, ctx)
            log.info("Plugin registered: %s", plugin.info().name)
        except Exception as exc:
            log.error("Plugin registration failed (%s): %s", plugin.info().name, exc)

    if not loaded_plugins:
        log.info("  Edition: community (upgrade: https://buy.stripe.com/eVq7sL3Ry9feaby6x07IY05)")

    yield
    if _client:
        await _client.aclose()


app = FastAPI(title="CC-M — Claude Model Router", lifespan=lifespan)
if settings.governance_enabled:
    app.include_router(governance_router, dependencies=[Depends(require_admin)])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cc-m"}


@app.get("/license")
async def license_info():
    """Current license status and loaded plugins."""
    plugins = get_plugins()
    enterprise = [p for p in plugins if p.info().tier == "enterprise"]
    if enterprise:
        info = enterprise[0].info()
        return {
            "edition": "enterprise",
            "plugin": info.name,
            "version": info.version,
            "features": info.features,
            "license_configured": bool(settings.license_key),
        }
    return {
        "edition": "community",
        "plugin": None,
        "version": None,
        "features": [],
        "license_configured": False,
        "upgrade": "https://buy.stripe.com/eVq7sL3Ry9feaby6x07IY05",
    }


@app.get("/stats", dependencies=[Depends(require_admin)])
async def stats():
    return _tracker.get_stats()


@app.get("/calibration", dependencies=[Depends(require_admin)])
async def calibration():
    return _shadow.get_calibration_report()


@app.post("/v1/messages")
async def proxy_messages(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON in request body"})

    # --- Tool-result detection ---
    # Check if any user message contains tool_result blocks (follow-up after tool execution)
    _messages = body.get("messages", [])
    has_tool_result = any(
        isinstance(msg.get("content"), list) and
        any(isinstance(b, dict) and b.get("type") == "tool_result"
            for b in msg.get("content", []))
        for msg in _messages
        if msg.get("role") == "user"
    )

    # --- Determine model ---
    # Priority: header override > env force > tool_result downgrade > classifier
    override = request.headers.get("x-ccm-model-override", "")
    if override:
        if override not in _VALID_MODELS:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid model override '{override}'. Valid: {sorted(_VALID_MODELS)}"},
            )
        model = override
        tier_label = "OVERRIDE"
        score = 0.0
        log.info("Model override via header: %s", model)
    elif settings.force_model:
        model = settings.force_model
        tier_label = "FORCED"
        score = 0.0
    elif has_tool_result and settings.tool_result_downgrade:
        model = settings.model_simple
        tier_label = "TOOL_RESULT"
        score = 0.0
        log.info("Tool result follow-up → downgraded to %s (skipping classifier)", model)
    else:
        messages = body.get("messages", [])
        tools = body.get("tools")
        result = classify(
            messages,
            tools=tools,
            threshold_medium=settings.threshold_medium,
            threshold_complex=settings.threshold_complex,
        )
        tier_label = result.tier.value
        score = result.score
        model = _tier_to_model(tier_label)

        if settings.log_classifications:
            log.info("Classified: tier=%s score=%.1f task=%s → model=%s",
                     tier_label, score, result.task_type, model)

    # --- Skill Pruner ---
    prune_result = None
    if settings.pruner_enabled and body.get("tools"):
        extra = frozenset(
            n.strip().lower()
            for n in settings.pruner_extra_blocked.split(",")
            if n.strip()
        )
        prune_result = prune(body["tools"], tier_label, extra_blocked_names=extra)
        body["tools"] = prune_result.tools or None  # drop key entirely if all removed
        if prune_result.removed_names:
            log.info(
                "Pruner: tier=%s removed=%s kept=%d/%d",
                tier_label, prune_result.removed_names,
                prune_result.pruned_count, prune_result.original_count,
            )

    # Rewrite model in request body
    original_model = body.get("model", "")
    body["model"] = model

    # --- Swarm governance ---
    _swarm_watch = {n.strip().lower() for n in settings.swarm_tool_names.split(",") if n.strip()}
    request_tools = body.get("tools") or []
    swarm_tools_detected = [
        t.get("name", "") for t in request_tools
        if isinstance(t, dict) and t.get("name", "").lower() in _swarm_watch
    ]
    is_swarm = bool(swarm_tools_detected)

    if is_swarm:
        log.warning("Swarm detected: tools=%s user=%s action=%s",
                    swarm_tools_detected, request.headers.get("x-ccm-user", "anonymous"),
                    settings.swarm_action)

        if settings.swarm_action == "block":
            token = request.headers.get(settings.swarm_require_header, "")
            approved = False
            if settings.swarm_approval_secret:
                # Require HMAC-signed token: HMAC-SHA256(secret, "swarm-approved")
                expected = hmac.new(
                    settings.swarm_approval_secret.encode(),
                    b"swarm-approved",
                    digestmod="sha256",
                ).hexdigest()
                approved = hmac.compare_digest(token, expected)
            else:
                # No secret configured — fall back to presence check only (dev mode)
                approved = token.lower() == "true"
            if not approved:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "swarm_approval_required",
                        "message": "Sub-agent spawning detected. Provide a valid approval token "
                                   f"in the '{settings.swarm_require_header}' header.",
                        "tools_detected": swarm_tools_detected,
                    },
                )
        elif settings.swarm_action == "cap":
            current_max = body.get("max_tokens", settings.swarm_token_cap + 1)
            if current_max > settings.swarm_token_cap:
                body["max_tokens"] = settings.swarm_token_cap
                log.info("Swarm token cap applied: max_tokens capped at %d", settings.swarm_token_cap)

    # --- Identity (governance) ---
    # Always use the configured key — never forward client-supplied credentials.
    api_key = settings.anthropic_api_key
    api_key_fingerprint = (
        "key:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
        if api_key else ""
    )
    user_id = request.headers.get("x-ccm-user", "")
    team_id = request.headers.get("x-ccm-team", "")
    if not user_id:
        user_id = api_key_fingerprint or "anonymous"

    # --- Spend enforcement ---
    if settings.budget_user_daily_usd > 0:
        spent = _tracker.get_daily_spend(user_id=user_id)
        if spent >= settings.budget_user_daily_usd:
            log.warning("Budget exceeded: user=%s spent=$%.4f limit=$%.4f",
                        user_id, spent, settings.budget_user_daily_usd)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "budget_exceeded",
                    "message": f"Daily spend limit reached for user '{user_id}'. "
                               f"Spent: ${spent:.4f}, limit: ${settings.budget_user_daily_usd:.4f}",
                    "spent_usd": round(spent, 4),
                    "limit_usd": settings.budget_user_daily_usd,
                },
            )

    if settings.budget_team_daily_usd > 0 and team_id:
        spent = _tracker.get_daily_spend(team_id=team_id)
        if spent >= settings.budget_team_daily_usd:
            log.warning("Budget exceeded: team=%s spent=$%.4f limit=$%.4f",
                        team_id, spent, settings.budget_team_daily_usd)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "budget_exceeded",
                    "message": f"Daily spend limit reached for team '{team_id}'. "
                               f"Spent: ${spent:.4f}, limit: ${settings.budget_team_daily_usd:.4f}",
                    "spent_usd": round(spent, 4),
                    "limit_usd": settings.budget_team_daily_usd,
                },
            )

    # --- Forward to Anthropic ---
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"error": "No API key. Set CCM_ANTHROPIC_API_KEY."},
        )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
        "content-type": "application/json",
    }
    # Only pass through permitted anthropic-beta feature flags
    _ALLOWED_BETA_FLAGS = {
        "interleaved-thinking-2025-05-14",
        "token-efficient-tools-2025-02-19",
        "extended-cache-ttl-2025-01-13",
        "max-tokens-3-5-sonnet-2024-10-22",
    }
    if beta_val := request.headers.get("anthropic-beta", ""):
        allowed = ",".join(
            f for f in (v.strip() for v in beta_val.split(",")) if f in _ALLOWED_BETA_FLAGS
        )
        if allowed:
            headers["anthropic-beta"] = allowed

    target = f"{settings.anthropic_base_url}/v1/messages"
    is_streaming = body.get("stream", False)

    identity = {"user_id": user_id, "team_id": team_id, "api_key_fingerprint": api_key_fingerprint}
    swarm_meta = {
        "is_swarm": is_swarm,
        "swarm_tools": swarm_tools_detected,
        "tool_result": has_tool_result,
        "tools_pruned": len(prune_result.removed_names) if prune_result else 0,
    }

    if is_streaming:
        return await _stream_response(
            target, headers, body, model, tier_label, score, api_key, identity, swarm_meta,
        )
    else:
        return await _sync_response(
            target, headers, body, model, tier_label, score, api_key, identity, swarm_meta,
        )


async def _stream_response(
    target: str,
    headers: dict,
    body: dict,
    model: str,
    tier: str,
    score: float,
    api_key: str,
    identity: dict | None = None,
    swarm_meta: dict | None = None,
) -> StreamingResponse:
    """SSE passthrough with token counting from stream metadata."""

    async def generate():
        input_tokens = 0
        output_tokens = 0
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        current_tool: dict | None = None

        async with _client.stream(
            "POST", target, json=body, headers=headers,
            timeout=settings.request_timeout,
        ) as resp:
            async for line in resp.aiter_lines():
                # Pass through every line as-is
                yield f"{line}\n\n"

                # Extract token counts, content, and tool calls from stream events
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        data = json.loads(line[5:].strip())
                        event_type = data.get("type", "")

                        if event_type == "message_start":
                            usage = data.get("message", {}).get("usage", {})
                            input_tokens = usage.get("input_tokens", 0)

                        elif event_type == "content_block_start":
                            block = data.get("content_block", {})
                            if block.get("type") == "tool_use":
                                current_tool = {"name": block.get("name", ""), "input_parts": []}

                        elif event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                content_parts.append(delta.get("text", ""))
                            elif delta.get("type") == "input_json_delta" and current_tool is not None:
                                current_tool["input_parts"].append(delta.get("partial_json", ""))

                        elif event_type == "content_block_stop":
                            if current_tool is not None:
                                tool_calls.append({
                                    "name": current_tool["name"],
                                    "input": "".join(current_tool["input_parts"]),
                                })
                                current_tool = None

                        elif event_type == "message_delta":
                            usage = data.get("usage", {})
                            output_tokens = usage.get("output_tokens", 0)
                    except (json.JSONDecodeError, KeyError):
                        pass

        # Stream complete — log cost
        if input_tokens or output_tokens:
            _id = identity or {}
            _tracker.log_request(
                model_used=model,
                complexity_tier=tier,
                complexity_score=score,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                user_id=_id.get("user_id", "anonymous"),
                team_id=_id.get("team_id", ""),
                api_key_fingerprint=_id.get("api_key_fingerprint", ""),
            )

        # Log tool calls detected in stream
        if tool_calls and settings.tool_log_calls:
            _id = identity or {}
            for tc in tool_calls:
                try:
                    input_data = json.loads(tc["input"]) if tc["input"] else {}
                    input_summary = list(input_data.keys())
                except (json.JSONDecodeError, ValueError):
                    input_summary = [f"raw:{len(tc['input'])}chars"]
                log.info("Tool call: tool=%s inputs=%s user=%s model=%s",
                         tc["name"], input_summary, _id.get("user_id", "anonymous"), model)

        # Shadow calibration (fire-and-forget in background)
        if content_parts and _shadow.should_shadow(tier):
            asyncio.create_task(
                _shadow.run_shadow(
                    _client, body, api_key, model, tier,
                    "".join(content_parts),
                )
            )

    _id = identity or {}
    _sw = swarm_meta or {}
    response_headers = {
        "X-CCM-Model-Used": model,
        "X-CCM-Complexity-Tier": tier,
        "X-CCM-Complexity-Score": str(score),
        "X-CCM-User": _id.get("user_id", ""),
        "X-CCM-Team": _id.get("team_id", ""),
        "X-CCM-Swarm-Detected": str(_sw.get("is_swarm", False)).lower(),
        "X-CCM-Tool-Result-Request": str(_sw.get("tool_result", False)).lower(),
        "X-CCM-Tools-Pruned": str(_sw.get("tools_pruned", 0)),
    }

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers=response_headers,
    )


async def _sync_response(
    target: str,
    headers: dict,
    body: dict,
    model: str,
    tier: str,
    score: float,
    api_key: str,
    identity: dict | None = None,
    swarm_meta: dict | None = None,
) -> JSONResponse:
    """Non-streaming: forward, log cost, optionally shadow, return."""
    resp = await _client.post(target, json=body, headers=headers, timeout=settings.request_timeout)
    try:
        result = resp.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=resp.status_code,
            content={"error": "Upstream returned non-JSON response", "status": resp.status_code},
        )

    # Extract tokens from response
    usage = result.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    _id = identity or {}
    cost_record = _tracker.log_request(
        model_used=model,
        complexity_tier=tier,
        complexity_score=score,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        user_id=_id.get("user_id", "anonymous"),
        team_id=_id.get("team_id", ""),
        api_key_fingerprint=_id.get("api_key_fingerprint", ""),
    )

    # Shadow calibration (fire-and-forget in background)
    if _shadow.should_shadow(tier):
        # Extract served response text for comparison
        served_text = ""
        for block in result.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                served_text += block.get("text", "")
        if served_text:
            asyncio.create_task(
                _shadow.run_shadow(
                    _client, body, api_key, model, tier, served_text,
                )
            )

    _sw = swarm_meta or {}

    # Log tool calls from sync response
    tool_calls_sync = [
        b for b in result.get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    if tool_calls_sync and settings.tool_log_calls:
        for tc in tool_calls_sync:
            log.info("Tool call: tool=%s inputs=%s user=%s model=%s",
                     tc.get("name"), list((tc.get("input") or {}).keys()),
                     _id.get("user_id", "anonymous"), model)

    response_headers = {
        "X-CCM-Model-Used": model,
        "X-CCM-Complexity-Tier": tier,
        "X-CCM-Complexity-Score": str(score),
        "X-CCM-Cost-USD": str(cost_record.actual_cost_usd),
        "X-CCM-Savings-USD": str(cost_record.savings_usd),
        "X-CCM-User": _id.get("user_id", ""),
        "X-CCM-Team": _id.get("team_id", ""),
        "X-CCM-Swarm-Detected": str(_sw.get("is_swarm", False)).lower(),
        "X-CCM-Tool-Calls": str(len(tool_calls_sync)),
        "X-CCM-Tool-Result-Request": str(_sw.get("tool_result", False)).lower(),
        "X-CCM-Tools-Pruned": str(_sw.get("tools_pruned", 0)),
    }

    return JSONResponse(
        content=result,
        status_code=resp.status_code,
        headers=response_headers,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ccm.main:app", host="0.0.0.0", port=settings.port, reload=True)
