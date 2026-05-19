"""
Tributary Social Search
========================
Searches social media platforms for how narratives spread among users.
Adds a "downstream spread" layer to the narrative tracing pipeline.

Key insight: web search finds articles and origin points. Social search
finds how regular people adopted, repeated, and mutated the framing.

Supported platforms:
  - Bluesky: Full support (open API, search with date filters)
  - Reddit: Placeholder (needs API key, easy to add)
  - X/Twitter: Placeholder (expensive API)

The spread analyzer uses Haiku to determine whether the pattern
looks like PROPAGATION (one source → many repeaters) or 
CONVERGENCE (many people independently reaching the same conclusion).

Setup:
    pip install httpx
    # Bluesky search requires auth — set these env vars:
    export BLUESKY_HANDLE="yourhandle.bsky.social"
    export BLUESKY_APP_PASSWORD="your-app-password"
    # Get an app password at: Bluesky Settings → App Passwords
"""

import os
import re
import json
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import httpx


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class SpreadType(str, Enum):
    PROPAGATION = "propagation"    # Spread from a source outward
    CONVERGENCE = "convergence"    # Independent parallel emergence
    MIXED = "mixed"                # Elements of both
    UNKNOWN = "unknown"            # Insufficient data


@dataclass
class SocialPost:
    """A single social media post mentioning the narrative."""
    platform: str
    author: str
    text: str
    url: str
    posted_at: str                         # ISO timestamp
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes_url: Optional[str] = None       # If this post quotes/links another


@dataclass
class SpreadAnalysis:
    """Analysis of how a narrative spread on social media."""
    narrative: str
    platform: str
    posts: list[SocialPost] = field(default_factory=list)
    spread_type: SpreadType = SpreadType.UNKNOWN
    total_posts_found: int = 0
    earliest_post: Optional[SocialPost] = None
    most_engaged_post: Optional[SocialPost] = None
    timeline_summary: str = ""
    analysis: str = ""

    def to_dict(self) -> dict:
        return {
            "narrative": self.narrative,
            "platform": self.platform,
            "spread_type": self.spread_type.value,
            "total_posts_found": self.total_posts_found,
            "earliest_post": {
                "author": self.earliest_post.author,
                "text": self.earliest_post.text[:200],
                "posted_at": self.earliest_post.posted_at,
                "url": self.earliest_post.url,
            } if self.earliest_post else None,
            "most_engaged_post": {
                "author": self.most_engaged_post.author,
                "text": self.most_engaged_post.text[:200],
                "posted_at": self.most_engaged_post.posted_at,
                "likes": self.most_engaged_post.likes,
                "reposts": self.most_engaged_post.reposts,
                "url": self.most_engaged_post.url,
            } if self.most_engaged_post else None,
            "timeline_summary": self.timeline_summary,
            "analysis": self.analysis,
            "sample_posts": [
                {
                    "author": p.author,
                    "text": p.text[:200],
                    "posted_at": p.posted_at,
                    "likes": p.likes,
                    "reposts": p.reposts,
                    "url": p.url,
                }
                for p in self.posts[:20]  # Cap at 20 for export
            ],
        }


# ---------------------------------------------------------------------------
# Bluesky social search
# ---------------------------------------------------------------------------

