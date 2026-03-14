# CC-M: Claude Model Router — Product Spec

## What Is This?

CC-M is a tool that **automatically picks the right Claude AI model for each task** so you don't waste money sending simple questions to the most expensive model.

Think of it like fuel grades at a gas station:
- **Regular (Haiku)** — cheap, works great for most driving (simple questions, formatting, explanations)
- **Mid-grade (Sonnet)** — moderate cost, for when you need more power (bug fixes, code generation)
- **Premium (Opus)** — expensive, for high-performance needs (complex refactors, architecture design)

Most developers put premium fuel in every car — even the grocery run. CC-M looks at what you're asking and picks the right grade automatically.

---

## The Problem

When developers use Claude through the API, they pay per request. The pricing difference between models is significant:

| Model | Input Cost (per 1M tokens) | Output Cost (per 1M tokens) | Best For |
|-------|---------------------------|----------------------------|----------|
| Haiku 4.5 | $1.00 | $5.00 | Simple tasks, explanations, formatting |
| Sonnet 4.6 | $3.00 | $15.00 | Bug fixes, code writing, moderate tasks |
| Opus 4.6 | $15.00 | $75.00 | Complex reasoning, architecture, multi-file refactors |

**Opus costs 15x more than Haiku for input and 15x more for output.** But for a question like "What does this function do?" — Haiku gives you the same quality answer.

If you're on a subscription plan (Pro/Max), you have limited Opus usage per day. Using Opus for simple tasks means you run out faster when you actually need it.

---

## The Solution

CC-M sits between your application and the Anthropic API. It:

1. **Receives** your request (same format as the normal Anthropic API)
2. **Analyzes** the prompt to determine how complex it is
3. **Picks** the cheapest model that can handle it well
4. **Forwards** the request to Anthropic with the right model
5. **Tracks** how much money you saved

```
Your App                    CC-M                      Anthropic
   |                         |                           |
   |-- "What is Python?" --> |                           |
   |                         |-- Picks Haiku ----------> |
   |                         |<-- Response ------------- |
   |<-- Response ----------- |                           |
   |                         |  (logged: saved $0.01)    |
```

**You don't change your code.** Just point your API calls at CC-M instead of directly at Anthropic. Everything else works the same.

---

## How It Decides Which Model to Use

CC-M scores each prompt based on these signals:

| What It Looks For | Why It Matters | Score Impact |
|-------------------|----------------|-------------|
| Keywords like "explain", "rename", "format" | These are simple tasks | Stays at Haiku |
| Keywords like "fix", "implement", "create" | These need moderate capability | Pushes to Sonnet |
| Keywords like "refactor", "architect", "migrate" | These need deep reasoning | Pushes to Opus |
| Multiple file references (routes.py, models.py, etc.) | Multi-file work is complex | Pushes to Opus |
| Tool definitions in the request | Agentic workflows need smart models | Pushes to Opus |
| Images in the request | Vision tasks need capable models | Pushes toward Sonnet/Opus |
| Very long prompts (4000+ tokens) | More context = more complexity | Pushes toward Opus |
| Many conversation turns (5+) | Deep conversations are harder | Pushes toward Opus |

The scores add up. More complexity signals = higher score = more capable (expensive) model.

**Default thresholds:**
- Score < 1.5 → **Haiku** (cheapest)
- Score 1.5 – 3.4 → **Sonnet** (middle)
- Score >= 3.5 → **Opus** (most capable)

### Examples

| Prompt | Score | Model Picked | Why |
|--------|-------|-------------|-----|
| "What is a Python decorator?" | 0.0 | Haiku | Simple explanation, no code signals |
| "Fix the login bug in the handler" | 1.5 | Sonnet | "Fix" keyword = medium task |
| "Fix this error: \`\`\`code\`\`\`" | 2.0 | Sonnet | "Fix" keyword + code block |
| "Refactor auth across auth.py, routes.py, models.py, tests.py" | 5.5 | Opus | "Refactor" keyword + 4 file refs |

---

## Features

### 1. Smart Routing (always on)

Every request through CC-M gets classified and routed. No configuration needed beyond your API key.

