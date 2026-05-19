#!/usr/bin/env python3
"""
Tributary Source Tracer — Local Demo
=====================================

Run this to trace claims in sample content or your own text.

Setup:
    pip install anthropic httpx youtube-transcript-api atproto
    export ANTHROPIC_API_KEY="sk-ant-..."

    # For --social flag (Bluesky spread search):
    export BLUESKY_HANDLE="yourhandle.bsky.social"
    export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"

Usage:
    # Trace claims in the built-in sample article
    python demo.py

    # Trace a single claim
    python demo.py --claim "The US economy added 256,000 jobs in December 2024"

    # Trace claims from a file
    python demo.py --file article.txt

    # Trace from any URL (auto-detects platform)
    python demo.py --url https://www.nytimes.com/some-article
    python demo.py --url https://bsky.app/profile/user.bsky.social/post/abc123
    python demo.py --url https://www.youtube.com/watch?v=dQw4w9WgXcQ
    python demo.py --url https://www.tiktok.com/@user/video/123456

    # Bluesky: trace recent posts from a user
    python demo.py --bluesky-feed user.bsky.social --limit 10

    # Just extract and classify claims (no tracing, no search cost)
    python demo.py --extract-only

    # Full trace with social media spread (requires Bluesky auth)
    python demo.py --claim "The economy is rigged" --social --verbose --json full_trace.json

    # Same data, different stories — find competing framings
    python demo.py --claim "The US added 256,000 jobs in December" --perspectives --verbose
    python demo.py --claim "The economy is rigged" --perspectives --verbose --json perspectives.json

    # Social search with longer lookback
    python demo.py --claim "The border is wide open" --social --days-back 90

    # Verbose mode: see every LLM call
    python demo.py --verbose
"""

import argparse
import asyncio
import json
import os
import sys

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import SourceTracer, TraceCache, Claim, ContentLabel
from search_anthropic import AnthropicSearchAdapter


SAMPLE_ARTICLE = """
The Bureau of Labor Statistics reported that the US economy added 256,000 
jobs in December 2024, significantly exceeding economists' expectations of 
155,000. The unemployment rate fell to 4.1%, down from 4.2% in November.

Meanwhile, a new study from MIT found that AI-assisted workers were 35% 
more productive than those without AI tools, though the researchers noted 
that benefits varied significantly across industries.

Critics argue that these job numbers don't tell the full story, as many 
new positions are part-time or gig work rather than traditional full-time 
employment. Some economists believe the labor market is actually weaker 
than the headline numbers suggest.

According to the Federal Reserve's latest Beige Book, most districts 
reported slight to modest growth, with consumer spending remaining 
resilient despite elevated interest rates.
"""


def print_separator(title: str = ""):
    print()
    if title:
        print(f"{'─' * 20} {title} {'─' * (49 - len(title))}")
    else:
        print("─" * 70)


async def extract_only(content: str):
    """Just extract and classify claims — no searching, minimal cost."""
    print_separator("EXTRACTING CLAIMS")
    print("Using Haiku to extract and classify claims...")
    print("(This step costs ~$0.001 per article)\n")

    search = AnthropicSearchAdapter()
    tracer = SourceTracer(search_adapter=search)

    try:
        claims = await tracer.extract_claims(content)

        label_colors = {
            "fact": "\033[92m",       # Green
            "study": "\033[94m",      # Blue
            "narrative": "\033[95m",  # Magenta
            "opinion": "\033[93m",    # Yellow
            "unverifiable": "\033[91m",  # Red
            "unproven": "\033[90m",   # Gray
        }
        reset = "\033[0m"

        for i, claim in enumerate(claims, 1):
            color = label_colors.get(claim.label.value, "")
            print(f"  {i}. {color}[{claim.label.value.upper()}]{reset} {claim.text}")
            if claim.context:
                print(f"     Context: {claim.context[:100]}...")
            print()

        traceable = [c for c in claims if c.label in (
            ContentLabel.FACT, ContentLabel.STUDY_ANALYSIS, ContentLabel.NARRATIVE
        )]
        print_separator("SUMMARY")
        print(f"  Total claims found: {len(claims)}")
        print(f"  Traceable (fact/study/narrative): {len(traceable)}")
        print(f"  ├─ Facts: {sum(1 for c in claims if c.label == ContentLabel.FACT)}")
        print(f"  ├─ Studies: {sum(1 for c in claims if c.label == ContentLabel.STUDY_ANALYSIS)}")
        print(f"  ├─ Narratives: {sum(1 for c in claims if c.label == ContentLabel.NARRATIVE)}")
        print(f"  Opinions: {sum(1 for c in claims if c.label == ContentLabel.OPINION)}")
        print(f"  Unverifiable: {sum(1 for c in claims if c.label == ContentLabel.UNVERIFIABLE)}")
        print(f"\n  Est. cost: ${tracer.usage['cost_usd']:.4f}")
        print(f"  Tokens: {tracer.usage['input']:,} in / {tracer.usage['output']:,} out")

    finally:
        await search.close()


