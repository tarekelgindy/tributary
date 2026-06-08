"""
Batch-API probe — does web_search work in the Anthropic Message Batches API?
============================================================================
The corpus-cost estimate for Tributary hinges on one unknown: whether the
server-side `web_search` tool runs inside the 50%-off Message Batches API.

This script submits ONE Sonnet + web_search request through the Batch API,
waits for it to finish, and reports:
  (a) whether the batch ran and the request succeeded, and
  (b) whether the response actually contains web_search results
      (i.e. web_search is supported in batch mode).

Cost: ~$0.05 (one small Sonnet call + a couple of searches, at the 50%
batch discount). Run it from the activated venv with ANTHROPIC_API_KEY set:

    python batch_probe.py

A single batched request usually finishes in well under a minute, though the
Batch API's SLA is up to 24h — this script polls for up to ~20 minutes then
tells you to re-check later with the printed batch id.
"""

import sys
import time

import anthropic

MODEL = "claude-sonnet-4-6"
POLL_SECONDS = 15
MAX_WAIT_SECONDS = 20 * 60


def main():
    client = anthropic.Anthropic()

    print("Submitting one Sonnet + web_search request to the Batch API...")
    try:
        batch = client.messages.batches.create(
            requests=[
                {
                    "custom_id": "probe-websearch",
                    "params": {
                        "model": MODEL,
                        "max_tokens": 1024,
                        "tools": [{
                            "type": "web_search_20250305",
                            "name": "web_search",
                            "max_uses": 2,
                        }],
                        "messages": [{
                            "role": "user",
                            "content": (
                                "Search the web: in what year was the Eiffel Tower "
                                "completed? Give the year and cite a source URL."
                            ),
                        }],
                    },
                }
            ]
        )
    except anthropic.APIError as e:
        print(f"\nFAIL: the Batch API rejected the request: {type(e).__name__}: {e}")
        print("If the error mentions the tool/web_search, that itself is the answer: "
              "web_search is not accepted in batch mode (Scenario B).")
        sys.exit(1)

    print(f"  batch id: {batch.id}")
    print(f"  status:   {batch.processing_status}")

    waited = 0
    while batch.processing_status != "ended":
        if waited >= MAX_WAIT_SECONDS:
            print(f"\nStill processing after {waited // 60} min. The Batch API can take "
                  f"up to 24h. Re-check later with:")
            print(f"    python -c \"import anthropic; "
                  f"print(anthropic.Anthropic().messages.batches.retrieve('{batch.id}'))\"")
            sys.exit(0)
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        batch = client.messages.batches.retrieve(batch.id)
        print(f"  ...{batch.processing_status} ({waited}s)")

    print("\nBatch ended. Fetching result...")
    verdict = None
    for r in client.messages.batches.results(batch.id):
        rtype = getattr(r.result, "type", "unknown")
        print(f"  custom_id={r.custom_id}  result={rtype}")
        if rtype == "succeeded":
            msg = r.result.message
            block_types = [getattr(b, "type", "?") for b in msg.content]
            has_search = any(t == "web_search_tool_result" for t in block_types)
            stop = getattr(msg, "stop_reason", None)
            usage = getattr(msg, "usage", None)
            print(f"  stop_reason={stop}")
            print(f"  block types: {block_types}")
            if usage is not None:
                print(f"  usage: input={getattr(usage,'input_tokens','?')} "
                      f"output={getattr(usage,'output_tokens','?')} "
                      f"server_tool_use={getattr(usage,'server_tool_use', None)}")
            verdict = has_search
        else:
            err = getattr(r.result, "error", r.result)
            print(f"  ERROR: {err}")
            verdict = False

    print("\n" + "=" * 60)
    if verdict is True:
        print("RESULT: ✅ web_search WORKS in the Batch API (Scenario A).")
        print("→ The batch runner can route the whole pipeline through Batch")
        print("  for ~50% off tokens. Corpus ≈ $0.20–0.28 per event.")
    elif verdict is False:
        print("RESULT: ❌ web_search did NOT return results in batch (Scenario B).")
        print("→ Batch only discounts the non-search calls; the batch runner")
        print("  should run search calls live and weight toward --max-searches")
        print("  capping. Corpus ≈ $0.30–0.35 per event.")
    else:
        print("RESULT: inconclusive — see the output above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
