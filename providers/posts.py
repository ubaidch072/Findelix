# providers/posts.py
import re
import requests, feedparser
from urllib.parse import urljoin
from typing import List, Dict, Optional

from .serper import serper_news, gemini_summarize  # Gemini kept as fallback

UA = {"User-Agent": "Findelix/1.0 (+https://example.com/findelix)"}
MAX_POSTS = 3
SUMMARY_MIN = 100
SUMMARY_MAX = 150

# --- ML Summarizer (preferred). If unavailable, we'll fall back to Gemini ---
_SUMMARIZER = None
def _get_summarizer():
    """
    Lazy-load the ML summarizer so the app still runs even if transformers/torch
    aren't installed in some environments.
    """
    global _SUMMARIZER
    if _SUMMARIZER is None:
        try:
            from ml.summarizer import Summarizer
            _SUMMARIZER = Summarizer()  # swap in fine-tuned ckpt via Summarizer(model_ckpt="...")
        except Exception:
            _SUMMARIZER = False  # signals "unavailable"
    return _SUMMARIZER

def _enforce_word_window(text: str, wmin: int, wmax: int) -> str:
    if not text:
        return text
    words = text.split()
    if len(words) > wmax:
        text = " ".join(words[:wmax]).rstrip()
        if not text.endswith((".", "!", "?")):
            text += "."
    return text

def _get(url: str):
    return requests.get(url, headers=UA, timeout=12)

def _discover_site_feeds(website: str) -> List[Dict]:
    posts: List[Dict] = []
    for path in ["/newsroom", "/news", "/press", "/blog", "/stories"]:
        url = urljoin(website.rstrip("/"), path)
        try:
            r = _get(url)
            if r.status_code < 400:
                for rss in ["/feed", "/rss", "/atom.xml", "/index.xml"]:
                    rr = _get(url.rstrip("/") + rss)
                    if rr.status_code < 400 and "xml" in rr.headers.get("Content-Type", "").lower():
                        feed = feedparser.parse(rr.text)
                        for e in feed.entries[:6]:
                            posts.append({
                                "source": "blog",
                                "title": e.get("title"),
                                "url": e.get("link"),
                                "published": str(e.get("published", "")),
                            })
                        break
        except Exception:
            pass
    return posts

def get_recent_posts(company: str, domain: str, website: Optional[str]) -> List[Dict]:
    """
    Return a list of recent posts, capped to MAX_POSTS.
    If none found, return a single placeholder row: title = 'No Posts to Show'.
    """
    posts: List[Dict] = []

    # 1) first-party feeds
    if website:
        posts.extend(_discover_site_feeds(website))

    # 2) SERPER news (fallback if nothing from the site)
    q = company or domain or ""
    if q and not posts:
        try:
            data = serper_news(q, num=8)
            for n in data.get("news", []):
                posts.append({
                    "source": "news",
                    "title": n.get("title"),
                    "url": n.get("link"),
                    "published": n.get("date"),
                })
        except Exception:
            pass

    # Clean + cap
    posts = [p for p in posts if isinstance(p.get("title"), str) and p.get("title")]
    posts = posts[:MAX_POSTS]

    # Placeholder if empty
    if not posts:
        posts = [{
            "source": None,
            "title": "No Posts to Show",
            "url": None,
            "published": None,
            "placeholder": True
        }]

    return posts

def build_summary_from_posts(posts: List[Dict], company: str, domain: str) -> str:
    """
    Build a 100–150 word summary using the ML model; fall back to Gemini if needed.
    """
    # Collect material from kept posts (skip placeholder)
    material = []
    for p in posts:
        if p.get("placeholder"):
            continue
        title = p.get("title") or ""
        published = p.get("published") or ""
        line = f"{title}. {published}".strip()
        if line:
            material.append(line)

    long_text = "\n".join(material)

    # Preferred: ML
    summary = ""
    summarizer = _get_summarizer()
    if summarizer:
        try:
            summary = summarizer.summarize_100_150_words(
                long_text, target_min=SUMMARY_MIN, target_max=SUMMARY_MAX
            )
        except Exception:
            summary = ""

    # Fallback: Gemini with explicit word bounds
    if not summary:
        if material:
            bullets = "\n".join([f"- {m}" for m in material])
            prompt = (
                f"Summarize the following recent items about {company or domain}. "
                f"Write {SUMMARY_MIN}-{SUMMARY_MAX} words, neutral tone, focus on official product/feature announcements, "
                f"partnerships, or financial updates. Avoid unrelated celebrity news.\n\n{bullets}"
            )
        else:
            prompt = (
                f"In {SUMMARY_MIN}-{SUMMARY_MAX} words, provide a neutral overview of {company or domain}: "
                f"what it does, products/services, market position, and very recent developments if any."
            )
        try:
            summary = gemini_summarize(prompt) or ""
        except Exception:
            summary = ""

    # Final enforcement
    summary = _enforce_word_window(summary, SUMMARY_MIN, SUMMARY_MAX)
    return summary