async def trace_single_claim(
    claim_text: str, verbose: bool = False,
    social: bool = False, days_back: int = 30,
):
    """Trace a single claim through the full pipeline, optionally including social spread."""
    print_separator("TRACING CLAIM")
    print(f'  "{claim_text}"\n')

    search = AnthropicSearchAdapter()
    tracer = SourceTracer(search_adapter=search)
    spread_result = None
    mutation_result = None

    try:
        # First classify the claim to determine trace mode
        claims = await tracer.extract_claims(claim_text)
        if claims:
            claim = claims[0]  # Use the classified version
            print(f"  Classified as: {claim.label.value.upper()}")
        else:
            claim = Claim(text=claim_text, label=ContentLabel.FACT, context="")
            print(f"  Classified as: FACT (default)")

        chain = await tracer.trace_claim(claim)
        is_narrative = claim.label == ContentLabel.NARRATIVE

        # ── Web trace results ──

        if is_narrative:
            print_separator("ORIGIN (web trace)")
        else:
            print_separator("PROVENANCE (web trace)")

        print(f"  Confidence: {chain.confidence:.0%}")
        print(f"  Chain complete: {'Yes' if chain.chain_complete else 'No'}")
        print(f"  Sources found: {len(chain.sources)}")

        if chain.primary_source:
            if is_narrative:
                print(f"\n  ORIGIN POINT:")
                print(f"    Who: {chain.primary_source.title}")
                print(f"    URL: {chain.primary_source.url}")
                print(f"    Role: {chain.primary_source.tier.value}")
                if chain.primary_source.relevant_excerpt:
                    print(f"    Excerpt: {chain.primary_source.relevant_excerpt[:250]}")
            else:
                print(f"\n  PRIMARY SOURCE:")
                print(f"    URL: {chain.primary_source.url}")
                if chain.primary_source.relevant_excerpt:
                    print(f"    Excerpt: {chain.primary_source.relevant_excerpt[:250]}")

        if chain.summary:
            print(f"\n  Summary: {chain.summary}")

        if verbose and chain.sources:
            if is_narrative:
                _print_narrative_sources(chain.sources)
            else:
                _print_fact_sources(chain.sources)

        # ── Social spread (if enabled) ──

        if social:
            print_separator("CURRENT SPREAD (Bluesky)")
            from social_search import BlueskySearch, SpreadAnalyzer

            bsky = BlueskySearch()
            await bsky.login()

            spread_result = await bsky.search_narrative(
                claim_text, days_back=days_back, limit=50
            )

            if spread_result.posts:
                # Run spread analysis
                analyzer = SpreadAnalyzer()
                spread_result = await analyzer.analyze(spread_result)

                print(f"  Posts found: {spread_result.total_posts_found}")
                print(f"  Spread type: {spread_result.spread_type.value}")

                if spread_result.earliest_post:
                    ep = spread_result.earliest_post
                    print(f"\n  EARLIEST POST:")
                    print(f"    @{ep.author} ({ep.posted_at[:10] if ep.posted_at else '?'})")
                    print(f"    \"{ep.text[:150]}\"")
                    print(f"    {ep.url}")

                if spread_result.most_engaged_post:
                    mp = spread_result.most_engaged_post
                    print(f"\n  MOST ENGAGED:")
                    print(f"    @{mp.author} ({mp.likes} likes, {mp.reposts} reposts)")
                    print(f"    \"{mp.text[:150]}\"")
                    print(f"    {mp.url}")

                if spread_result.analysis:
                    print(f"\n  Pattern: {spread_result.analysis}")

                if verbose and spread_result.timeline_summary:
                    print(f"\n  TIMELINE:")
                    print(spread_result.timeline_summary)
            else:
                print("  No posts found on Bluesky for this narrative.")

            await bsky.close()

        # ── Full chain summary ──

        if social and spread_result and spread_result.posts and chain.primary_source:
            print_separator("FULL CHAIN")
            # Build the complete narrative flow
            origin = chain.primary_source.title if is_narrative else chain.primary_source.url
            amplifiers = [
                s.title for s in chain.sources
                if s.tier.value in ("amplifier", "secondary")
            ]
            critics = [
                s.title for s in chain.sources
                if s.tier.value == "critic"
            ]
            social_amplifiers = []
            if spread_result.most_engaged_post:
                social_amplifiers.append(
                    f"@{spread_result.most_engaged_post.author} "
                    f"({spread_result.most_engaged_post.likes} likes)"
                )

            print(f"  Origin: {origin}")
            if amplifiers:
                print(f"    → Media/Institutional: {', '.join(amplifiers[:3])}")
            if critics:
                print(f"    ✗ Critics: {', '.join(critics[:3])}")
            if social_amplifiers:
                print(f"    → Social amplifiers: {', '.join(social_amplifiers)}")
            print(f"    → {spread_result.total_posts_found} posts on Bluesky "
                  f"({spread_result.spread_type.value})")

        # ── Mutation tracking (if enough sources) ──

        if verbose and len(chain.sources) >= 2:
            # Convert Source objects to dicts for mutation analysis
            source_dicts = [
                {
                    "url": s.url,
                    "title": s.title,
                    "tier": s.tier.value,
                    "excerpt": s.relevant_excerpt,
                }
                for s in chain.sources
            ]
            mutation_result = await tracer.analyze_mutations(
                claim_text=claim.text,
                sources=source_dicts,
            )

            if mutation_result and mutation_result.get("overall"):
                overall = mutation_result["overall"]
                severity = overall.get("mutation_severity", "unknown")
                pattern = overall.get("mutation_pattern", "unknown")
                summary = overall.get("summary", "")

                print_separator("MUTATION TRACKING")
                print(f"  Severity: {severity}")
                print(f"  Pattern: {pattern}")
                if summary:
                    print(f"  {summary}")

                transitions = mutation_result.get("transitions", [])
                if transitions:
                    print()
                    for t in transitions:
                        from_s = t.get("from_source", "?")
                        to_s = t.get("to_source", "?")
                        print(f"  {from_s}  →  {to_s}")
                        if t.get("preserved"):
                            print(f"    Preserved: {t['preserved'][:120]}")
                        if t.get("dropped"):
                            print(f"    Dropped: {t['dropped'][:120]}")
                        if t.get("added"):
                            print(f"    Added: {t['added'][:120]}")
                        if t.get("distorted"):
                            print(f"    Distorted: {t['distorted'][:120]}")
                        print()

        # ── Cost ──

        print_separator("COST")
        print(f"  Total: ${tracer.usage['cost_usd']:.4f}")
        print(f"  Tokens: {tracer.usage['input']:,} in / {tracer.usage['output']:,} out")

        return chain, spread_result, mutation_result

    finally:
        await search.close()


