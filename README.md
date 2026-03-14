# CC-M: Claude Model Router

**Automatically route Claude API requests to the cheapest model that can handle them.**

CC-M is a lightweight proxy that sits between your app and the Anthropic API. It analyzes each prompt, picks the right model tier (Haiku / Sonnet / Opus), and tracks how much you save.

```
Your App → CC-M (localhost:8082) → api.anthropic.com
```

## Why

| Model | Input $/MTok | Output $/MTok | Good For |
|-------|-------------|---------------|----------|
| Haiku 4.5 | $1 | $5 | Explanations, formatting, simple tasks |
| Sonnet 4.6 | $3 | $15 | Bug fixes, code generation |
| Opus 4.6 | $5 | $25 | Architecture, complex refactors, deep reasoning |

Opus costs **5x more** than Haiku. But "What does this function do?" gets the same answer from both. CC-M stops you from paying Opus prices for Haiku-level tasks.

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/michaelwahl/CC-M.git && cd CC-M
cp .env.example .env
# Edit .env → add your Anthropic API key (CCM_ANTHROPIC_API_KEY=sk-ant-...)

docker compose up -d
```

### Manual

```bash
git clone https://github.com/michaelwahl/CC-M.git && cd CC-M
cp .env.example .env
# Edit .env → add your Anthropic API key (CCM_ANTHROPIC_API_KEY=sk-ant-...)

# Install
uv venv .venv && uv pip install -e .
# OR: python -m venv .venv && pip install -e .

# Run
source .venv/bin/activate
uvicorn ccm.main:app --port 8082
```

Then point your API calls at `http://localhost:8082` instead of `https://api.anthropic.com`:

```bash
curl -X POST http://localhost:8082/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-opus-4-6",
    "messages": [{"role": "user", "content": "What is a Python decorator?"}],
    "max_tokens": 200
  }'
```

Check the response headers — CC-M routed this to **Haiku** instead of Opus:
```
X-CCM-Model-Used: claude-haiku-4-5-20251001
X-CCM-Complexity-Tier: SIMPLE
X-CCM-Complexity-Score: 0.0
```

## How It Works

CC-M scores each prompt based on complexity signals:

| Signal | Effect |
|--------|--------|
| "explain", "rename", "format" keywords | → Haiku |
| "fix", "implement", "create" keywords | → Sonnet |
| "refactor", "architect", "migrate" keywords | → Opus |
| Multiple file references | → pushes toward Opus |
| Tool definitions (agentic workflows) | → pushes toward Opus |
| Image content | → pushes toward Sonnet/Opus |
| Long prompts or deep conversations | → pushes toward Opus |

Scores are additive. More signals = higher score = more capable model.

## Endpoints

| Endpoint | Method | What It Does |
|----------|--------|-------------|
| `/v1/messages` | POST | Anthropic API proxy (transparent) |
| `/health` | GET | Health check |
| `/stats` | GET | Cost savings dashboard |
| `/usage` | GET | Team governance — who's spending what |
| `/usage/user/{id}` | GET | Single user daily breakdown |
| `/usage/teams` | GET | Team-level summary |
| `/calibration` | GET | Shadow calibration report |

> `/stats`, `/usage`, and `/calibration` require `Authorization: Bearer <token>` when `CCM_ADMIN_TOKEN` is set. Unset = open access (dev mode).

### `/usage` — Team Governance

See who's spending what across your team:

```bash
curl http://localhost:8082/usage?group_by=user&days=7
```
```json
{
  "period": {"days": 7},
  "total": {"requests": 847, "cost_usd": 12.34, "savings_usd": 31.56},
  "group_by": "user",
  "breakdown": [
    {
      "user": "mike",
      "requests": 312,
      "cost_usd": 5.67,
      "savings_usd": 14.23,
      "avg_complexity_score": 2.1,
      "model_distribution": {"haiku": 180, "sonnet": 102, "opus": 30}
    }
  ]
}
```

**Identity headers** — label requests with user/team:
```bash
curl -H "X-CCM-User: mike" -H "X-CCM-Team: platform" \
  http://localhost:8082/v1/messages ...
```

No headers? CC-M fingerprints the API key automatically (`key:a1b2c3d4`).

### `/stats` — See Your Savings

```bash
curl http://localhost:8082/stats
```
```json
{
  "total_requests": 142,
  "model_distribution": {"haiku": 89, "sonnet": 38, "opus": 15},
  "cost": {
    "total_actual_usd": 4.21,
    "total_opus_baseline_usd": 38.90,
    "total_savings_usd": 34.69,
    "savings_percent": 89.2
  }
}
```

