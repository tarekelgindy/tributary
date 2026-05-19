# Tributary — See Where Your Information Really Comes From

## What is Tributary?

Every day you read news articles, scroll through social media, and watch videos that shape how you understand the world. But have you ever wondered: where did this information actually come from? Who said it first? And what are you *not* being told?

Tributary is a tool that answers these questions. Give it any piece of content — a news article, a social media post, a YouTube video — and it will:

- **Trace facts to their source.** When an article says "the economy added 256,000 jobs," Tributary finds the original government report that number came from.

- **Trace narratives to their origin.** When someone says "the economy is rigged," Tributary finds who coined that framing, who spread it, and who pushed back on it.

- **Show you competing perspectives.** The same event gets framed differently by different groups. Tributary finds these different framings and shows you what each one emphasizes and what it leaves out.

- **Reveal what you're not being told.** Each perspective tells a partial story. Tributary measures how complete each one is, so you can see what any single source misses.

- **Track how information changes as it spreads.** A nuanced research finding can become a sensationalized headline by the time it reaches your feed. Tributary shows you exactly where the distortion happened.

## Why does this matter?

The internet has fractured into echo chambers. People in different information ecosystems see the same events through completely different lenses — and most don't realize it. Tributary makes this visible.

It doesn't tell you what to think. It doesn't label things as true or false. Instead, it shows you the machinery behind what you're reading: where it came from, how it changed along the way, what other perspectives exist, and what's being left out. Armed with that context, you can make your own informed judgments.

## Quick Start

You need an Anthropic API key ($5 minimum credit) and Python installed.

```bash
# One-time setup
pip install anthropic httpx
export ANTHROPIC_API_KEY="your-key-here"

# See what claims a news article makes (costs less than a penny)
python demo.py --url https://some-news-article.com --extract-only

# Trace where a specific claim comes from
python demo.py --claim "The US economy added 256,000 jobs" --verbose

# See how different groups frame the same event
python demo.py --claim "ICE agent killed Renee Good" --perspectives --verbose

# See what's trending and how it's being framed
python batch.py --discover-only
```

## What does the output look like?

When you run a perspectives analysis, Tributary shows you something like:

```
COMMON GROUND
  Underlying data: ICE agent shot and killed Renee Good during an 
  enforcement operation in Minneapolis on January 7, 2026

  6 raw framings consolidated into 4 distinct lenses:

  ■ ACCOUNTABILITY — Was this lawful? Who is responsible?
  ■ USE OF FORCE — Were procedures followed? Was force proportionate?
  ■ HUMAN COST — Who was affected and what was lost?
  ■ POLITICAL FALLOUT — How is this being used politically?

  Each lens asks a different QUESTION about the same event.
  The underlying facts are shared. The disagreements are about
  which questions matter most and how to interpret the answers.
```

Each lens groups the sources that ask the same fundamental question — regardless of their political leaning. You see the full picture, not just one side's version of it.

## Learn More

See the full [README](README.md) for detailed setup, all features, and technical documentation.