def _print_narrative_sources(sources):
    """Print narrative sources grouped by role."""
    originators = [s for s in sources if s.tier.value == "originator"]
    amplifiers = [s for s in sources if s.tier.value == "amplifier"]
    critics = [s for s in sources if s.tier.value == "critic"]
    other = [s for s in sources if s.tier.value not in ("originator", "amplifier", "critic")]

    if originators:
        print(f"\n  ORIGINATORS ({len(originators)}):")
        for s in originators:
            print(f"    ★ {s.title} (confidence: {s.confidence:.0%})")
            print(f"      URL: {s.url}")
            print(f"      \"{s.relevant_excerpt[:150]}\"")

    if amplifiers:
        print(f"\n  AMPLIFIERS ({len(amplifiers)}):")
        for s in amplifiers:
            print(f"    ↗ {s.title} (confidence: {s.confidence:.0%})")
            print(f"      URL: {s.url}")
            print(f"      \"{s.relevant_excerpt[:150]}\"")

    if critics:
        print(f"\n  CRITICS ({len(critics)}):")
        for s in critics:
            print(f"    ✗ {s.title} (confidence: {s.confidence:.0%})")
            print(f"      URL: {s.url}")
            print(f"      \"{s.relevant_excerpt[:150]}\"")

    if other:
        print(f"\n  OTHER ({len(other)}):")
        for s in other:
            print(f"    ? {s.url} ({s.tier.value}, {s.confidence:.0%})")


def _print_fact_sources(sources):
    """Print fact sources as a chain."""
    print_separator("SOURCE CHAIN")
    for i, source in enumerate(sources, 1):
        print(f"\n  [{i}] {source.tier.value.upper()} (confidence: {source.confidence:.0%})")
        print(f"      URL: {source.url}")
        print(f"      Excerpt: {source.relevant_excerpt[:150]}")
        if source.cites:
            print(f"      Cites → {source.cites}")