## Demo: Compare Models Side-by-Side

See the actual difference between model outputs for any prompt:

```bash
python -m ccm.compare "Explain what a Python decorator is"
```

Sends the same prompt to all 3 models, shows outputs + costs + equivalence scores. Useful for proving to your team that Haiku handles simple tasks just as well as Opus.

## Shadow Calibration

Want proof that CC-M's routing is accurate? Enable shadow mode:

```env
CCM_CALIBRATION_ENABLED=true
```

CC-M will silently send 20% of prompts to Opus in the background and compare the answers. Check the report:

```bash
curl http://localhost:8082/calibration
```
```json
{
  "prompts_shadowed": 50,
  "equivalence_rate": 0.84,
  "by_tier": {
    "SIMPLE_vs_OPUS": {"count": 18, "equivalent": 17, "rate": 0.94},
    "MEDIUM_vs_OPUS": {"count": 24, "equivalent": 21, "rate": 0.88}
  },
  "recommendation": "Classifier thresholds are well-calibrated. High equivalence rate."
}
```

Calibration stops automatically after 50 shadows (~$3-5 in Opus costs).

## Override

When you *know* you need Opus:

```bash
# Per-request
curl -H "X-CCM-Model-Override: claude-opus-4-6" ...

# Or globally in .env
CCM_FORCE_MODEL=claude-opus-4-6
```

## Configuration

All env vars use the `CCM_` prefix. Set in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `CCM_ANTHROPIC_API_KEY` | — | Your Anthropic API key |
| `CCM_PORT` | `8082` | Server port |
| `CCM_MODEL_SIMPLE` | `claude-haiku-4-5-20251001` | Model for simple tasks |
| `CCM_MODEL_MEDIUM` | `claude-sonnet-4-6` | Model for medium tasks |
| `CCM_MODEL_COMPLEX` | `claude-opus-4-6` | Model for complex tasks |
| `CCM_THRESHOLD_MEDIUM` | `1.5` | Score cutoff for Sonnet |
| `CCM_THRESHOLD_COMPLEX` | `3.5` | Score cutoff for Opus |
| `CCM_FORCE_MODEL` | — | Force all requests to one model |
| `CCM_GOVERNANCE_ENABLED` | `true` | Enable /usage governance endpoints |
| `CCM_ADMIN_TOKEN` | — | Protect admin endpoints (`Bearer` auth) |
| `CCM_CALIBRATION_ENABLED` | `false` | Enable shadow testing |
| `CCM_CALIBRATION_SAMPLE_RATE` | `0.2` | Fraction of prompts to shadow |
| `CCM_CALIBRATION_MAX_PROMPTS` | `50` | Stop after N shadows |

## Project Structure

```
ccm/
├── main.py           # FastAPI proxy, SSE streaming, shadow wiring
├── classifier.py     # Prompt complexity scoring
├── config.py         # Settings (pydantic-settings)
├── cost.py           # SQLite cost tracking + /stats + governance queries
├── governance.py     # /usage endpoints for team visibility
├── equivalence.py    # Response comparison logic
├── shadow.py         # Background shadow calibration
└── compare.py        # CLI demo tool
tests/
├── test_classifier.py
├── test_cost.py
├── test_equivalence.py
├── test_governance.py
└── test_security.py
Dockerfile
docker-compose.yml
```

## Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

76 tests covering classification, cost calculation, equivalence comparison, governance endpoints, and security (admin auth, override validation).

## CC-RLM Integration

CC-M is a companion to [CC-RLM](https://github.com/michaelwahl/CC-RLM) (Claude Context — REPL + Local Models). They solve different problems:

| Project | What It Does |
|---------|-------------|
| **CC-RLM** | Context packing + local model routing for REPL workflows |
| **CC-M** | Model-tier selection for Anthropic API calls (Haiku/Sonnet/Opus) |

They compose with one env var change — point CC-RLM's Anthropic fallback through CC-M:

```env
# In CC-RLM's .env
CCR_ANTHROPIC_FALLBACK_URL=http://localhost:8082
```

Now CC-RLM's Anthropic fallback gets model-tier optimization for free: simple prompts go to Haiku, complex ones to Opus, with full cost tracking.

## Requirements

- Python 3.12+
- An Anthropic API key

## License

MIT
