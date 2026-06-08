#!/usr/bin/env python3
"""
Tributary Batch Processor
==========================
Discovers trending topics, runs full Tributary analysis on each,
and stores results for serving through a web interface.

Usage:
    # Discover today's top stories and analyze them
    python batch.py

    # Analyze specific topics from a file (one per line)
    python batch.py --topics topics.txt

    # Just discover topics without analyzing
    python batch.py --discover-only

    # Discover topics for a specific region
    python batch.py --region Minneapolis --discover-only
    python batch.py --region Texas --discover-only
    python batch.py --region Europe --discover-only
    python batch.py --region "United Kingdom" --discover-only

    # Use Google web search instead of Bluesky for discovery
    python batch.py --source web --discover-only

    # Filter by topic category
    python batch.py --category politics --discover-only
    python batch.py --category technology --discover-only
    python batch.py --category health --region Europe --discover-only

    # Analyze a single topic
    python batch.py --topic "ICE killed Renee Good in Minneapolis"

    # Control how many topics to process
    python batch.py --max-topics 5

    # Set output directory
    python batch.py --output-dir ./results

Setup:
    source .venv/bin/activate
    source .env
"""

import argparse
import asyncio
import json
import os
import sys
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import SourceTracer, TraceCache, ContentLabel
from search_anthropic import AnthropicSearchAdapter


# ---------------------------------------------------------------------------
# Topic discovery
# ---------------------------------------------------------------------------

def _parse_json_safe(text: str):
    """Robustly parse JSON from LLM output."""
    cleaned = text.strip()

    # Strip markdown fences
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find JSON array or object boundary
    import re
    for start_char, end_char in [('[', ']'), ('{', '}')]:
        start = cleaned.find(start_char)
        if start >= 0:
            depth = 0
            for i in range(start, len(cleaned)):
                if cleaned[i] == start_char:
                    depth += 1
                elif cleaned[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i + 1])
                        except json.JSONDecodeError:
                            break

    return None

# Available topic categories
CATEGORIES = {
    "politics": "Government, elections, legislation, political parties, diplomacy",
    "economy": "Markets, trade, employment, inflation, central banks, fiscal policy",
    "technology": "AI, software, hardware, startups, digital policy, cybersecurity",
    "world": "International conflicts, diplomacy, global events, humanitarian crises",
    "science": "Research breakthroughs, climate, health, space, environment",
    "social": "Culture, civil rights, education, immigration, policing, inequality",
    "legal": "Court rulings, trials, constitutional law, regulatory decisions",
    "health": "Public health, pandemics, healthcare policy, drug approvals",
    "business": "Corporate news, mergers, labor disputes, industry shifts",
    "media": "Journalism, social media platforms, misinformation, censorship",
}

ALL_CATEGORIES = "|".join(CATEGORIES.keys())


async def discover_topics(
    num_topics: int = 10,
    region: str = None,
    source: str = "bluesky",
    category: str = None,
) -> list[dict]:
    """
    Router: discovers topics from the specified source.
    
    Args:
        source: "bluesky" (default, engagement-driven) or "web" (Google search)
        region: Optional region focus
        category: Optional category filter (e.g., "politics", "technology")
    """
    if source == "web":
        return await discover_topics_web(num_topics=num_topics, region=region, category=category)
    else:
        return await discover_topics_bluesky(num_topics=num_topics, region=region, category=category)


