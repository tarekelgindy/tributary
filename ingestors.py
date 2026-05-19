"""
Tributary Content Ingestors
============================
Platform-specific extractors that normalize content from any source
into a common ContentItem format for the tracing pipeline.

Supported platforms:
  - Web articles (URL → extracted text)
  - Bluesky (post/thread URL or handle → text, no API key needed)
  - X/Twitter (post URL → text, limited API)
  - YouTube (video URL → transcript, no API key needed)
  - TikTok/Instagram (video URL → audio → transcript via Whisper)

Each extractor produces a ContentItem that feeds directly into
SourceTracer.trace_content().

Setup:
  pip install httpx atproto youtube-transcript-api openai

Optional (for TikTok/Instagram video transcription):
  pip install yt-dlp openai
  # Requires ffmpeg installed on system
"""

import re
import json
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import urlparse, parse_qs

import httpx


# ---------------------------------------------------------------------------
# Common content model
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    WEB = "web"
    BLUESKY = "bluesky"
    X = "x"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


@dataclass
class ContentItem:
    """
    Normalized content from any platform.
    This is the input to SourceTracer.trace_content().
    """
    platform: Platform
    text: str                              # The actual content text
    author: str                            # Username/handle of creator
    url: str                               # Original URL
    title: str = ""                        # Title if available
    published_at: Optional[str] = None     # ISO timestamp
    content_id: str = ""                   # Platform-specific ID
    metadata: dict = field(default_factory=dict)  # Platform-specific extras
    extracted_at: str = ""

    def __post_init__(self):
        if not self.extracted_at:
            self.extracted_at = datetime.now(timezone.utc).isoformat()
        if not self.content_id:
            self.content_id = hashlib.sha256(
                f"{self.platform}:{self.url}".encode()
            ).hexdigest()[:12]

    @property
    def summary(self) -> str:
        return (
            f"[{self.platform.value}] @{self.author}: "
            f"{self.text[:100]}..."
        )


# ---------------------------------------------------------------------------
# Base extractor
# ---------------------------------------------------------------------------

class BaseExtractor:
    """Base class for platform extractors."""

    platform: Platform

    def __init__(self):
        self.http = httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "Tributary/1.0 (source tracer)"},
            follow_redirects=True,
        )

    async def extract(self, url_or_id: str) -> ContentItem:
        raise NotImplementedError

    async def close(self):
        await self.http.aclose()

    def _strip_html(self, html: str) -> str:
        """Basic HTML to text conversion."""
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Web article extractor
# ---------------------------------------------------------------------------

class WebExtractor(BaseExtractor):
    """
    Extracts article text from any web URL.
    Works with news sites, blogs, etc.
    
    Usage:
        extractor = WebExtractor()
        item = await extractor.extract("https://nytimes.com/some-article")
    """

    platform = Platform.WEB

    async def extract(self, url: str) -> ContentItem:
        resp = await self.http.get(url)
        resp.raise_for_status()

        html = resp.text
        text = self._strip_html(html)

        # Try to extract title from <title> tag
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        # Try to extract author from meta tags
        author_match = re.search(
            r'<meta[^>]*name=["\']author["\'][^>]*content=["\'](.*?)["\']',
            html, re.IGNORECASE,
        )
        author = author_match.group(1) if author_match else urlparse(url).netloc

        return ContentItem(
            platform=Platform.WEB,
            text=text[:20000],  # Cap to avoid huge pages
            author=author,
            url=url,
            title=title,
        )


# ---------------------------------------------------------------------------
# Bluesky extractor
# ---------------------------------------------------------------------------