async def find_perspectives(
    claim_text: str, verbose: bool = False,
    social: bool = False, days_back: int = 30,
):
    """
    Find competing narratives built on the same factual data point.
    First traces the claim to its primary source, then searches for
    different framings of the same data.
    """
    print_separator("SAME DATA, DIFFERENT STORIES")
    print(f'  Claim: "{claim_text}"\n')

    search = AnthropicSearchAdapter()
    tracer = SourceTracer(search_adapter=search)

    try:
        # Step 0: Neutral rephrasing for unbiased analysis
        print("  Step 0: Generating neutral phrasing...")
        import anthropic as _anthropic
        _client = _anthropic.AsyncAnthropic()
        neutral_response = await _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": (
                    f"Rephrase this into a maximally neutral factual statement. "
                    f"State ONLY what all sides agree happened. Use active voice. "
                    f"Include names, dates, places. Remove ALL evaluative language. "
                    f"Respond with ONLY the rephrasing.\n\n"
                    f"Original: \"{claim_text}\""
                ),
            }],
        )
        neutral_text = neutral_response.content[0].text.strip().strip('"')
        print(f"  Original: {claim_text}")
        print(f"  Neutral:  {neutral_text}\n")

        # Step 1: Classify and trace to find the primary source
        print("  Step 1: Finding the primary data source...")
        claims = await tracer.extract_claims(neutral_text)
        if claims:
            claim = claims[0]
        else:
            claim = Claim(text=neutral_text, label=ContentLabel.FACT, context="")

        # If it's a narrative, we need to find the underlying fact first
        if claim.label == ContentLabel.NARRATIVE:
            print(f"  This is a NARRATIVE. Searching for the underlying factual data...\n")
            chain = await tracer.trace_claim(claim)
            primary_url = chain.primary_source.url if chain.primary_source else ""
            fact_text = neutral_text
        else:
            chain = await tracer.trace_claim(claim)
            primary_url = chain.primary_source.url if chain.primary_source else ""
            fact_text = neutral_text

        if chain.primary_source:
            print(f"  Primary source: {chain.primary_source.title}")
            print(f"  URL: {primary_url}")
        else:
            print(f"  No primary source found — searching for framings anyway")

        # Step 2: Find competing narratives
        print(f"\n  Step 2: Finding different framings of the same data...\n")
        result = await tracer.find_competing_narratives(
            fact_text=fact_text,
            primary_source_url=primary_url,
        )
        result["original_topic"] = claim_text
        result["neutral_topic"] = neutral_text

        views = result.get("views", [])
        if not views:
            print("  No competing framings found.")
            return result

        raw_count = result.get("raw_framings_count", 0)

        # Step 3: Display the results
        print_separator("COMMON GROUND")
        print(f"  Underlying data: {fact_text}")
        if primary_url:
            print(f"  Primary source: {primary_url}")
        print(f"\n  {raw_count} raw framings consolidated into {len(views)} distinct views:\n")

        # Color-code by view
        colors = [
            "\033[92m",  # Green
            "\033[94m",  # Blue
            "\033[93m",  # Yellow
            "\033[95m",  # Magenta
            "\033[96m",  # Cyan
            "\033[91m",  # Red
            "\033[97m",  # White
            "\033[33m",  # Dark yellow
        ]
        reset = "\033[0m"

        for i, vw in enumerate(views):
            color = colors[i % len(colors)]
            name = vw.get("view_name", f"View {i+1}")
            question = vw.get("question", "")
            description = vw.get("description", "")
            key_claim = vw.get("key_claim", "")
            sources = vw.get("sources", [])

            print(f"  {color}╔═ VIEW {i + 1}: {name.upper()}{reset}")
            print(f"  {color}║{reset}  Question: {question}")
            if description:
                print(f"  {color}║{reset}  {description}")
            if key_claim:
                print(f"  {color}║{reset}  Key claim: \"{key_claim[:150]}\"")
            print(f"  {color}║{reset}  Sources ({len(sources)}):")

            for s in sources:
                url = s.get("url", "")
                source_type = s.get("source_type", "")
                headline = s.get("framing_headline", "")
                print(f"  {color}║{reset}    • [{source_type}] {headline[:80]}")
                print(f"  {color}║{reset}      {url}")

                if verbose:
                    emphasizes = s.get("emphasizes", "")
                    downplays = s.get("downplays", "")
                    who_benefits = s.get("who_benefits", "")

                    if emphasizes:
                        print(f"  {color}║{reset}      Emphasizes: {emphasizes[:120]}")
                    if downplays:
                        print(f"  {color}║{reset}      Downplays: {downplays[:120]}")
                    if who_benefits:
                        print(f"  {color}║{reset}      Benefits: {who_benefits[:120]}")

            print(f"  {color}╚{'═' * 60}{reset}\n")

        # Gap analysis
        gap = result.get("gap_analysis", "")
        if gap:
            print_separator("GAP ANALYSIS")
            print(f"  {gap}")

        # Summary
        print_separator("INSIGHT")
        print(f"  This event is being viewed through {len(views)} different analytical views:\n")
        for i, vw in enumerate(views):
            color = colors[i % len(colors)]
            name = vw.get("view_name", f"View {i+1}")
            question = vw.get("question", "")
            num_sources = len(vw.get("sources", []))
            print(f"    {color}■{reset} {name}: {question} ({num_sources} sources)")
        print(f"\n  Each view asks a different QUESTION about the same event.")
        print(f"  The underlying facts are shared. The disagreements are about")
        print(f"  which questions matter most and how to interpret the answers.")

        # ── Social media spread by view (if enabled) ──

        if social and views:
            print_separator("SOCIAL MEDIA BY VIEW (Bluesky)")
            from social_search import BlueskySearch
            import anthropic

            bsky = BlueskySearch()
            await bsky.login()

            # Search Bluesky for posts about this event
            social_posts = await bsky.search_narrative(
                claim_text, days_back=days_back, limit=100
            )

            if social_posts.posts:
                print(f"  Found {len(social_posts.posts)} posts. Classifying by view...\n")

                # Build view descriptions for the classifier
                view_descriptions = []
                for i, vw in enumerate(views):
                    view_descriptions.append({
                        "id": i,
                        "name": vw.get("view_name", f"View {i}"),
                        "question": vw.get("question", ""),
                        "key_claim": vw.get("key_claim", ""),
                    })

                # Classify posts in batches
                client = anthropic.AsyncAnthropic()
                classified = {i: [] for i in range(len(views))}
                classified["none"] = []

                batch_size = 15
                posts_list = social_posts.posts[:60]  # Cap to control cost

                for batch_start in range(0, len(posts_list), batch_size):
                    batch = posts_list[batch_start:batch_start + batch_size]
                    post_summaries = [
                        {"idx": batch_start + j, "author": p.author,
                         "text": p.text[:200], "likes": p.likes}
                        for j, p in enumerate(batch)
                    ]

                    classify_raw = await client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=1024,
                        messages=[{"role": "user", "content": (
                            f"Event: \"{claim_text}\"\n\n"
                            f"Analytical views:\n{json.dumps(view_descriptions, indent=2)}\n\n"
                            f"Social media posts:\n{json.dumps(post_summaries, indent=2)}\n\n"
                            f"For each post, assign it to the view it BEST aligns with "
                            f"based on the framing/question it expresses. "
                            f"If a post doesn't clearly fit any view, assign it to -1.\n\n"
                            f"Respond with ONLY a JSON array of objects. No other text:\n"
                            f'[{{"idx": 0, "view_id": 2}}, {{"idx": 1, "view_id": -1}}, ...]'
                        )}],
                    )

                    try:
                        raw_text = classify_raw.content[0].text.strip()
                        if raw_text.startswith("```"):
                            raw_text = raw_text.split("\n", 1)[-1]
                        if raw_text.endswith("```"):
                            raw_text = raw_text.rsplit("```", 1)[0]
                        assignments = json.loads(raw_text.strip())

                        for a in assignments:
                            idx = a.get("idx", -1)
                            view_id = a.get("view_id", -1)
                            if 0 <= idx < len(posts_list):
                                post = posts_list[idx]
                                if 0 <= view_id < len(views):
                                    classified[view_id].append(post)
                                else:
                                    classified["none"].append(post)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass  # Skip failed batches

                # Display results grouped by view
                for i, vw in enumerate(views):
                    color = colors[i % len(colors)]
                    name = vw.get("view_name", f"View {i+1}")
                    posts = classified.get(i, [])

                    if not posts:
                        continue

                    # Sort by engagement
                    posts.sort(key=lambda p: p.likes + p.reposts, reverse=True)

                    print(f"  {color}■ {name.upper()}{reset} — {len(posts)} posts on Bluesky")

                    for p in posts[:5]:  # Show top 5 by engagement
                        engagement = f"{p.likes}♥ {p.reposts}⟳" if p.likes or p.reposts else ""
                        print(f"    @{p.author} {engagement}")
                        print(f"      \"{p.text[:120]}\"")
                        print(f"      {p.url}")
                    if len(posts) > 5:
                        print(f"    ... and {len(posts) - 5} more posts")
                    print()

                unclassified = classified.get("none", [])
                if unclassified:
                    print(f"  \033[90m■ UNCLASSIFIED{reset} — {len(unclassified)} posts didn't fit a clear view\n")

                # Add to result for JSON export
                result["social_by_view"] = {}
                for i, vw in enumerate(views):
                    name = vw.get("view_name", f"View {i}")
                    posts = classified.get(i, [])
                    result["social_by_view"][name] = [
                        {"author": p.author, "text": p.text[:200],
                         "likes": p.likes, "reposts": p.reposts,
                         "url": p.url, "posted_at": p.posted_at}
                        for p in posts
                    ]
            else:
                print("  No posts found on Bluesky for this topic.")

            await bsky.close()

        # ── Mutation tracking ──

        if verbose and views:
            all_sources = []
            for vw in views:
                for s in vw.get("sources", []):
                    all_sources.append(s)

            if len(all_sources) >= 2:
                mutation_result = await tracer.analyze_mutations(
                    claim_text=fact_text,
                    sources=all_sources[:10],
                )
                result["mutations"] = mutation_result

                if mutation_result and mutation_result.get("overall"):
                    overall = mutation_result["overall"]
                    severity = overall.get("mutation_severity", "unknown")
                    pattern = overall.get("mutation_pattern", "unknown")
                    summary = overall.get("summary", "")

                    print_separator("MUTATION TRACKING")
                    print(f"  Severity: {severity}")
                    print(f"  Pattern: {pattern}")
                    if summary:
                        print(f"  {summary}")

                    orig_vs_final = overall.get("original_vs_final", "")
                    if orig_vs_final:
                        print(f"\n  Original vs final: {orig_vs_final}")

        # ── Missing info analysis ──

        if verbose and views and len(views) >= 2:
            print_separator("OMISSION ANALYSIS")
            omission_data = []

            for vw in views[:5]:
                sources_in_view = vw.get("sources", [])
                if not sources_in_view:
                    continue

                view_content = vw.get("key_claim", "")
                for s in sources_in_view:
                    excerpt = s.get("excerpt", s.get("framing_headline", ""))
                    if excerpt:
                        view_content += f"\n{excerpt}"

                if view_content.strip():
                    view_name = vw.get("view_name", "Unknown")
                    omission = await tracer.analyze_missing_info(
                        content_text=view_content,
                        content_url=view_name,
                        views=[l for l in views if l.get("view_name") != view_name],
                    )
                    omission["view_name"] = view_name
                    omission_data.append(omission)

                    score = omission.get("completeness_score", 0)
                    summary = omission.get("summary", "")
                    n_omissions = (
                        len(omission.get("factual_omissions", []))
                        + len(omission.get("perspective_omissions", []))
                        + len(omission.get("context_omissions", []))
                    )
                    color = colors[views.index(vw) % len(colors)]
                    print(f"\n  {color}■ {view_name}{reset} — completeness: {score:.0%}, "
                          f"{n_omissions} omissions")
                    if summary:
                        print(f"    {summary}")

            result["omissions_by_view"] = omission_data

            if omission_data:
                avg = sum(o.get("completeness_score", 0) for o in omission_data) / len(omission_data)
                print(f"\n  Average completeness per view: {avg:.0%}")
                print(f"  (Reading any single view gives you {avg:.0%} of the full picture)")

        print_separator("COST")
        print(f"  Total: ${tracer.usage['cost_usd']:.4f}")
        print(f"  Tokens: {tracer.usage['input']:,} in / {tracer.usage['output']:,} out")

        return result

    finally:
        await search.close()


