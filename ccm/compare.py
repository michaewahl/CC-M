"""
Demo comparison CLI.

Sends a single prompt to all 3 Claude model tiers and displays
the results side-by-side with cost and equivalence analysis.

Usage:
    python -m ccm.compare "Explain what a Python decorator is"
    python -m ccm.compare "Refactor this auth module to use JWT"
"""

import argparse
import asyncio
import sys
import time

import httpx

from ccm.classifier import classify
from ccm.config import settings
from ccm.cost import MODEL_PRICING, calculate_cost
from ccm.equivalence import compare

MODELS = [
    ("HAIKU", settings.model_simple),
    ("SONNET", settings.model_medium),
    ("OPUS", settings.model_complex),
]


def _extract_text(response: dict) -> str:
    content = response.get("content", [])
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


async def _send_prompt(
    client: httpx.AsyncClient,
    prompt: str,
    model: str,
    api_key: str,
) -> tuple[dict, float]:
    """Send prompt to Anthropic, return (response_dict, latency_seconds)."""
    start = time.monotonic()
    resp = await client.post(
        f"{settings.anthropic_base_url}/v1/messages",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        timeout=settings.request_timeout,
    )
    elapsed = time.monotonic() - start

    if resp.status_code != 200:
        return {"error": resp.text, "status": resp.status_code}, elapsed

    return resp.json(), elapsed


async def run_comparison(prompt: str, api_key: str) -> None:
    results: list[tuple[str, str, dict, float]] = []  # (label, model, response, latency)

    async with httpx.AsyncClient() as client:
        # Send to all 3 models concurrently
        tasks = [
            _send_prompt(client, prompt, model, api_key)
            for _, model in MODELS
        ]
        responses = await asyncio.gather(*tasks)

    for (label, model), (response, latency) in zip(MODELS, responses):
        results.append((label, model, response, latency))

    # --- Classification info ---
    messages = [{"role": "user", "content": prompt}]
    classification = classify(messages)

    # --- Display results ---
    print()
    for label, model, response, latency in results:
        print(f"─── {label} ({model}) {'─' * max(1, 56 - len(label) - len(model))}")

        if "error" in response:
            print(f"  ERROR ({response.get('status', '?')}): {response['error'][:200]}")
        else:
            text = _extract_text(response)
            # Truncate long responses for display
            if len(text) > 800:
                text = text[:800] + "\n  [...truncated]"
            print(text)

            usage = response.get("usage", {})
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cost = calculate_cost(model, in_tok, out_tok)
            print(f"\n  Tokens: {in_tok} in / {out_tok} out | "
                  f"Cost: ${cost:.4f} | Latency: {latency:.1f}s")

        print()

    # --- Comparison summary ---
    print(f"─── COMPARISON {'─' * 42}")

    # Show classifier decision
    tier_map = {"SIMPLE": "Haiku", "MEDIUM": "Sonnet", "COMPLEX": "Opus"}
    print(f"  Classifier would route to: {classification.tier.value} "
          f"({tier_map.get(classification.tier.value, '?')}) — "
          f"score {classification.score}")

    # Cost comparison
    opus_response = results[2][2]
    if "error" not in opus_response:
        opus_usage = opus_response.get("usage", {})
        opus_in = opus_usage.get("input_tokens", 0)
        opus_out = opus_usage.get("output_tokens", 0)
        opus_cost = calculate_cost(MODELS[2][1], opus_in, opus_out)

        haiku_response = results[0][2]
        if "error" not in haiku_response:
            haiku_usage = haiku_response.get("usage", {})
            haiku_cost = calculate_cost(
                MODELS[0][1],
                haiku_usage.get("input_tokens", 0),
                haiku_usage.get("output_tokens", 0),
            )
            savings = opus_cost - haiku_cost
            pct = (savings / opus_cost * 100) if opus_cost > 0 else 0
            print(f"  Cost saved (Haiku vs Opus): ${savings:.4f} ({pct:.0f}%)")

        # Equivalence analysis
        for label, _, resp, _ in results[:2]:  # Haiku and Sonnet vs Opus
            if "error" in resp:
                continue
            eq = compare(resp, opus_response)
            print(f"  {label} vs OPUS: equivalence={eq.score:.2f} "
                  f"({'equivalent' if eq.equivalent else 'DIVERGENT'}) "
                  f"| length_ratio={eq.length_ratio} "
                  f"| key_overlap={eq.key_overlap:.0%} "
                  f"| code_match={'yes' if eq.code_match else 'NO'}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Compare Claude model responses side-by-side"
    )
    parser.add_argument("prompt", help="The prompt to send to all models")
    parser.add_argument(
        "--api-key",
        default=settings.anthropic_api_key,
        help="Anthropic API key (or set CCM_ANTHROPIC_API_KEY)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("Error: No API key. Set CCM_ANTHROPIC_API_KEY in .env or pass --api-key")
        sys.exit(1)

    print(f"Sending prompt to {len(MODELS)} models: {', '.join(l for l, _ in MODELS)}")
    print(f"Prompt: \"{args.prompt[:100]}{'...' if len(args.prompt) > 100 else ''}\"")

    asyncio.run(run_comparison(args.prompt, args.api_key))


if __name__ == "__main__":
    main()