class BlueskyExtractor(BaseExtractor):
    """
    Extracts posts and threads from Bluesky.
    Uses the public AT Protocol API — no API key needed.
    
    Usage:
        extractor = BlueskyExtractor()
        
        # Single post by URL
        item = await extractor.extract(
            "https://bsky.app/profile/handle.bsky.social/post/abc123"
        )
        
        # Recent posts by handle
        items = await extractor.extract_feed("handle.bsky.social", limit=20)
    """

    platform = Platform.BLUESKY
    API_BASE = "https://public.api.bsky.app/xrpc"

    def _normalize_handle(self, handle_or_url: str) -> str:
        """Accept either a handle or a full profile URL."""
        # Strip full URLs like https://bsky.app/profile/user.bsky.social
        match = re.match(r"https?://bsky\.app/profile/([^/]+)", handle_or_url)
        if match:
            return match.group(1)
        return handle_or_url.strip().lstrip("@")

    async def extract(self, url: str) -> ContentItem:
        """Extract a single post (and its thread) from a Bluesky URL."""
        # Parse URL: https://bsky.app/profile/{handle}/post/{post_id}
        match = re.match(
            r"https?://bsky\.app/profile/([^/]+)/post/([^/]+)", url
        )
        if not match:
            raise ValueError(f"Not a valid Bluesky post URL: {url}")

        handle, post_id = match.groups()

        # Resolve handle to DID
        did = await self._resolve_handle(handle)

        # Build AT URI
        at_uri = f"at://{did}/app.bsky.feed.post/{post_id}"

        # Get the thread
        resp = await self.http.get(
            f"{self.API_BASE}/app.bsky.feed.getPostThread",
            params={"uri": at_uri, "depth": 0},
        )
        resp.raise_for_status()
        data = resp.json()

        thread = data.get("thread", {})
        post = thread.get("post", {})
        record = post.get("record", {})
        author_data = post.get("author", {})

        return ContentItem(
            platform=Platform.BLUESKY,
            text=record.get("text", ""),
            author=author_data.get("handle", handle),
            url=url,
            published_at=record.get("createdAt"),
            content_id=post_id,
            metadata={
                "likes": post.get("likeCount", 0),
                "reposts": post.get("repostCount", 0),
                "replies": post.get("replyCount", 0),
                "did": did,
                "embedded_links": self._extract_links(record),
            },
        )

    async def extract_feed(
        self, handle: str, limit: int = 20
    ) -> list[ContentItem]:
        """Extract recent posts from a Bluesky user's feed."""
        handle = self._normalize_handle(handle)
        resp = await self.http.get(
            f"{self.API_BASE}/app.bsky.feed.getAuthorFeed",
            params={"actor": handle, "limit": min(limit, 100)},
        )
        resp.raise_for_status()
        data = resp.json()

        items = []
        for feed_item in data.get("feed", []):
            post = feed_item.get("post", {})
            record = post.get("record", {})
            author_data = post.get("author", {})
            uri = post.get("uri", "")

            # Extract post ID from AT URI
            post_id = uri.split("/")[-1] if uri else ""
            post_handle = author_data.get("handle", handle)
            post_url = f"https://bsky.app/profile/{post_handle}/post/{post_id}"

            items.append(ContentItem(
                platform=Platform.BLUESKY,
                text=record.get("text", ""),
                author=post_handle,
                url=post_url,
                published_at=record.get("createdAt"),
                content_id=post_id,
                metadata={
                    "likes": post.get("likeCount", 0),
                    "reposts": post.get("repostCount", 0),
                    "replies": post.get("replyCount", 0),
                    "embedded_links": self._extract_links(record),
                },
            ))

        return items

    async def _resolve_handle(self, handle: str) -> str:
        """Resolve a Bluesky handle to a DID."""
        resp = await self.http.get(
            f"{self.API_BASE}/com.atproto.identity.resolveHandle",
            params={"handle": handle},
        )
        resp.raise_for_status()
        return resp.json()["did"]

    def _extract_links(self, record: dict) -> list[str]:
        """Extract any links embedded in a Bluesky post."""
        links = []
        for facet in record.get("facets", []):
            for feature in facet.get("features", []):
                if feature.get("$type") == "app.bsky.richtext.facet#link":
                    links.append(feature.get("uri", ""))
        # Also check for embedded external links
        embed = record.get("embed", {})
        if embed.get("$type") == "app.bsky.embed.external":
            external = embed.get("external", {})
            if external.get("uri"):
                links.append(external["uri"])
        return links


# ---------------------------------------------------------------------------
# X/Twitter extractor
# ---------------------------------------------------------------------------

class XExtractor(BaseExtractor):
    """
    Extracts posts from X (Twitter).

    X's API is expensive and restrictive, so this extractor supports
    multiple approaches:

    1. Paste mode: User pastes tweet text directly (free, always works)
    2. URL scraping: Attempts to extract from common embed/cache services

    Usage:
        extractor = XExtractor()

        # From pasted text (most reliable)
        item = extractor.from_text(
            text="The economy added 256k jobs...",
            author="somehandle",
            url="https://x.com/somehandle/status/123456"
        )
    """

    platform = Platform.X

    def from_text(
        self, text: str, author: str, url: str = ""
    ) -> ContentItem:
        """Create a ContentItem from manually pasted tweet text."""
        # Extract post ID from URL if provided
        post_id = ""
        if url:
            match = re.search(r"/status/(\d+)", url)
            post_id = match.group(1) if match else ""

        return ContentItem(
            platform=Platform.X,
            text=text,
            author=author,
            url=url,
            content_id=post_id,
        )

    async def extract(self, url: str) -> ContentItem:
        """
        Attempt to extract tweet content from URL.
        Uses Twitter's oEmbed endpoint (free, no auth required)
        which returns the tweet text in HTML.
        """
        # Twitter oEmbed endpoint — free, no API key
        oembed_url = "https://publish.twitter.com/oembed"
        try:
            resp = await self.http.get(
                oembed_url, params={"url": url, "omit_script": "true"}
            )
            resp.raise_for_status()
            data = resp.json()

            html = data.get("html", "")
            text = self._strip_html(html)
            author = data.get("author_name", "")

            match = re.search(r"/status/(\d+)", url)
            post_id = match.group(1) if match else ""

            return ContentItem(
                platform=Platform.X,
                text=text,
                author=author,
                url=url,
                content_id=post_id,
                metadata={"source": "oembed"},
            )
        except Exception as e:
            raise RuntimeError(
                f"Could not extract tweet. X's API is restricted. "
                f"Use XExtractor.from_text() to paste the content manually. "
                f"Error: {e}"
            )