async def trace_content(content: str, verbose: bool = False):
    """Full pipeline: extract claims and trace each one."""
    print_separator("FULL CONTENT TRACE")
    print(f"  Content length: {len(content)} chars\n")

    search = AnthropicSearchAdapter()
    tracer = SourceTracer(search_adapter=search)

    try:
        chains = await tracer.trace_content(content)

        print_separator("RESULTS")
        for chain in chains:
            is_narrative = chain.claim.label == ContentLabel.NARRATIVE
            status = "✓" if chain.primary_source else "?"
            label_tag = "\033[95mNARRATIVE\033[0m" if is_narrative else chain.claim.label.value.upper()
            conf = f"{chain.confidence:.0%}"
            print(f"\n  {status} [{label_tag}] [{conf}] {chain.claim.text[:80]}")
            if chain.primary_source:
                if is_narrative:
                    print(f"    ⟵ Origin: {chain.primary_source.title}")
                    print(f"      URL: {chain.primary_source.url}")
                else:
                    print(f"    → Source: {chain.primary_source.url}")
            if chain.summary:
                print(f"    {chain.summary}")

        print_separator("SUMMARY")
        total = len(chains)
        facts = [c for c in chains if c.claim.label in (ContentLabel.FACT, ContentLabel.STUDY_ANALYSIS)]
        narratives = [c for c in chains if c.claim.label == ContentLabel.NARRATIVE]
        with_origin = sum(1 for c in chains if c.primary_source)
        print(f"  Claims traced: {total}")
        print(f"  ├─ Facts/Studies: {len(facts)} (found source: {sum(1 for c in facts if c.primary_source)})")
        print(f"  ├─ Narratives: {len(narratives)} (found origin: {sum(1 for c in narratives if c.primary_source)})")
        print(f"  Average confidence: {sum(c.confidence for c in chains) / max(total, 1):.0%}")

        print_separator("COST")
        print(f"  Total: ${tracer.usage['cost_usd']:.4f}")
        print(f"  Tokens: {tracer.usage['input']:,} in / {tracer.usage['output']:,} out")
        print(f"  Cost per claim: ${tracer.usage['cost_usd'] / max(total, 1):.4f}")

        if verbose:
            for chain in chains:
                is_narrative = chain.claim.label == ContentLabel.NARRATIVE
                print_separator(f"{'NARRATIVE' if is_narrative else 'FACT'}: {chain.claim.text[:50]}...")

                if is_narrative and chain.sources:
                    originators = [s for s in chain.sources if s.tier.value == "originator"]
                    amplifiers = [s for s in chain.sources if s.tier.value == "amplifier"]
                    critics = [s for s in chain.sources if s.tier.value == "critic"]

                    if originators:
                        print(f"\n  ORIGINATORS:")
                        for s in originators:
                            print(f"    ★ {s.title}")
                            print(f"      {s.url}")
                            print(f"      \"{s.relevant_excerpt[:180]}\"")

                    if amplifiers:
                        print(f"\n  AMPLIFIERS:")
                        for s in amplifiers:
                            print(f"    ↗ {s.title}")
                            print(f"      {s.url}")

                    if critics:
                        print(f"\n  CRITICS:")
                        for s in critics:
                            print(f"    ✗ {s.title}")
                            print(f"      {s.url}")

                elif chain.sources:
                    for i, s in enumerate(chain.sources, 1):
                        print(f"  [{i}] {s.tier.value.upper()} — {s.url}")
                        if s.cites:
                            print(f"      Cites → {s.cites}")

                if chain.summary:
                    print(f"\n  {chain.summary}")

                # Mutation tracking for this chain
                if len(chain.sources) >= 2:
                    source_dicts = [
                        {"url": s.url, "title": s.title, "tier": s.tier.value,
                         "excerpt": s.relevant_excerpt}
                        for s in chain.sources
                    ]
                    mutation = await tracer.analyze_mutations(chain.claim.text, source_dicts)
                    if mutation and mutation.get("overall"):
                        overall = mutation["overall"]
                        severity = overall.get("mutation_severity", "?")
                        pattern = overall.get("mutation_pattern", "?")
                        print(f"\n  Mutation: {severity} ({pattern})")
                        if overall.get("summary"):
                            print(f"  {overall['summary']}")

        return chains

    finally:
        await search.close()