async def discover_topics_bluesky(
    num_topics: int = 10,
    region: str = None,
    category: str = None,
) -> list[dict]:
    """
    Discover trending topics from Bluesky's most engaged posts.
    This surfaces what people are actually talking about and debating,
    not just what news desks decided to cover.
    """
    import anthropic
    from social_search import BlueskySearch

    client = anthropic.AsyncAnthropic()
    bsky = BlueskySearch()

    # Check credentials before trying 15 searches
    if not bsky.handle or not bsky.app_password:
        print("[discover] Bluesky credentials not set (BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)")
        print("[discover] Falling back to web search discovery...")
        return await discover_topics_web(num_topics=num_topics, region=region, category=category)

    await bsky.login()

    if not bsky.access_token:
        print("[discover] Bluesky login failed, falling back to web search discovery...")
        return await discover_topics_web(num_topics=num_topics, region=region, category=category)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    region_label = region or "global"
    category_label = category or "all topics"
    print(f"[discover] Finding trending Bluesky topics ({today}, region: {region_label}, category: {category_label})...")

    # Build search queries based on region and category
    if category:
        # Category-specific search terms
        category_terms = {
            "politics": ["politics", "government", "election", "legislation", "vote", "parliament"],
            "economy": ["economy", "inflation", "markets", "trade", "unemployment", "GDP"],
            "technology": ["AI", "tech", "cybersecurity", "startup", "software", "algorithm"],
            "world": ["conflict", "international", "diplomacy", "war", "UN", "sanctions"],
            "science": ["study finds", "research", "climate", "discovery", "NASA", "scientists"],
            "social": ["civil rights", "inequality", "immigration", "education", "protest", "policing"],
            "legal": ["court ruling", "Supreme Court", "trial", "verdict", "constitutional", "lawsuit"],
            "health": ["health", "pandemic", "vaccine", "FDA", "WHO", "public health"],
            "business": ["CEO", "merger", "layoffs", "earnings", "IPO", "labor dispute"],
            "media": ["misinformation", "censorship", "journalism", "social media", "platform"],
        }
        terms = category_terms.get(category, [category])
        prefix = f"{region} " if region and region.lower() not in ("national", "us", "international") else ""
        search_queries = [f"{prefix}{term}" for term in terms]
    elif region and region.lower() not in ("national", "us", "international"):
        search_queries = [
            f"{region}",
            f"{region} news",
            f"{region} breaking",
            f"{region} controversy",
            f"{region} announced",
        ]
    else:
        # Topic-neutral reaction/debate language
        search_queries = [
            "breaking",
            "just announced",
            "confirmed",
            "this is huge",
            "report",
            "according to",
            "investigation",
            "officials say",
            "thread",
            "wrong about",
            "actually",
            "important context",
            "today",
            "happening now",
        ]

    # Pull top-engaged posts for each query
    all_posts = []
    seen_urls = set()

    for query in search_queries:
        try:
            posts = await bsky.search_posts(
                query=query,
                sort="top",
                since=since,
                limit=25,
            )
            for p in posts:
                if p.url not in seen_urls and (p.likes + p.reposts) >= 10:
                    seen_urls.add(p.url)
                    all_posts.append(p)
        except Exception as e:
            print(f"  [bluesky] Search error for '{query}': {e}")

    await bsky.close()

    if not all_posts:
        print("[discover] No trending posts found on Bluesky")
        # Fall back to web discovery
        print("[discover] Falling back to web search discovery...")
        return await discover_topics_web(num_topics=num_topics, region=region, category=category)

    # Sort by engagement and take top posts
    all_posts.sort(key=lambda p: p.likes + p.reposts, reverse=True)
    top_posts = all_posts[:80]

    print(f"  Found {len(all_posts)} engaged posts, using top {len(top_posts)}")

    # Use Haiku to extract distinct topics from the posts
    post_summaries = [
        {
            "author": p.author,
            "text": p.text[:200],
            "likes": p.likes,
            "reposts": p.reposts,
            "posted_at": p.posted_at[:10] if p.posted_at else "",
        }
        for p in top_posts
    ]

    region_instruction = ""
    if region and region.lower() not in ("national", "us", "international"):
        region_instruction = (
            f"Focus on topics relevant to {region}. Include local stories "
            f"and national stories with {region} relevance."
        )
    else:
        region_instruction = (
            "Focus on nationally or internationally significant topics. "
            "Skip purely local stories unless they have national implications."
        )

    category_instruction = ""
    if category:
        cat_desc = CATEGORIES.get(category, category)
        category_instruction = (
            f"\nCATEGORY FILTER: Only include topics related to '{category}' "
            f"({cat_desc}). Skip topics from other categories entirely."
        )
    
    categories_for_prompt = category if category else ALL_CATEGORIES

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": (
                f"Today's date is {today}.\n\n"
                f"These are the most engaged posts on Bluesky right now:\n\n"
                f"{json.dumps(post_summaries, indent=2)}\n\n"
                f"Extract the {num_topics + 5} most important DISTINCT topics that people "
                f"are debating. These posts represent what's actually generating "
                f"conversation and disagreement right now.\n\n"
                f"SIGNIFICANCE: {region_instruction}{category_instruction}\n\n"
                f"RULES:\n"
                f"- Each topic MUST describe a SPECIFIC EVENT or claim (not a vague theme)\n"
                f"- Merge posts about the same story into one topic\n"
                f"- Prioritize topics where the posts show DISAGREEMENT or strong reactions\n"
                f"- Higher engagement (likes + reposts) = higher priority\n"
                f"- Write each topic as a concrete factual claim with names/dates/numbers\n"
                f"- BAD: 'People are discussing politics' (vague)\n"
                f"- GOOD: 'Senate passed 52-48 vote on immigration bill' (specific)\n\n"
                f"Respond with ONLY a JSON array:\n"
                f'[{{"topic": "specific factual claim", '
                f'"category": "{categories_for_prompt}", '
                f'"engagement": total_likes_and_reposts_across_related_posts, '
                f'"source_url": ""}}]'
            ),
        }],
    )

    raw = response.content[0].text.strip()
    topics = _parse_json_safe(raw)

    if topics is None:
        print(f"[discover] Failed to parse Bluesky topics. Raw: {raw[:300]}")
        return []

    if not isinstance(topics, list):
        topics = [topics]

    # Filter vague topics
    concrete_topics = []
    for t in topics:
        topic_text = t.get("topic", "")
        has_specifics = (
            any(c.isupper() for c in topic_text[1:])
            or any(c.isdigit() for c in topic_text)
        )
        if has_specifics and len(topic_text) > 20:
            concrete_topics.append(t)
        else:
            print(f"  [discover] Filtered vague topic: {topic_text[:60]}")

    # Sort by engagement if available
    concrete_topics.sort(
        key=lambda t: t.get("engagement", 0), reverse=True
    )

    print(f"[discover] Extracted {len(concrete_topics)} trending topics from Bluesky")
    return concrete_topics[:num_topics]


