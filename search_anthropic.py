"""
Anthropic Web Search Adapter
==============================
Uses Claude's built-in web search tool instead of a separate search API.
This means you only need an Anthropic API key to run locally.

The trade-off: each search costs a small amount (model tokens + search fee)
but it eliminates the need for Google/Bing API setup.
"""

import json
import anthropic
import httpx


SEARCH_MODEL = "claude-haiku-4-5-20251001"


class AnthropicSearchAdapter:
    """
    Search adapter that uses Claude's server-side web search tool.
    Drop-in replacement for WebSearchAdapter.
    """

    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.http = httpx.AsyncClient(timeout=15.0)

    async def search(self, query: str, num_results: int = 5) -> list[dict]:
        """
        Search the web using Claude's built-in web search tool.
        Returns list of {"url": ..., "title": ..., "snippet": ...}
        """
        response = await self.client.messages.create(
            model=SEARCH_MODEL,
            max_tokens=1024,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
            }],
            messages=[{
                "role": "user",
                "content": (
                    f"Search the web for: {query}\n\n"
                    f"Return the top {num_results} results. "
                    "For each result, provide the URL, title, and a brief snippet."
                ),
            }],
        )

        # Extract URLs and info from the response
        results = []
        for block in response.content:
            # Look for web search result blocks
            if hasattr(block, "type") and block.type == "web_search_tool_result":
                # Server-side web search returns structured results
                for search_result in getattr(block, "content", []):
                    if hasattr(search_result, "url"):
                        results.append({
                            "url": search_result.url,
                            "title": getattr(search_result, "title", ""),
                            "snippet": getattr(search_result, "text", "")[:300],
                        })
            elif hasattr(block, "text"):
                # If the model responded with text containing URLs,
                # try to parse them out
                pass

        return results[:num_results]

    async def fetch_page(self, url: str, max_chars: int = 15000) -> str:
        """Fetch and return text content from a URL."""
        try:
            resp = await self.http.get(
                url,
                follow_redirects=True,
                headers={"User-Agent": "Tributary/1.0 (source tracer)"},
            )
            resp.raise_for_status()
            text = resp.text

            # Basic HTML stripping — in production use trafilatura or similar
            import re
            # Remove script/style blocks
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            # Remove tags
            text = re.sub(r"<[^>]+>", " ", text)
            # Collapse whitespace
            text = re.sub(r"\s+", " ", text).strip()

            return text[:max_chars]
        except Exception as e:
            return f"[Error fetching {url}: {e}]"

    async def close(self):
        await self.http.aclose()