async def trace_bluesky_feed(
    handle: str, limit: int, extract_only: bool, verbose: bool,
    json_path: str = None, social: bool = False, days_back: int = 30,
):
    """Trace claims across a Bluesky user's recent posts."""
    from ingestors import BlueskyExtractor

    print_separator(f"BLUESKY FEED: @{handle}")
    print(f"  Fetching {limit} recent posts...\n")

    bsky = BlueskyExtractor()
    search = AnthropicSearchAdapter()
    tracer = SourceTracer(search_adapter=search)

    try:
        items = await bsky.extract_feed(handle, limit=limit)
        print(f"  Retrieved {len(items)} posts\n")

        all_claims = []
        all_claim_labels = []  # Track all labels for profile summary
        for i, item in enumerate(items, 1):
            if not item.text.strip():
                continue
            print(f"  Post {i}: {item.text[:80]}...")

            claims = await tracer.extract_claims(item.text)
            traceable = [
                c for c in claims
                if c.label in (ContentLabel.FACT, ContentLabel.STUDY_ANALYSIS, ContentLabel.NARRATIVE)
            ]
            all_claims.extend(traceable)
            all_claim_labels.extend(c.label.value for c in claims)

            label_counts = {}
            for c in claims:
                label_counts[c.label.value] = label_counts.get(c.label.value, 0) + 1
            labels_str = ", ".join(f"{v}x {k}" for k, v in label_counts.items())
            print(f"    → {len(claims)} claims ({labels_str})")
            print(f"    → {len(traceable)} traceable\n")

        # Creator profile summary (#10)
        print_separator("CREATOR PROFILE")
        print(f"  Handle: @{handle}")
        print(f"  Posts analyzed: {len(items)}")
        print(f"  Total claims: {len(all_claim_labels)}")
        if all_claim_labels:
            total = len(all_claim_labels)
            label_summary = {}
            for l in all_claim_labels:
                label_summary[l] = label_summary.get(l, 0) + 1
            for label, count in sorted(label_summary.items(), key=lambda x: -x[1]):
                pct = count / total * 100
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                print(f"    {label:15s} {bar} {pct:.0f}% ({count})")
        print(f"  Traceable claims: {len(all_claims)}")

        # Trace claims
        traced_chains = []
        if all_claims and not extract_only:
            print_separator("TRACING CLAIMS")
            for claim in all_claims:
                chain = await tracer.trace_claim(claim)
                traced_chains.append(chain)
                status = "✓" if chain.primary_source else "?"
                is_narr = claim.label == ContentLabel.NARRATIVE
                label = "\033[95mNARR\033[0m" if is_narr else claim.label.value.upper()[:4]
                print(f"  {status} [{label}] [{chain.confidence:.0%}] {claim.text[:65]}")
                if chain.primary_source:
                    if is_narr:
                        print(f"    ⟵ Origin: {chain.primary_source.title}")
                    else:
                        print(f"    → {chain.primary_source.url}")

        # Social spread for top narrative claims
        social_data = {}
        if social and traced_chains:
            narrative_chains = [c for c in traced_chains if c.claim.label == ContentLabel.NARRATIVE]
            if narrative_chains:
                print_separator("SOCIAL SPREAD (top narratives)")
                from social_search import BlueskySearch, SpreadAnalyzer

                bsky_search = BlueskySearch()
                await bsky_search.login()
                analyzer = SpreadAnalyzer()

                for chain in narrative_chains[:3]:  # Top 3 narratives
                    print(f"\n  Searching: {chain.claim.text[:60]}...")
                    spread = await bsky_search.search_narrative(
                        chain.claim.text, days_back=days_back, limit=50
                    )
                    if spread.posts:
                        spread = await analyzer.analyze(spread)
                        print(f"    {spread.total_posts_found} posts ({spread.spread_type.value})")
                        if spread.most_engaged_post:
                            mp = spread.most_engaged_post
                            print(f"    Top: @{mp.author} ({mp.likes} likes)")
                        social_data[chain.claim.text] = spread.to_dict()

                await bsky_search.close()

        print_separator("COST")
        print(f"  Total: ${tracer.usage['cost_usd']:.4f}")
        print(f"  Tokens: {tracer.usage['input']:,} in / {tracer.usage['output']:,} out")

        # JSON export (#3)
        if json_path:
            export = {
                "handle": handle,
                "posts_analyzed": len(items),
                "profile": {
                    "total_claims": len(all_claim_labels),
                    "label_distribution": {},
                    "traceable": len(all_claims),
                },
                "claims": [],
            }
            if all_claim_labels:
                total = len(all_claim_labels)
                for l in set(all_claim_labels):
                    count = all_claim_labels.count(l)
                    export["profile"]["label_distribution"][l] = {
                        "count": count, "percentage": round(count / total * 100, 1)
                    }
            for chain in traced_chains:
                entry = {
                    "claim": chain.claim.text,
                    "label": chain.claim.label.value,
                    "confidence": chain.confidence,
                    "summary": chain.summary,
                    "primary_source": None,
                    "sources": [
                        {"url": s.url, "title": s.title, "tier": s.tier.value,
                         "confidence": s.confidence, "excerpt": s.relevant_excerpt}
                        for s in chain.sources
                    ],
                }
                if chain.primary_source:
                    entry["primary_source"] = {
                        "url": chain.primary_source.url,
                        "title": chain.primary_source.title,
                        "tier": chain.primary_source.tier.value,
                    }
                export["claims"].append(entry)

            if social_data:
                export["social_spread"] = social_data

            with open(json_path, "w") as f:
                json.dump(export, f, indent=2, default=str)
            print(f"\n  Exported to: {json_path}")

    finally:
        await bsky.close()
        await search.close()