Response headers tell you what happened:
```
X-CCM-Model-Used: claude-haiku-4-5-20241022
X-CCM-Complexity-Tier: SIMPLE
X-CCM-Complexity-Score: 0.0
```

### 2. Cost Dashboard (`GET /stats`)

See how much you've saved:
```json
{
  "total_requests": 142,
  "model_distribution": { "haiku": 89, "sonnet": 38, "opus": 15 },
  "cost": {
    "total_actual_usd": 4.21,
    "total_opus_baseline_usd": 38.90,
    "total_savings_usd": 34.69,
    "savings_percent": 89.2
  }
}
```

### 3. Demo Comparison CLI

See the difference (or lack of difference) for yourself:
```bash
python -m ccm.compare "Explain what a Python decorator is"
```

This sends the same prompt to all 3 models and shows you the results side-by-side with costs. Great for convincing your team that Haiku really does handle simple tasks just as well.

### 4. Shadow Calibration Mode (opt-in)

Want proof that the routing is working well? Turn on calibration:

```
CCM_CALIBRATION_ENABLED=true
```

CC-M will silently send 20% of your prompts to Opus in the background (on top of the normal cheap model response). It compares the two answers and builds a report:

```bash
curl http://localhost:8082/calibration
```

```json
{
  "prompts_shadowed": 50,
  "equivalence_rate": 0.84,
  "by_tier": {
    "SIMPLE_vs_OPUS": { "count": 18, "equivalent": 17, "rate": 0.94 },
    "MEDIUM_vs_OPUS": { "count": 24, "equivalent": 21, "rate": 0.88 }
  },
  "recommendation": "Classifier thresholds are well-calibrated. High equivalence rate."
}
```

This tells you: "94% of the time, Haiku gave you an equivalent answer to Opus." After you're confident, turn calibration off and save money.

### 5. Override Escape Hatch

Sometimes you *know* you need Opus. Two ways to force it:

**Per-request** (header):
```
X-CCM-Model-Override: claude-opus-4-6-20250514
```

**Globally** (env var):
```
CCM_FORCE_MODEL=claude-opus-4-6-20250514
```

---

## Setup

### Requirements
- Python 3.12+
- An Anthropic API key

### Quick Start

```bash
# 1. Clone and install
cd CC-M_API-demo
cp .env.example .env
# Edit .env → add your Anthropic API key

# 2. Create a virtual environment and install
uv venv .venv && uv pip install -e .
# OR: python -m venv .venv && pip install -e .

# 3. Run
source .venv/bin/activate
uvicorn ccm.main:app --port 8082

# 4. Use it — just change your base URL
# Before: https://api.anthropic.com/v1/messages
# After:  http://localhost:8082/v1/messages
```

### Configuration

All settings use environment variables with the `CCM_` prefix. Set them in your `.env` file:

| Variable | Default | What It Does |
|----------|---------|-------------|
| `CCM_ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `CCM_PORT` | 8082 | Port CC-M runs on |
| `CCM_MODEL_SIMPLE` | claude-haiku-4-5-20241022 | Model for simple tasks |
| `CCM_MODEL_MEDIUM` | claude-sonnet-4-6-20250514 | Model for medium tasks |
| `CCM_MODEL_COMPLEX` | claude-opus-4-6-20250514 | Model for complex tasks |
| `CCM_THRESHOLD_MEDIUM` | 1.5 | Score cutoff for Sonnet |
| `CCM_THRESHOLD_COMPLEX` | 3.5 | Score cutoff for Opus |
| `CCM_FORCE_MODEL` | *(empty)* | Force all requests to one model |
| `CCM_CALIBRATION_ENABLED` | false | Turn on shadow testing |
| `CCM_CALIBRATION_SAMPLE_RATE` | 0.2 | What % of prompts to shadow (0.2 = 20%) |
| `CCM_CALIBRATION_MAX_PROMPTS` | 50 | Stop shadowing after this many |

---

## Project Structure

```
CC-M_API-demo/
├── ccm/                        # Main application code
│   ├── main.py                 # Web server — receives requests, routes them
│   ├── classifier.py           # Analyzes prompts to determine complexity
│   ├── config.py               # Settings (reads from .env file)
│   ├── cost.py                 # Tracks spending and savings in SQLite
│   ├── equivalence.py          # Compares two model responses for similarity
│   ├── shadow.py               # Background testing against Opus
│   └── compare.py              # CLI tool for side-by-side comparison
├── tests/                      # Automated tests
│   ├── test_classifier.py      # Tests for prompt classification
│   ├── test_cost.py            # Tests for cost calculation
│   └── test_equivalence.py     # Tests for response comparison
├── .env.example                # Template for your settings
├── pyproject.toml              # Python project config and dependencies
└── CLAUDE.md                   # Quick reference for AI assistants
```

---

## How It Works (Technical Detail)

### The Request Flow

1. **Client sends a request** to `http://localhost:8082/v1/messages` — same format as the Anthropic API
2. **CC-M reads the request body** — extracts the messages array and any tool definitions
3. **Classifier scores the prompt** — checks keywords, token count, file references, etc.
4. **Model is selected** — score maps to a tier (SIMPLE/MEDIUM/COMPLEX), tier maps to a model
5. **Model field is rewritten** — the `model` field in the request body is changed to the selected model
6. **Request is forwarded** to `api.anthropic.com/v1/messages` with the user's API key
7. **Response streams back** — SSE events pass through directly to the client (zero buffering)
8. **Cost is logged** — tokens and cost recorded to SQLite
9. **Shadow fires (if enabled)** — same prompt sent to Opus in background for comparison

### What "Transparent Proxy" Means

CC-M doesn't change the request or response format. It only changes the `model` field. This means:
- Any tool that works with the Anthropic API works with CC-M
- You just change the URL from `api.anthropic.com` to `localhost:8082`
- Response format, streaming, tool use, images — all pass through unchanged

### Equivalence Scoring

When comparing a cheap model's response to Opus (for calibration or the demo CLI), CC-M measures:

- **Length ratio** — is the cheap response roughly the same length? (within 0.5x–1.5x is OK)
- **Code block match** — if Opus included code, did the cheap model also include code?
- **Key term overlap** — do both responses mention the same functions, variables, and concepts?
- **Completion status** — did both models finish their response normally?

This is intentionally simple. It doesn't judge whether an answer is "correct" — it just flags when the two responses are noticeably different. If they're similar, the cheap model was good enough.

---

## Roadmap

### Done (v1.0 + v1.1)
- Smart model routing based on prompt complexity
- Cost tracking with savings dashboard
- Shadow calibration mode
- Demo comparison CLI
- Full test suite (42 tests)

### Future (v2+)
- **Multi-pass escalation** — start with cheap model, automatically retry with Opus if the answer seems incomplete
- **Auto-threshold tuning** — use calibration data to automatically adjust the scoring thresholds
- **Web dashboard** — visual charts for cost savings and model distribution
- **[CC-RLM](https://github.com/michaelwahl/CC-RLM) integration** — chain with the context engine for local + cloud model optimization. One env var change: `CCR_ANTHROPIC_FALLBACK_URL=http://localhost:8082`

---

## FAQ

**Q: Does CC-M read or store my prompts?**
A: CC-M logs token counts and costs to a local SQLite database (`~/.cc-m/cost.db`). In calibration mode, it temporarily stores response text for comparison. It never sends data anywhere except the Anthropic API.

**Q: What if the classifier picks the wrong model?**
A: Worst case, a simple task goes to Opus (you overpay, but get a good answer) or a complex task goes to Haiku (you might get a weaker answer). Use the override header for critical requests. Use calibration mode to measure how often this happens.

**Q: Can I use this with Claude Code?**
A: Not directly yet (Claude Code manages its own API calls). CC-M works with any application that calls the Anthropic Messages API directly.

**Q: How much does shadow calibration cost?**
A: With default settings (20% sample rate, 50 max), you'll spend roughly $3–5 extra on Opus shadow calls. That's a one-time cost to validate your routing.

**Q: What happens if CC-M goes down?**
A: Your requests will fail. CC-M is a proxy — if it's not running, requests don't reach Anthropic. For production use, you'd want health checks and a fallback to direct API calls.