async def discover_topics_web(
    num_topics: int = 10,
    categories: list[str] = None,
    region: str = None,
    category: str = None,
) -> list[dict]:
    """
    Discover today's most newsworthy and controversial topics.
    
    Args:
        region: Optional region focus. Examples:
            "national" or None — global / international (default)
            "Minneapolis" — local to Minneapolis
            "Texas" — state-level
            "Europe" — continental
            "UK" — country-level
    
    Returns list of:
      {"topic": "headline/claim", "category": "politics|economy|...", "source_url": "..."}
    """
    import anthropic

    client = anthropic.AsyncAnthropic()
    search = AnthropicSearchAdapter(client=client)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    region_label = region or "national/international"
    print(f"[discover] Searching for today's top stories ({today}, region: {region_label})...")

    if region and region.lower() not in ("national", "us", "international"):
        # Region-specific queries
        discovery_queries = [
            f"{region} news today",
            f"{region} breaking news today",
            f"{region} controversial news today",
            f"{region} politics today",
            f"{region} debate controversy today",
            f"{region} policy decision today",
            f"{region} protest today",
            f"{region} court ruling today",
            f"top stories {region} today",
            f"{region} local news controversy",
        ]
        significance_instruction = (
            f"Focus on stories from or significantly affecting {region}. "
            f"Include both local stories specific to {region} AND national/international "
            f"stories that have particular relevance to {region}."
        )
    else:
        # Global/international queries — balanced across regions
        discovery_queries = [
            "AP News top stories today",
            "Reuters top news today",
            "BBC News top stories today",
            "Al Jazeera top news today",
            "controversial news today",
            "political controversy today 2026",
            "divisive policy debate today",
            "international dispute today",
            "election results today",
            "court ruling today 2026",
            "economic data released today",
            "world news conflict today",
            "international crisis today 2026",
            "trending debate today",
        ]
        significance_instruction = (
            "Include topics from around the world, not just the US. "
            "Prioritize internationally significant events. "
            "Reject purely local stories unless they have broader implications."
        )

    # Add category-specific queries if specified
    if category:
        cat_desc = CATEGORIES.get(category, category)
        prefix = f"{region} " if region and region.lower() not in ("national", "us", "international") else ""
        category_queries = [
            f"{prefix}{category} news today",
            f"{prefix}{category} controversy today",
            f"{prefix}{category} debate today",
            f"{prefix}{cat_desc.split(',')[0].strip()} today",
        ]
        discovery_queries = category_queries + discovery_queries[:6]
        significance_instruction += (
            f" CATEGORY FILTER: Only include topics related to '{category}' "
            f"({cat_desc}). Skip topics from other categories."
        )

    categories_for_prompt = category if category else ALL_CATEGORIES

    all_snippets = []
    for query in discovery_queries:
        try:
            results = await search.search(query, num_results=5)
            for r in results:
                all_snippets.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "url": r.get("url", ""),
                })
        except Exception as e:
            print(f"  [discover] Error: {e}")

    if not all_snippets:
        print("[discover] No results found")
        return []

    print(f"  Found {len(all_snippets)} news items from {len(discovery_queries)} searches")

    # Use Haiku to extract specific, concrete events
    snippets_text = json.dumps(all_snippets[:50], indent=2)

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": (
                f"Today's date is {today}.\n\n"
                f"Here are news items from today's searches:\n\n{snippets_text}\n\n"
                f"Extract the {num_topics + 5} most important SPECIFIC news events from today "
                f"or the past 48 hours.\n\n"
                f"SIGNIFICANCE: {significance_instruction}\n\n"
                f"REQUIREMENTS:\n"
                f"- Each topic MUST describe a SPECIFIC EVENT with names, numbers, and dates\n"
                f"- Prioritize topics where DIFFERENT GROUPS DISAGREE — this is critical. "
                f"  The best topics are ones where different political factions, countries, "
                f"  or institutions interpret the same event differently\n"
                f"- Policy decisions, court rulings, legislative actions, economic data, "
                f"  international conflicts, and cultural flashpoints are ideal\n"
                f"- Include stories from around the world, not just one country\n\n"
                f"QUALITY FILTER:\n"
                f"- BAD: 'A teenager was killed in a local park' (local crime, no broader debate)\n"
                f"- BAD: 'City announced road safety plan' (local infrastructure)\n"
                f"- GOOD: 'EU imposed tariffs on Chinese electric vehicles' (trade policy, competing views)\n"
                f"- GOOD: 'UN General Assembly voted on ceasefire resolution' (international, contested)\n"
                f"- GOOD: 'India's central bank cut interest rates to 5.75%' (economic, debated impact)\n"
                f"- GOOD: 'UK parliament passed online safety amendments' (policy, free speech debate)\n\n"
                f"For each topic, write a concrete factual claim about WHAT HAPPENED.\n\n"
                f"Respond with ONLY a JSON array:\n"
                f'[{{"topic": "specific factual claim with names/numbers/places", '
                f'"category": "{categories_for_prompt}", '
                f'"source_url": "most relevant url"}}]'
            ),
        }],
    )

    raw = response.content[0].text.strip()
    topics = _parse_json_safe(raw)

    if topics is None:
        print(f"[discover] Failed to parse topics. Raw response:")
        print(f"  {raw[:500]}")

        # Retry with stricter prompt
        print("[discover] Retrying with stricter instructions...")
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": (
                    f"Today's date is {today}.\n\n"
                    f"News items:\n{snippets_text[:3000]}\n\n"
                    f"Extract {num_topics + 5} specific news events. "
                    f"Return ONLY a valid JSON array, nothing else. "
                    f"No explanation, no markdown, no backticks.\n"
                    f'[{{"topic": "what happened", "category": "politics", "source_url": ""}}]'
                ),
            }],
        )
        raw = response.content[0].text.strip()
        topics = _parse_json_safe(raw)

        if topics is None:
            print(f"[discover] Retry also failed. Raw: {raw[:300]}")
            return []

    if not isinstance(topics, list):
        topics = [topics]

    # Filter out any remaining vague topics
    concrete_topics = []
    for t in topics:
        topic_text = t.get("topic", "")
        has_specifics = (
            any(c.isupper() for c in topic_text[1:])
            or any(c.isdigit() for c in topic_text)
        )
        if has_specifics and len(topic_text) > 20:
            concrete_topics.append(t)
        else:
            print(f"  [discover] Filtered vague topic: {topic_text[:60]}")

    print(f"[discover] Extracted {len(concrete_topics)} concrete topics")
    return concrete_topics[:num_topics]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class ResultStore:
    """
    Stores analysis results as JSON files organized by date.
    
    Structure:
      results/
        2026-04-21/
          topic_abc123.json
          topic_def456.json
          index.json           <- list of all topics for this date
        latest.json            <- symlink/copy of most recent index
    """

    def __init__(self, base_dir: str = "./results"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _date_dir(self, date: str = None) -> Path:
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d = self.base_dir / date
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _topic_id(self, topic: str) -> str:
        return hashlib.sha256(topic.lower().strip().encode()).hexdigest()[:10]

    def save_result(self, topic: str, result: dict, date: str = None):
        """Save a single topic's analysis."""
        topic_id = self._topic_id(topic)
        date_dir = self._date_dir(date)

        # Save the full result
        filepath = date_dir / f"topic_{topic_id}.json"
        result["_meta"] = {
            "topic_id": topic_id,
            "topic": topic,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        with open(filepath, "w") as f:
            json.dump(result, f, indent=2, default=str)

        # Update the daily index
        self._update_index(date)

        print(f"  [store] Saved: {filepath}")
        return filepath

    def _update_index(self, date: str = None):
        """Rebuild the index.json for a given date."""
        date_dir = self._date_dir(date)
        topics = []

        for f in sorted(date_dir.glob("topic_*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                meta = data.get("_meta", {})
                topics.append({
                    "topic_id": meta.get("topic_id", ""),
                    "topic": meta.get("topic", ""),
                    "category": data.get("category", ""),
                    "analyzed_at": meta.get("analyzed_at", ""),
                    "file": f.name,
                    "num_views": len(data.get("views", data.get("framings", []))),
                    "has_social": "social_by_view" in data,
                    "primary_source": data.get("primary_source", ""),
                })
            except (json.JSONDecodeError, KeyError):
                continue

        index_path = date_dir / "index.json"
        with open(index_path, "w") as f:
            json.dump({
                "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "topics": topics,
                "count": len(topics),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

        # Also save as latest
        latest_path = self.base_dir / "latest.json"
        with open(latest_path, "w") as f:
            json.dump({
                "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "topics": topics,
                "count": len(topics),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

    def get_result(self, topic_id: str, date: str = None) -> dict | None:
        """Load a single topic's analysis."""
        date_dir = self._date_dir(date)
        filepath = date_dir / f"topic_{topic_id}.json"
        if filepath.exists():
            with open(filepath) as f:
                return json.load(f)
        return None

    def get_index(self, date: str = None) -> dict:
        """Get the index for a given date."""
        date_dir = self._date_dir(date)
        index_path = date_dir / "index.json"
        if index_path.exists():
            with open(index_path) as f:
                return json.load(f)
        return {"date": date, "topics": [], "count": 0}

    def topic_exists(self, topic: str, date: str = None) -> bool:
        """Check if a topic has already been analyzed today."""
        topic_id = self._topic_id(topic)
        date_dir = self._date_dir(date)
        return (date_dir / f"topic_{topic_id}.json").exists()


# ---------------------------------------------------------------------------
# Batch analyzer
# ---------------------------------------------------------------------------

async def analyze_topic(
    topic: str,
    store: ResultStore,
    include_social: bool = False,
    days_back: int = 30,
    skip_existing: bool = True,
) -> dict | None:
    """
    Run full Tributary analysis on a single topic.
    Returns the result dict, or None if skipped/failed.
    """
    if skip_existing and store.topic_exists(topic):
        print(f"\n[skip] Already analyzed: {topic[:60]}")
        return None

    print(f"\n{'='*70}")
    print(f"ANALYZING: {topic}")
    print(f"{'='*70}")

    search = AnthropicSearchAdapter()
    tracer = SourceTracer(search_adapter=search)

    try:
        # Step 1: Generate a neutral phrasing for unbiased analysis
        print("\n[step 1] Generating neutral phrasing...")
        import anthropic as _anthropic
        _client = _anthropic.AsyncAnthropic()

        neutral_response = await _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": (
                    f"Rephrase this news topic into a maximally neutral factual statement "
                    f"suitable for unbiased analysis.\n\n"
                    f"Original: \"{topic}\"\n\n"
                    f"RULES:\n"
                    f"- State ONLY what all sides would agree happened\n"
                    f"- Use active voice (don't obscure who did what)\n"
                    f"- Include specific names, dates, places, and numbers\n"
                    f"- Remove ALL evaluative language (no 'brutal', 'tragic', "
                    f"'controversial', 'justified', 'unprecedented')\n"
                    f"- Don't add context or interpretation — just the core event\n\n"
                    f"Examples:\n"
                    f"  Input: 'Trump's radical immigration crackdown causes chaos'\n"
                    f"  Output: 'The Trump administration expanded immigration enforcement "
                    f"operations in multiple US cities in January 2026'\n\n"
                    f"  Input: 'Brave protestors stand against ICE tyranny'\n"
                    f"  Output: 'Civilians protested ICE enforcement operations in "
                    f"Minneapolis in January 2026'\n\n"
                    f"Respond with ONLY the neutral rephrasing, nothing else."
                ),
            }],
        )
        neutral_topic = neutral_response.content[0].text.strip().strip('"')
        print(f"  Original:  {topic}")
        print(f"  Neutral:   {neutral_topic}")

        # Step 2: Classify the claim
        print("\n[step 2] Classifying claim...")
        claims = await tracer.extract_claims(neutral_topic)
        if claims:
            claim = claims[0]
            print(f"  Classified as: {claim.label.value.upper()}")
        else:
            from agent import Claim
            claim = Claim(text=neutral_topic, label=ContentLabel.FACT, context="")

        # Step 3: Trace to primary source
        print("\n[step 3] Tracing to primary source...")
        chain = await tracer.trace_claim(claim)
        primary_url = chain.primary_source.url if chain.primary_source else ""

        if chain.primary_source:
            print(f"  Found: {chain.primary_source.title}")

        # Step 4: Find competing perspectives (using neutral phrasing)
        print("\n[step 4] Finding competing perspectives...")
        perspectives = await tracer.find_competing_narratives(
            fact_text=neutral_topic,
            primary_source_url=primary_url,
        )

        # Step 5: Social spread (optional — uses original topic for broader search)
        social_data = None
        if include_social:
            print("\n[step 5] Searching social media...")
            try:
                from social_search import BlueskySearch, SpreadAnalyzer

                bsky = BlueskySearch()
                await bsky.login()

                spread = await bsky.search_narrative(topic, days_back=days_back, limit=100)

                if spread.posts:
                    analyzer = SpreadAnalyzer()
                    spread = await analyzer.analyze(spread)
                    social_data = spread.to_dict()
                    print(f"  Found {spread.total_posts_found} posts ({spread.spread_type.value})")

                    # Classify posts by view if we have views
                    views = perspectives.get("views", [])
                    if views and spread.posts:
                        social_data["by_view"] = await _classify_posts_by_view(
                            topic, views, spread.posts
                        )

                await bsky.close()
            except Exception as e:
                print(f"  [social] Error: {e}")

        # Step 6: Mutation tracking — how did the claim change across sources?
        mutation_data = None
        views = perspectives.get("views", [])
        all_sources = []
        for view_item in views:
            for s in view_item.get("sources", []):
                all_sources.append(s)
        if len(all_sources) >= 2:
            print("\n[step 6] Tracking narrative mutations...")
            mutation_data = await tracer.analyze_mutations(
                claim_text=neutral_topic,
                sources=all_sources[:10],  # Cap to control cost
            )

        # Step 7: Missing info analysis — what does each view omit?
        omission_data = []
        if views and len(views) >= 2:
            print("\n[step 7] Analyzing omissions per view...")
            for view_item in views[:5]:  # Analyze top 5 views
                sources_in_view = view_item.get("sources", [])
                if not sources_in_view:
                    continue
                # Use the key claim and source excerpts as the "content"
                view_content = view_item.get("key_claim", "")
                for s in sources_in_view:
                    excerpt = s.get("excerpt", s.get("framing_headline", ""))
                    if excerpt:
                        view_content += f"\n{excerpt}"

                if view_content.strip():
                    view_name = view_item.get("view_name", "Unknown")
                    print(f"  Analyzing omissions in: {view_name}")
                    omission = await tracer.analyze_missing_info(
                        content_text=view_content,
                        content_url=view_name,
                        views=[l for l in views if l.get("view_name") != view_name],
                    )
                    omission["view_name"] = view_name
                    omission_data.append(omission)

        # Step 8: Shared foundation analysis — what do all views agree on?
        shared_data = None
        if views and len(views) >= 2:
            print("\n[step 8] Identifying shared foundation across views...")

            # Fetch the primary source content for verification
            primary_content = ""
            if primary_url:
                try:
                    primary_content = await search.fetch_page(primary_url)
                except Exception as e:
                    print(f"  [shared] Could not fetch primary source: {e}")

            shared_data = await tracer.analyze_shared_foundation(
                event_text=neutral_topic,
                views=views,
                primary_source_url=primary_url,
                primary_source_content=primary_content if not str(primary_content).startswith("[Error") else "",
            )

        # Build the complete result
        trace_sources = []
        for s in chain.sources:
            trace_sources.append({
                "url": s.url,
                "title": s.title,
                "tier": s.tier.value,
                "excerpt": s.relevant_excerpt,
                "referenced_url": s.referenced_url,
                "flow_type": s.flow_type,
                "flow_evidence": s.flow_evidence,
                "published_date": s.published_date,
                "author": s.author,
                "confidence": s.confidence,
            })

        result = {
            "topic": topic,
            "neutral_topic": neutral_topic,
            "label": claim.label.value,
            "primary_source": {
                "url": chain.primary_source.url if chain.primary_source else None,
                "title": chain.primary_source.title if chain.primary_source else None,
                "tier": chain.primary_source.tier.value if chain.primary_source else None,
            },
            "trace_sources": trace_sources,
            "trace_summary": chain.summary,
            "trace_confidence": chain.confidence,
            "views": perspectives.get("views", []),
            "gap_analysis": perspectives.get("gap_analysis", ""),
            "raw_framings_count": perspectives.get("raw_framings_count", 0),
            "total_sources_checked": perspectives.get("total_sources_checked", 0),
            "mutations": mutation_data,
            "omissions_by_view": omission_data,
            "shared_foundation": shared_data,
            "social": social_data,
            "cost_usd": tracer.usage["cost_usd"],
            "tokens": {
                "input": tracer.usage["input"],
                "output": tracer.usage["output"],
            },
        }

        # Save in TributaryEvent format
        from models import TributaryEvent
        event = TributaryEvent.from_batch_result(result)
        store.save_result(topic, event.to_dict())

        print(f"\n[done] {len(perspectives.get('views', []))} views, "
              f"{len(event.flows)} flows, "
              f"${tracer.usage['cost_usd']:.3f}"
              f"\n  Topic:   {topic}"
              f"\n  Neutral: {neutral_topic}"
              f"\n  Event ID: {event.event_id}")
        if mutation_data and mutation_data.get("overall"):
            severity = mutation_data["overall"].get("mutation_severity", "?")
            pattern = mutation_data["overall"].get("mutation_pattern", "?")
            print(f"  Mutation: {severity} ({pattern})")
        if omission_data:
            avg_completeness = sum(
                o.get("completeness_score", 0) for o in omission_data
            ) / len(omission_data)
            print(f"  Avg completeness per view: {avg_completeness:.0%}")
        if shared_data:
            verified = len(shared_data.get("verified_facts", []))
            unverified = len(shared_data.get("unverified_facts", []))
            disagreements = len(shared_data.get("points_of_disagreement", []))
            print(f"  Shared ground: {verified} verified facts, "
                  f"{unverified} unverified, {disagreements} points of disagreement")

        return result

    except Exception as e:
        print(f"\n[error] Failed to analyze '{topic[:50]}': {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        await search.close()


async def _classify_posts_by_view(topic, views, posts):
    """Classify social posts by which view they align with."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    view_descriptions = [
        {"id": i, "name": l.get("view_name", ""), "question": l.get("question", "")}
        for i, l in enumerate(views)
    ]

    classified = {l.get("view_name", f"View {i}"): [] for i, l in enumerate(views)}
    posts_to_classify = posts[:60]

    batch_size = 15
    for batch_start in range(0, len(posts_to_classify), batch_size):
        batch = posts_to_classify[batch_start:batch_start + batch_size]
        post_summaries = [
            {"idx": batch_start + j, "author": p.author, "text": p.text[:200]}
            for j, p in enumerate(batch)
        ]

        try:
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": (
                    f"Event: \"{topic}\"\n\n"
                    f"Views:\n{json.dumps(view_descriptions)}\n\n"
                    f"Posts:\n{json.dumps(post_summaries)}\n\n"
                    f"Assign each post to a view. Respond with ONLY JSON:\n"
                    f'[{{"idx": 0, "view_id": 2}}, ...]'
                )}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            assignments = json.loads(raw.strip())

            for a in assignments:
                idx = a.get("idx", -1)
                view_id = a.get("view_id", -1)
                if 0 <= idx < len(posts_to_classify) and 0 <= view_id < len(views):
                    p = posts_to_classify[idx]
                    view_name = views[view_id].get("view_name", f"View {view_id}")
                    classified[view_name].append({
                        "author": p.author,
                        "text": p.text[:200],
                        "likes": p.likes,
                        "reposts": p.reposts,
                        "url": p.url,
                        "posted_at": p.posted_at,
                    })
        except Exception:
            continue

    return classified


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_batch(args):
    """Main batch processing loop."""
    store = ResultStore(args.output_dir)

    # Determine topics to analyze
    if args.topic:
        # Single topic
        topics = [{"topic": args.topic, "category": "manual"}]

    elif args.topics:
        # Topics from file
        with open(args.topics) as f:
            topics = [
                {"topic": line.strip(), "category": "manual"}
                for line in f if line.strip()
            ]

    else:
        # Auto-discover
        topics = await discover_topics(
            num_topics=args.max_topics, region=args.region,
            source=args.source, category=args.category,
        )

    if not topics:
        print("No topics to analyze.")
        return

    # Display topics
    print(f"\n{'='*70}")
    print(f"TRIBUTARY BATCH PROCESSOR")
    print(f"{'='*70}")
    print(f"  Topics to analyze: {len(topics)}")
    print(f"  Output directory: {args.output_dir}")
    print(f"  Social search: {'yes' if args.social else 'no'}")
    print(f"  Skip existing: {'yes' if not args.force else 'no'}")

    if args.discover_only:
        # Generate neutral phrasings for all discovered topics
        import anthropic as _anthropic
        _client = _anthropic.AsyncAnthropic()

        print(f"\n  Generating neutral phrasings...\n")

        topic_list = json.dumps([t["topic"] for t in topics])

        try:
            resp = await _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Rephrase each of these news topics into maximally neutral "
                        f"factual statements.\n\n"
                        f"Topics: {topic_list}\n\n"
                        f"RULES:\n"
                        f"- State ONLY what all sides would agree happened\n"
                        f"- Use active voice\n"
                        f"- Include specific names, dates, places, numbers\n"
                        f"- Remove ALL evaluative language\n"
                        f"- Keep the same order\n\n"
                        f"Respond with ONLY a JSON array of neutral strings, "
                        f"same length as input. No markdown, no explanation."
                    ),
                }],
            )
            neutral_raw = resp.content[0].text.strip()
            neutral_topics = _parse_json_safe(neutral_raw)
            if not neutral_topics or not isinstance(neutral_topics, list):
                neutral_topics = [None] * len(topics)
        except Exception:
            neutral_topics = [None] * len(topics)

        print(f"  Topics discovered:\n")
        for i, t in enumerate(topics):
            cat = t.get("category", "")
            original = t["topic"]
            neutral = neutral_topics[i] if i < len(neutral_topics) else None

            print(f"    {i+1}. [{cat}] {original}")
            if neutral and neutral != original:
                print(f"       → Neutral: {neutral}")
            print()
        return

    print(f"\n  Topics:")
    for i, t in enumerate(topics, 1):
        cat = t.get("category", "")
        print(f"    {i}. [{cat}] {t['topic']}")

    # Process each topic
    total_cost = 0
    results = []

    for i, topic_info in enumerate(topics, 1):
        topic_text = topic_info["topic"]
        print(f"\n[{i}/{len(topics)}] ", end="")

        result = await analyze_topic(
            topic=topic_text,
            store=store,
            include_social=args.social,
            days_back=args.days_back,
            skip_existing=not args.force,
        )

        if result:
            total_cost += result.get("cost_usd", result.get("ai_cost_usd", 0))
            results.append(result)

    # Final summary
    print(f"\n{'='*70}")
    print(f"BATCH COMPLETE")
    print(f"{'='*70}")
    print(f"  Topics analyzed: {len(results)}/{len(topics)}")

    for i, r in enumerate(results, 1):
        neutral = r.get("neutral_statement", r.get("neutral_topic", ""))
        original = r.get("topic", "")
        views = len(r.get("views", []))
        flows = len(r.get("flows", []))
        cost = r.get("ai_cost_usd", r.get("cost_usd", 0))
        stats = r.get("stats", {})
        print(f"\n  {i}. {original}")
        if neutral and neutral != original:
            print(f"     → Neutral: {neutral}")
        print(f"     {views} views, {flows} flows, ${cost:.2f}")
        if stats:
            print(f"     {stats.get('sources', 0)} sources, "
                  f"{stats.get('mutations', 0)} mutations, "
                  f"{stats.get('omissions', 0)} omissions")

    print(f"\n  Total cost: ${total_cost:.2f}")
    print(f"  Results saved to: {args.output_dir}")

    index = store.get_index()
    print(f"  Index: {index['count']} topics in today's index")
    print(f"  Browse: {args.output_dir}/latest.json")


def main():
    parser = argparse.ArgumentParser(
        description="Tributary Batch Processor — analyze trending topics"
    )
    parser.add_argument(
        "--topic", type=str,
        help="Analyze a single topic"
    )
    parser.add_argument(
        "--topics", type=str,
        help="File with topics to analyze (one per line)"
    )
    parser.add_argument(
        "--max-topics", type=int, default=10,
        help="Max topics to auto-discover (default: 10)"
    )
    parser.add_argument(
        "--region", type=str, default=None,
        help="Region focus for topic discovery (e.g., 'Minneapolis', 'Texas', 'Europe'). Default: national/international"
    )
    parser.add_argument(
        "--source", type=str, default="bluesky", choices=["bluesky", "web"],
        help="Discovery source: 'bluesky' (engagement-driven, default) or 'web' (Google search)"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        choices=list(CATEGORIES.keys()),
        help="Filter topics by category (e.g., 'politics', 'technology', 'health')"
    )
    parser.add_argument(
        "--discover-only", action="store_true",
        help="Just discover topics, don't analyze"
    )
    parser.add_argument(
        "--social", action="store_true",
        help="Include Bluesky social search"
    )
    parser.add_argument(
        "--days-back", type=int, default=30,
        help="Social search lookback (default: 30)"
    )
    parser.add_argument(
        "--output-dir", type=str, default="./results",
        help="Directory for results (default: ./results)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-analyze topics even if already done today"
    )

    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    asyncio.run(run_batch(args))


if __name__ == "__main__":
    main()