def main():
    parser = argparse.ArgumentParser(
        description="Tributary Source Tracer — trace factual claims to their origins"
    )
    parser.add_argument(
        "--claim", type=str,
        help="Trace a single claim (e.g., --claim 'The US added 256k jobs')"
    )
    parser.add_argument(
        "--file", type=str,
        help="Read content from a text file"
    )
    parser.add_argument(
        "--url", type=str,
        help="Fetch and trace claims from any URL (auto-detects platform)"
    )
    parser.add_argument(
        "--bluesky-feed", type=str,
        help="Trace recent posts from a Bluesky user (e.g., --bluesky-feed user.bsky.social)"
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Number of posts to fetch for --bluesky-feed (default: 10)"
    )
    parser.add_argument(
        "--extract-only", action="store_true",
        help="Only extract and classify claims, skip tracing (very cheap)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed source chain info"
    )
    parser.add_argument(
        "--social", action="store_true",
        help="Also search Bluesky for social media spread (requires BLUESKY_HANDLE and BLUESKY_APP_PASSWORD)"
    )
    parser.add_argument(
        "--perspectives", action="store_true",
        help="Find competing narratives built on the same factual data (use with --claim)"
    )
    parser.add_argument(
        "--days-back", type=int, default=30,
        help="How far back to search social media (default: 30 days)"
    )
    parser.add_argument(
        "--json", type=str, metavar="FILE",
        help="Export full trace results to a JSON file (e.g., --json results.json)"
    )

    args = parser.parse_args()

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        print()
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        print()
        print("Get your key at: https://console.anthropic.com/")
        sys.exit(1)

    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║              TRIBUTARY SOURCE TRACER — LOCAL DEMO                   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    # --bluesky-feed: special mode that traces multiple posts from a user
    if args.bluesky_feed:
        asyncio.run(trace_bluesky_feed(
            args.bluesky_feed, args.limit, args.extract_only, args.verbose,
            json_path=args.json, social=args.social, days_back=args.days_back,
        ))
        return

    # Determine content source
    if args.url:
        from ingestors import ContentExtractor
        print(f"\nIngesting: {args.url}")

        async def _extract():
            extractor = ContentExtractor()
            try:
                item = await extractor.extract(args.url)
                print(f"  Platform: {item.platform.value}")
                print(f"  Author: @{item.author}")
                if item.title:
                    print(f"  Title: {item.title}")
                print(f"  Text: {len(item.text)} chars")
                return item.text
            finally:
                await extractor.close()

        content = asyncio.run(_extract())
        if not content:
            print("\nError: No text content extracted from URL.")
            sys.exit(1)
    elif args.file:
        with open(args.file) as f:
            content = f.read()
        print(f"\nLoaded: {args.file} ({len(content)} chars)")
    else:
        content = SAMPLE_ARTICLE

    # Run the appropriate mode
    chains = None
    spread_results = {}  # claim_text → SpreadAnalysis
    perspectives_result = None

    if args.claim and args.perspectives:
        # Perspectives on a specific claim
        perspectives_result = asyncio.run(
            find_perspectives(
                args.claim, verbose=args.verbose,
                social=args.social, days_back=args.days_back,
            )
        )
    elif args.claim:
        result = asyncio.run(trace_single_claim(
            args.claim, verbose=args.verbose,
            social=args.social, days_back=args.days_back,
        ))
        if result:
            chain, spread, mutations = result
            chains = [chain] if chain else []
            if spread:
                spread_results[args.claim] = spread
    elif args.extract_only:
        asyncio.run(extract_only(content))
    elif args.perspectives:
        # URL/file + --perspectives: extract claims, pick most significant, run perspectives
        async def _url_perspectives():
            search = AnthropicSearchAdapter()
            tracer = SourceTracer(search_adapter=search)
            try:
                print("\n[step 1] Extracting claims from content...")
                claims = await tracer.extract_claims(content)
                traceable = [
                    c for c in claims
                    if c.label in (ContentLabel.FACT, ContentLabel.STUDY_ANALYSIS, ContentLabel.NARRATIVE)
                ]
                if not traceable:
                    print("  No traceable claims found in this content.")
                    return None
                print(f"  Found {len(traceable)} traceable claims:")
                for i, c in enumerate(traceable, 1):
                    print(f"    {i}. [{c.label.value}] {c.text[:80]}")
                # Pick the most interesting claim (prefer narratives, then facts)
                narratives = [c for c in traceable if c.label == ContentLabel.NARRATIVE]
                best = narratives[0] if narratives else traceable[0]
                print(f"\n  Selected for perspectives: {best.text[:80]}...")
            finally:
                await search.close()
            return best.text

        selected_claim = asyncio.run(_url_perspectives())
        if selected_claim:
            perspectives_result = asyncio.run(
                find_perspectives(
                    selected_claim, verbose=args.verbose,
                    social=args.social, days_back=args.days_back,
                )
            )
    else:
        chains = asyncio.run(trace_content(content, verbose=args.verbose))

    # Export to JSON if requested
    if args.json and perspectives_result:
        with open(args.json, "w") as f:
            json.dump(perspectives_result, f, indent=2, default=str)
        print(f"\n  Perspectives exported to: {args.json}")

    elif args.json and chains:
        export_data = []
        for chain in chains:
            if chain is None:
                continue
            entry = {
                "claim": chain.claim.text,
                "label": chain.claim.label.value,
                "confidence": chain.confidence,
                "chain_complete": chain.chain_complete,
                "summary": chain.summary,
                "primary_source": None,
                "sources": [],
                "social_spread": None,
            }
            if chain.primary_source:
                entry["primary_source"] = {
                    "url": chain.primary_source.url,
                    "title": chain.primary_source.title,
                    "tier": chain.primary_source.tier.value,
                    "excerpt": chain.primary_source.relevant_excerpt,
                }
            for s in chain.sources:
                entry["sources"].append({
                    "url": s.url,
                    "title": s.title,
                    "tier": s.tier.value,
                    "confidence": s.confidence,
                    "excerpt": s.relevant_excerpt,
                    "referenced_url": s.referenced_url,
                    "flow_type": s.flow_type,
                    "flow_evidence": s.flow_evidence,
                    "published_date": s.published_date,
                    "author": s.author,
                })
            # Include social spread data if available
            spread = spread_results.get(chain.claim.text)
            if spread:
                entry["social_spread"] = spread.to_dict()

            # Include mutation data if available
            if mutations:
                entry["mutations"] = mutations

            export_data.append(entry)

        with open(args.json, "w") as f:
            json.dump(export_data, f, indent=2, default=str)
        print(f"\n  Results exported to: {args.json}")


if __name__ == "__main__":
    main()
