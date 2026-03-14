"""
CC-M — Claude Model Router

Lightweight Anthropic API proxy that classifies prompt complexity
and routes to the cheapest viable Claude model.
"""

import asyncio
import json
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ccm.classifier import classify
from ccm.config import settings
from ccm.cost import CostTracker
from ccm.shadow import ShadowRunner

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ccm")

app = FastAPI(title="CC-M — Claude Model Router")

_client: httpx.AsyncClient | None = None
_tracker: CostTracker | None = None
_shadow: ShadowRunner | None = None

_TIER_TO_MODEL = {
    "SIMPLE": settings.model_simple,
    "MEDIUM": settings.model_medium,
    "COMPLEX": settings.model_complex,
}


@app.on_event("startup")
async def startup():
    global _client, _tracker, _shadow
    _client = httpx.AsyncClient(timeout=settings.request_timeout)
    _tracker = CostTracker(settings.store_path)
    _shadow = ShadowRunner(settings.store_path)
    log.info("CC-M started on port %d", settings.port)
    log.info("  Tiers: SIMPLE=%s  MEDIUM=%s  COMPLEX=%s",
             settings.model_simple, settings.model_medium, settings.model_complex)
    if settings.force_model:
        log.info("  FORCE_MODEL=%s (classifier bypassed)", settings.force_model)
    if settings.calibration_enabled:
        log.info("  Calibration: ON (sample_rate=%.0f%%, max=%d)",
                 settings.calibration_sample_rate * 100, settings.calibration_max_prompts)


@app.on_event("shutdown")
async def shutdown():
    if _client:
        await _client.aclose()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cc-m"}


@app.get("/stats")
async def stats():
    return _tracker.get_stats()


@app.get("/calibration")
async def calibration():
    return _shadow.get_calibration_report()


@app.post("/v1/messages")
async def proxy_messages(request: Request):
    body_bytes = await request.body()
    body = json.loads(body_bytes)

    # --- Determine model ---
    # Priority: header override > env force > classifier
    override = request.headers.get("x-ccm-model-override", "")
    if override:
        model = override
        tier_label = "OVERRIDE"
        score = 0.0
        log.info("Model override via header: %s", model)
    elif settings.force_model:
        model = settings.force_model
        tier_label = "FORCED"
        score = 0.0
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
        model = _TIER_TO_MODEL[tier_label]

        if settings.log_classifications:
            log.info("Classified: tier=%s score=%.1f task=%s → model=%s",
                     tier_label, score, result.task_type, model)

    # Rewrite model in request body
    original_model = body.get("model", "")
    body["model"] = model

    # --- Forward to Anthropic ---
    api_key = request.headers.get("x-api-key", "") or settings.anthropic_api_key
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"error": "No API key. Set CCM_ANTHROPIC_API_KEY or pass x-api-key header."},
        )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
        "content-type": "application/json",
    }
    # Pass through optional Anthropic headers
    for h in ("anthropic-beta",):
        if val := request.headers.get(h):
            headers[h] = val

    target = f"{settings.anthropic_base_url}/v1/messages"
    is_streaming = body.get("stream", False)

    if is_streaming:
        return await _stream_response(
            target, headers, body, model, tier_label, score, api_key,
        )
    else:
        return await _sync_response(
            target, headers, body, model, tier_label, score, api_key,
        )


async def _stream_response(
    target: str,
    headers: dict,
    body: dict,
    model: str,
    tier: str,
    score: float,
    api_key: str,
) -> StreamingResponse:
    """SSE passthrough with token counting from stream metadata."""

    async def generate():
        input_tokens = 0
        output_tokens = 0
        content_parts: list[str] = []

        async with _client.stream(
            "POST", target, json=body, headers=headers,
            timeout=settings.request_timeout,
        ) as resp:
            async for line in resp.aiter_lines():
                # Pass through every line as-is
                yield f"{line}\n\n"

                # Extract token counts and content from stream events
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        data = json.loads(line[5:].strip())
                        event_type = data.get("type", "")

                        if event_type == "message_start":
                            usage = data.get("message", {}).get("usage", {})
                            input_tokens = usage.get("input_tokens", 0)

                        elif event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                content_parts.append(delta.get("text", ""))

                        elif event_type == "message_delta":
                            usage = data.get("usage", {})
                            output_tokens = usage.get("output_tokens", 0)
                    except (json.JSONDecodeError, KeyError):
                        pass

        # Stream complete — log cost
        if input_tokens or output_tokens:
            _tracker.log_request(
                model_used=model,
                complexity_tier=tier,
                complexity_score=score,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        # Shadow calibration (fire-and-forget in background)
        if content_parts and _shadow.should_shadow(tier):
            asyncio.create_task(
                _shadow.run_shadow(
                    _client, body, api_key, model, tier,
                    "".join(content_parts),
                )
            )

    response_headers = {
        "X-CCM-Model-Used": model,
        "X-CCM-Complexity-Tier": tier,
        "X-CCM-Complexity-Score": str(score),
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
) -> JSONResponse:
    """Non-streaming: forward, log cost, optionally shadow, return."""
    resp = await _client.post(target, json=body, headers=headers, timeout=settings.request_timeout)
    result = resp.json()

    # Extract tokens from response
    usage = result.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    cost_record = _tracker.log_request(
        model_used=model,
        complexity_tier=tier,
        complexity_score=score,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
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

    response_headers = {
        "X-CCM-Model-Used": model,
        "X-CCM-Complexity-Tier": tier,
        "X-CCM-Complexity-Score": str(score),
        "X-CCM-Cost-USD": str(cost_record.actual_cost_usd),
        "X-CCM-Savings-USD": str(cost_record.savings_usd),
    }

    return JSONResponse(
        content=result,
        status_code=resp.status_code,
        headers=response_headers,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ccm.main:app", host="0.0.0.0", port=settings.port, reload=True)