# ---------------------------------------------------------------------------
# YouTube extractor
# ---------------------------------------------------------------------------

class YouTubeExtractor(BaseExtractor):
    """
    Extracts video transcripts from YouTube.
    Uses youtube-transcript-api — free, no API key needed.

    Falls back to video description if no transcript available.

    Setup:
        pip install youtube-transcript-api

    Usage:
        extractor = YouTubeExtractor()
        item = await extractor.extract(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
    """

    platform = Platform.YOUTUBE

    def _parse_video_id(self, url: str) -> str:
        """Extract video ID from various YouTube URL formats."""
        parsed = urlparse(url)

        if parsed.hostname in ("youtu.be",):
            return parsed.path.lstrip("/")

        if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
            if parsed.path == "/watch":
                return parse_qs(parsed.query).get("v", [""])[0]
            if parsed.path.startswith(("/embed/", "/v/")):
                return parsed.path.split("/")[2]
            if parsed.path.startswith("/shorts/"):
                return parsed.path.split("/")[2]

        raise ValueError(f"Could not extract video ID from: {url}")

    async def extract(self, url: str) -> ContentItem:
        """Extract transcript from a YouTube video."""
        video_id = self._parse_video_id(url)

        # Try to get transcript
        transcript_text = await self._get_transcript(video_id)

        # Get video metadata via oEmbed (free, no API key)
        title, author = await self._get_metadata(url)

        return ContentItem(
            platform=Platform.YOUTUBE,
            text=transcript_text,
            author=author,
            url=url,
            title=title,
            content_id=video_id,
            metadata={
                "has_transcript": bool(transcript_text),
                "transcript_source": "captions" if transcript_text else "none",
            },
        )

    async def _get_transcript(self, video_id: str) -> str:
        """Get transcript using youtube-transcript-api."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            ytt_api = YouTubeTranscriptApi()
            transcript = ytt_api.fetch(video_id)
            return " ".join(snippet.text for snippet in transcript)
        except ImportError:
            raise ImportError(
                "youtube-transcript-api is required for YouTube extraction. "
                "Install it: pip install youtube-transcript-api"
            )
        except Exception as e:
            print(f"  [youtube] No transcript available: {e}")
            return ""

    async def _get_metadata(self, url: str) -> tuple[str, str]:
        """Get video title and author via oEmbed."""
        try:
            resp = await self.http.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("title", ""), data.get("author_name", "")
        except Exception:
            return "", ""


# ---------------------------------------------------------------------------
# Video transcription extractor (TikTok, Instagram Reels)
# ---------------------------------------------------------------------------

class VideoTranscriptExtractor(BaseExtractor):
    """
    Extracts content from video platforms (TikTok, Instagram Reels)
    by downloading audio and transcribing with OpenAI Whisper.

    This is the most expensive extractor — Whisper API costs ~$0.006/minute.
    A 60-second TikTok costs less than a cent to transcribe.

    Setup:
        pip install yt-dlp openai
        # Also requires ffmpeg installed on your system

    Usage:
        extractor = VideoTranscriptExtractor()

        # TikTok
        item = await extractor.extract(
            "https://www.tiktok.com/@user/video/123456"
        )

        # Instagram Reel
        item = await extractor.extract(
            "https://www.instagram.com/reel/ABC123/"
        )
    """

    platform = Platform.TIKTOK  # Updated per-URL

    def _detect_platform(self, url: str) -> Platform:
        """Detect platform from URL."""
        parsed = urlparse(url)
        if "tiktok.com" in (parsed.hostname or ""):
            return Platform.TIKTOK
        if "instagram.com" in (parsed.hostname or ""):
            return Platform.INSTAGRAM
        return Platform.WEB

    async def extract(self, url: str) -> ContentItem:
        """Download video audio, transcribe with Whisper."""
        import subprocess
        import tempfile
        import os

        platform = self._detect_platform(url)

        # Step 1: Download audio using yt-dlp
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.m4a")

            try:
                result = subprocess.run(
                    [
                        "yt-dlp",
                        "--extract-audio",
                        "--audio-format", "m4a",
                        "--output", audio_path,
                        "--no-playlist",
                        "--quiet",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode != 0:
                    raise RuntimeError(
                        f"yt-dlp failed: {result.stderr[:200]}"
                    )
            except FileNotFoundError:
                raise ImportError(
                    "yt-dlp is required for video extraction. "
                    "Install it: pip install yt-dlp"
                )

            # Find the actual output file (yt-dlp may add extension)
            audio_files = [
                f for f in os.listdir(tmpdir)
                if f.endswith((".m4a", ".mp3", ".wav", ".ogg", ".webm"))
            ]
            if not audio_files:
                raise RuntimeError("No audio file downloaded")
            actual_audio = os.path.join(tmpdir, audio_files[0])

            # Step 2: Transcribe with Whisper
            transcript = await self._transcribe_whisper(actual_audio)

        # Step 3: Try to get metadata
        author, title = self._parse_url_metadata(url, platform)

        return ContentItem(
            platform=platform,
            text=transcript,
            author=author,
            url=url,
            title=title,
            metadata={
                "transcript_source": "whisper",
                "transcription_model": "whisper-1",
            },
        )

    async def _transcribe_whisper(self, audio_path: str) -> str:
        """Transcribe audio file using OpenAI Whisper API."""
        try:
            import openai

            client = openai.OpenAI()  # Uses OPENAI_API_KEY env var

            with open(audio_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                )

            return transcription
        except ImportError:
            raise ImportError(
                "openai package is required for video transcription. "
                "Install it: pip install openai\n"
                "Set OPENAI_API_KEY environment variable."
            )

    def _parse_url_metadata(
        self, url: str, platform: Platform
    ) -> tuple[str, str]:
        """Extract what we can from the URL itself."""
        parsed = urlparse(url)

        if platform == Platform.TIKTOK:
            # https://www.tiktok.com/@username/video/1234
            match = re.search(r"/@([^/]+)", parsed.path)
            author = match.group(1) if match else ""
            return author, ""

        if platform == Platform.INSTAGRAM:
            # Author not in URL for reels
            return "", ""

        return "", ""


# ---------------------------------------------------------------------------
# Auto-detect extractor
# ---------------------------------------------------------------------------

class ContentExtractor:
    """
    Auto-detects platform from URL and uses the appropriate extractor.

    Usage:
        extractor = ContentExtractor()

        # Automatically picks the right extractor
        item = await extractor.extract("https://bsky.app/profile/user/post/abc")
        item = await extractor.extract("https://youtube.com/watch?v=xyz")
        item = await extractor.extract("https://tiktok.com/@user/video/123")
        item = await extractor.extract("https://nytimes.com/some-article")

        # Extract and immediately trace
        chains = await extractor.extract_and_trace(
            "https://youtube.com/watch?v=xyz",
            tracer=my_tracer
        )
    """

    def __init__(self):
        self._extractors = {
            Platform.WEB: WebExtractor(),
            Platform.BLUESKY: BlueskyExtractor(),
            Platform.X: XExtractor(),
            Platform.YOUTUBE: YouTubeExtractor(),
            Platform.TIKTOK: VideoTranscriptExtractor(),
            Platform.INSTAGRAM: VideoTranscriptExtractor(),
        }

    def detect_platform(self, url: str) -> Platform:
        """Detect which platform a URL belongs to."""
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        if "bsky.app" in hostname:
            return Platform.BLUESKY
        if "twitter.com" in hostname or "x.com" in hostname:
            return Platform.X
        if "youtube.com" in hostname or "youtu.be" in hostname:
            return Platform.YOUTUBE
        if "tiktok.com" in hostname:
            return Platform.TIKTOK
        if "instagram.com" in hostname:
            return Platform.INSTAGRAM

        return Platform.WEB

    async def extract(self, url: str) -> ContentItem:
        """Auto-detect platform and extract content."""
        platform = self.detect_platform(url)
        extractor = self._extractors[platform]
        print(f"  [ingest] Detected platform: {platform.value}")
        return await extractor.extract(url)

    async def extract_and_trace(self, url: str, tracer) -> dict:
        """
        Extract content from URL and run the full tracing pipeline.
        Returns both the ContentItem and the provenance chains.
        """
        item = await self.extract(url)
        print(f"  [ingest] Extracted {len(item.text)} chars from @{item.author}")

        if not item.text:
            return {
                "content": item,
                "chains": [],
                "error": "No text content extracted",
            }

        chains = await tracer.trace_content(item.text)

        return {
            "content": item,
            "chains": chains,
            "total_claims": len(chains),
            "traced_to_primary": sum(1 for c in chains if c.primary_source),
        }

    async def close(self):
        for extractor in self._extractors.values():
            await extractor.close()