class BlueskySearch:
    """
    Search Bluesky posts for narrative spread.
    Uses the app.bsky.feed.searchPosts endpoint.
    Requires auth (Bluesky app password).
    
    Usage:
        search = BlueskySearch()
        await search.login()
        
        results = await search.search_narrative(
            "the economy is rigged",
            days_back=30,
            limit=50
        )
    """

    API_BASE = "https://bsky.social/xrpc"
    PUBLIC_API = "https://public.api.bsky.app/xrpc"

    def __init__(
        self,
        handle: Optional[str] = None,
        app_password: Optional[str] = None,
    ):
        self.handle = handle or os.environ.get("BLUESKY_HANDLE", "")
        self.app_password = app_password or os.environ.get("BLUESKY_APP_PASSWORD", "")
        self.access_token: Optional[str] = None
        self.http = httpx.AsyncClient(timeout=15.0)

    async def login(self):
        """Authenticate with Bluesky to access search."""
        if not self.handle or not self.app_password:
            print("  [bluesky] No credentials — trying public API (may be limited)")
            return

        try:
            resp = await self.http.post(
                f"{self.API_BASE}/com.atproto.server.createSession",
                json={
                    "identifier": self.handle,
                    "password": self.app_password,
                },
            )
            resp.raise_for_status()
            self.access_token = resp.json()["accessJwt"]
            print(f"  [bluesky] Logged in as @{self.handle}")
        except Exception as e:
            print(f"  [bluesky] Login failed: {e}")
            print("  [bluesky] Falling back to public API")

    async def search_posts(
        self,
        query: str,
        sort: str = "latest",
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 25,
    ) -> list[SocialPost]:
        """
        Search Bluesky posts by keyword.
        
        Args:
            query: Search terms (supports Lucene syntax)
            sort: "latest" or "top"
            since: ISO date string for start of range
            until: ISO date string for end of range
            limit: Max results (1-100)
        """
        params = {
            "q": query,
            "sort": sort,
            "limit": min(limit, 100),
        }
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        # Use authenticated endpoint if logged in, public otherwise
        if self.access_token:
            url = f"{self.API_BASE}/app.bsky.feed.searchPosts"
            headers = {"Authorization": f"Bearer {self.access_token}"}
        else:
            url = f"{self.PUBLIC_API}/app.bsky.feed.searchPosts"
            headers = {}

        try:
            resp = await self.http.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [bluesky] Search error: {e}")
            return []

        posts = []
        for item in data.get("posts", []):
            record = item.get("record", {})
            author = item.get("author", {})
            handle = author.get("handle", "")
            uri = item.get("uri", "")
            post_id = uri.split("/")[-1] if uri else ""

            posts.append(SocialPost(
                platform="bluesky",
                author=handle,
                text=record.get("text", ""),
                url=f"https://bsky.app/profile/{handle}/post/{post_id}",
                posted_at=record.get("createdAt", ""),
                likes=item.get("likeCount", 0),
                reposts=item.get("repostCount", 0),
                replies=item.get("replyCount", 0),
            ))

        return posts

    async def search_narrative(
        self,
        narrative_text: str,
        days_back: int = 30,
        limit: int = 50,
    ) -> SpreadAnalysis:
        """
        Search for a narrative's spread on Bluesky.
        
        Collects a broad pool of posts using multiple query variations
        and both "top" and "latest" sorting, then selects a representative
        sample that maximizes diversity across engagement, time, and authors.
        """
        # Generate search queries from the narrative
        queries = self._generate_queries(narrative_text)
        print(f"  [bluesky] Searching with {len(queries)} queries: {queries}")

        since = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Phase 1: Collect a broad pool from both "top" and "latest"
        all_posts: list[SocialPost] = []
        seen_urls: set[str] = set()

        for query in queries:
            for sort_mode in ("top", "latest"):
                posts = await self.search_posts(
                    query=query,
                    sort=sort_mode,
                    since=since,
                    limit=100,
                )
                for post in posts:
                    if post.url not in seen_urls:
                        seen_urls.add(post.url)
                        all_posts.append(post)

        print(f"  [bluesky] Collected {len(all_posts)} total posts")

        # Phase 2: Sample a representative subset
        if len(all_posts) > limit:
            sample = self._sample_representative(all_posts, limit)
            print(f"  [bluesky] Sampled {len(sample)} representative posts from {len(all_posts)}")
        else:
            sample = all_posts

        # Sort sample by date for timeline
        sample.sort(key=lambda p: p.posted_at or "")

        # Build analysis
        analysis = SpreadAnalysis(
            narrative=narrative_text,
            platform="bluesky",
            posts=sample,
            total_posts_found=len(all_posts),
        )

        if all_posts:
            all_posts.sort(key=lambda p: p.posted_at or "")
            analysis.earliest_post = all_posts[0]
            analysis.most_engaged_post = max(
                all_posts, key=lambda p: p.likes + p.reposts
            )
            # Build timeline from ALL posts (not just sample)
            analysis.timeline_summary = self._build_timeline(all_posts)

        return analysis

    def _sample_representative(
        self, posts: list[SocialPost], target: int
    ) -> list[SocialPost]:
        """
        Select a representative sample from a large pool of posts.
        
        Strategy: allocate slots across three dimensions to ensure diversity.
          - Top engaged (33%): highest likes+reposts — the posts that shaped the conversation
          - Time spread (33%): spread evenly across the date range — captures evolution
          - Author diversity (33%): one post per unique author — avoids over-representation
        
        Any remaining slots filled from whatever's left.
        """
        if len(posts) <= target:
            return posts

        selected_urls: set[str] = set()
        selected: list[SocialPost] = []

        def add(post: SocialPost):
            if post.url not in selected_urls:
                selected_urls.add(post.url)
                selected.append(post)

        slots_per_category = target // 3

        # 1. Top engaged posts
        by_engagement = sorted(
            posts, key=lambda p: p.likes + p.reposts, reverse=True
        )
        for p in by_engagement:
            if len(selected) >= slots_per_category:
                break
            add(p)

        # 2. Time spread — divide the date range into buckets, pick one from each
        dated_posts = [p for p in posts if p.posted_at]
        if dated_posts:
            dated_posts.sort(key=lambda p: p.posted_at)
            num_buckets = slots_per_category
            bucket_size = max(1, len(dated_posts) // num_buckets)

            for i in range(0, len(dated_posts), bucket_size):
                if len(selected) >= slots_per_category * 2:
                    break
                bucket = dated_posts[i:i + bucket_size]
                # Pick the most engaged post from each time bucket
                best = max(bucket, key=lambda p: p.likes + p.reposts)
                add(best)

        # 3. Author diversity — one post per unique author, prefer their best
        by_author: dict[str, list[SocialPost]] = {}
        for p in posts:
            by_author.setdefault(p.author, []).append(p)

        # Sort authors by their best post's engagement
        author_best = sorted(
            by_author.items(),
            key=lambda kv: max(p.likes + p.reposts for p in kv[1]),
            reverse=True,
        )

        for author, author_posts in author_best:
            if len(selected) >= target:
                break
            best = max(author_posts, key=lambda p: p.likes + p.reposts)
            add(best)

        # 4. Fill remaining slots from unselected posts (random-ish)
        remaining = [p for p in posts if p.url not in selected_urls]
        import random
        random.shuffle(remaining)
        for p in remaining:
            if len(selected) >= target:
                break
            add(p)

        return selected

    def _generate_queries(self, narrative_text: str) -> list[str]:
        """
        Generate search queries that capture the narrative and its variations.
        Uses multiple strategies to catch different phrasings of the same idea.
        """
        queries = []

        # Extract key words (remove common words)
        stop_words = {
            "the", "is", "a", "an", "and", "or", "but", "in", "on", "at",
            "to", "for", "of", "with", "by", "from", "that", "this", "it",
            "are", "was", "were", "be", "been", "being", "have", "has",
            "had", "do", "does", "did", "will", "would", "could", "should",
            "may", "might", "can", "shall", "not", "no", "so", "if", "as",
            "just", "about", "more", "very", "than", "also", "into", "its",
        }
        words = [
            w for w in re.findall(r'\b\w+\b', narrative_text.lower())
            if w not in stop_words and len(w) > 2
        ]

        # Strategy 1: Exact phrase (if short enough)
        if len(narrative_text) < 60:
            queries.append(f'"{narrative_text}"')

        # Strategy 2: Key noun phrases (2-3 word combinations)
        if len(words) >= 2:
            queries.append(" ".join(words[:3]))
        if len(words) >= 4:
            queries.append(" ".join(words[1:4]))
        if len(words) >= 5:
            queries.append(" ".join(words[-3:]))

        # Strategy 3: Individual distinctive words (catches looser mentions)
        # Pick the most distinctive words (longer = more distinctive)
        distinctive = sorted(words, key=len, reverse=True)[:2]
        if distinctive:
            queries.append(" ".join(distinctive))

        # Strategy 4: Names and proper nouns (if present in the text)
        # Look for capitalized words in the original text
        proper_nouns = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', narrative_text)
        for name in proper_nouns[:2]:
            if name.lower() not in stop_words and len(name) > 3:
                queries.append(name)

        # Deduplicate while preserving order
        seen = set()
        unique_queries = []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                unique_queries.append(q)

        return unique_queries[:6]

    def _build_timeline(self, posts: list[SocialPost]) -> str:
        """Build a human-readable timeline summary."""
        if not posts:
            return "No posts found."

        # Group by day
        days: dict[str, list[SocialPost]] = {}
        for p in posts:
            if p.posted_at:
                day = p.posted_at[:10]  # YYYY-MM-DD
                days.setdefault(day, []).append(p)

        lines = []
        for day in sorted(days.keys()):
            day_posts = days[day]
            total_engagement = sum(p.likes + p.reposts for p in day_posts)
            top_author = max(day_posts, key=lambda p: p.likes + p.reposts)
            lines.append(
                f"  {day}: {len(day_posts)} posts, "
                f"{total_engagement} total engagement, "
                f"top: @{top_author.author} ({top_author.likes} likes)"
            )

        return "\n".join(lines)

    async def close(self):
        await self.http.aclose()


# ---------------------------------------------------------------------------
# Reddit social search (placeholder — needs API key)
# ---------------------------------------------------------------------------

class RedditSearch:
    """
    Search Reddit for narrative spread.
    Requires a Reddit API application (free).
    
    Setup:
        1. Go to https://www.reddit.com/prefs/apps
        2. Create a "script" application
        3. Set env vars:
           export REDDIT_CLIENT_ID="your-client-id"
           export REDDIT_CLIENT_SECRET="your-secret"
    
    Not yet implemented — structure shows what it would do.
    """

    async def search_narrative(
        self, narrative_text: str, subreddits: list[str] = None, days_back: int = 30
    ) -> SpreadAnalysis:
        # Would search Reddit API for posts/comments matching the narrative
        # across specified subreddits (or all of Reddit)
        # Reddit's search supports: q, sort, time, subreddit, limit
        raise NotImplementedError(
            "Reddit search not yet implemented. "
            "Set up a Reddit API app and implement the OAuth flow."
        )


# ---------------------------------------------------------------------------
# Spread pattern analyzer
# ---------------------------------------------------------------------------

class SpreadAnalyzer:
    """
    Analyzes social media search results to determine whether a narrative
    spread through PROPAGATION or CONVERGENCE.
    
    Uses Haiku to analyze the pattern — cheap and fast.
    """

    def __init__(self, anthropic_client=None):
        import anthropic
        self.client = anthropic_client or anthropic.AsyncAnthropic()

    async def analyze(self, spread: SpreadAnalysis) -> SpreadAnalysis:
        """
        Analyze the spread pattern and classify as propagation vs convergence.
        Adds spread_type and analysis to the SpreadAnalysis object.
        """
        if not spread.posts:
            spread.spread_type = SpreadType.UNKNOWN
            spread.analysis = "No social media posts found for this narrative."
            return spread

        # Build a summary of the posts for Haiku to analyze
        post_summary = []
        for p in spread.posts[:30]:  # Cap to control token usage
            post_summary.append({
                "author": p.author,
                "date": p.posted_at[:10] if p.posted_at else "unknown",
                "text": p.text[:200],
                "likes": p.likes,
                "reposts": p.reposts,
            })

        prompt = f"""Analyze how this narrative spread on social media.

Narrative: "{spread.narrative}"

Posts found ({spread.total_posts_found} total, showing first {len(post_summary)}):
{json.dumps(post_summary, indent=2)}

Timeline:
{spread.timeline_summary}

Determine:
1. SPREAD TYPE — is this:
   - "propagation": The posts show signs of spreading from a source. Signals: 
     similar wording across posts, burst of activity after a specific event or 
     viral post, users explicitly referencing or quoting each other, repost/quote 
     chains visible.
   - "convergence": People independently arrived at this framing. Signals: 
     different wording expressing the same idea, posts spread across time 
     rather than clustered, no evidence of shared source, the framing is an 
     obvious conclusion from current events.
   - "mixed": Elements of both — perhaps an initial organic insight that then 
     got amplified after a viral post.
   
2. KEY OBSERVATIONS about the spread pattern
3. NOTABLE VOICES — any users with high engagement who may have amplified it
4. TIMELINE PATTERN — did usage spike on certain dates?

Respond with ONLY JSON:
{{
  "spread_type": "propagation|convergence|mixed",
  "analysis": "2-3 sentence analysis of the spread pattern",
  "key_amplifiers": ["@handle1", "@handle2"],
  "spike_dates": ["YYYY-MM-DD"],
  "notable_observations": "anything else interesting about the pattern"
}}"""

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text

            # Parse JSON (handle markdown fences)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]

            result = json.loads(cleaned.strip())

            spread.spread_type = SpreadType(result.get("spread_type", "unknown"))
            spread.analysis = result.get("analysis", "")

            return spread

        except Exception as e:
            print(f"  [spread] Analysis error: {e}")
            spread.spread_type = SpreadType.UNKNOWN
            spread.analysis = f"Analysis failed: {e}"
            return spread


# ---------------------------------------------------------------------------
# CLI for standalone testing
# ---------------------------------------------------------------------------

async def main():
    """Test social search independently."""
    import sys

    narrative = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "the economy is rigged"

    print(f"Searching Bluesky for: \"{narrative}\"\n")

    bsky = BlueskySearch()
    await bsky.login()

    spread = await bsky.search_narrative(narrative, days_back=30, limit=50)

    print(f"\nFound {spread.total_posts_found} posts\n")

    if spread.earliest_post:
        print(f"Earliest: @{spread.earliest_post.author} ({spread.earliest_post.posted_at[:10]})")
        print(f"  \"{spread.earliest_post.text[:150]}\"")

    if spread.most_engaged_post:
        print(f"\nMost engaged: @{spread.most_engaged_post.author} "
              f"({spread.most_engaged_post.likes} likes, {spread.most_engaged_post.reposts} reposts)")
        print(f"  \"{spread.most_engaged_post.text[:150]}\"")

    if spread.timeline_summary:
        print(f"\nTimeline:\n{spread.timeline_summary}")

    # Run spread analysis
    print("\nAnalyzing spread pattern...")
    analyzer = SpreadAnalyzer()
    spread = await analyzer.analyze(spread)
    print(f"\nSpread type: {spread.spread_type.value}")
    print(f"Analysis: {spread.analysis}")

    await bsky.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
