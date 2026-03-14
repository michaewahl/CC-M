# CC-M: Claude Model Router

Lightweight Anthropic API proxy that classifies prompt complexity and routes to the cheapest viable Claude model (Haiku / Sonnet / Opus).

## Architecture

```
Client → CC-M (port 8082, FastAPI) → api.anthropic.com
```

CC-M intercepts `/v1/messages`, scores prompt complexity, rewrites the `model` field, forwards via httpx with SSE streaming passthrough, and logs cost savings to SQLite.

## Layout

- `ccm/main.py` — FastAPI app, proxy endpoint, SSE streaming, identity extraction, shadow wiring
- `ccm/classifier.py` — Prompt complexity scorer → ComplexityTier
- `ccm/config.py` — CCMSettings (pydantic-settings, `CCM_` env prefix)
- `ccm/cost.py` — SQLite cost tracking, pricing, GET /stats, governance queries (get_usage)
- `ccm/governance.py` — /usage endpoints for team visibility (APIRouter)
- `ccm/equivalence.py` — Response equivalence scoring (divergence detection)
- `ccm/shadow.py` — Background shadow calibration (dual-route to Opus, compare, report)
- `ccm/compare.py` — CLI demo tool: send prompt to all 3 tiers, show side-by-side

## Running

```bash
cp .env.example .env   # add your API key
pip install -e .
uvicorn ccm.main:app --port 8082
```

## Demo comparison

```bash
python -m ccm.compare "Explain what a Python decorator is"
```

## Endpoints

- `GET /health` — health check
- `GET /stats` — cost tracking dashboard (total savings, model distribution)
- `GET /usage` — governance: who's spending what (filter by user/team, group_by user/team/model/tier/day)
- `GET /usage/user/{id}` — single user daily breakdown
- `GET /usage/teams` — team-level summary
- `GET /calibration` — shadow calibration report (equivalence rates by tier)
- `POST /v1/messages` — Anthropic Messages API proxy (transparent)

## Key constraints

- Pure SSE passthrough — zero response buffering, no added latency
- No multi-pass escalation — single-shot routing only
- Override via `X-CCM-Model-Override` header or `CCM_FORCE_MODEL` env var
- Identity via `X-CCM-User` and `X-CCM-Team` headers (fallback: API key fingerprint)
- Shadow calibration is opt-in (`CCM_CALIBRATION_ENABLED=true`), sampled (20% default), and capped
