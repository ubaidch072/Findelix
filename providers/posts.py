import os, re, time
from typing import List, Dict, Optional
from urllib.parse import urljoin
import requests, feedparser

from .serper import serper_news, serper_search, summarize_text, gemini_summarize

DEFAULT_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "1"))

def _http_get_with_retry(url, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = DEFAULT_TIMEOUT
    last_err = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            return requests.get(url, **kwargs)
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    raise last_err

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36 Findelix/1.0"),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_POSTS = 3
SUMMARY_MIN = 100
SUMMARY_MAX = 150

def _enforce_word_window(text: str, wmin: int, wmax: int) -> str:
    text = (text or "").strip()
    if not text: return text
    words = text.split()
    if len(words) > wmax:
        text = " ".join(words[:wmax]).rstrip()
        if not text.endswith((".", "!", "?")):
            text += "."
    return text

def _try_extract_rss_links(html: str, base_url: str) -> List[str]:
    urls: List[str] = []
    try:
        for m in re.finditer(r'href="([^"]+\.xml)"', html, flags=re.I):
            urls.append(urljoin(base_url, m.group(1)))
        for m in re.finditer(r'href="([^"]+/(?:feed|rss|atom)/?)"', html, flags=re.I):
            urls.append(urljoin(base_url, m.group(1)))
    except Exception:
        pass
    seen=set(); out=[]
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out[:5]

def _fetch_feed_items(url: str) -> List[Dict]:
    try:
        r = _http_get_with_retry(url, headers=UA_HEADERS, allow_redirects=True)
        if r.status_code >= 400 or not r.content:
            return []
        fp = feedparser.parse(r.content)
        items = []
        for it in (fp.entries or [])[:6]:
            title = (getattr(it, "title", "") or "").strip()
            link  = (getattr(it, "link", "") or "").strip()
            published = (getattr(it, "published", "") or getattr(it, "updated", "") or getattr(it, "created", "") or "").strip()
            if title and link:
                items.append({"title": title, "link": link, "published": published})
        return items
    except Exception:
        return []

def _pull_rss_or_news(company: Optional[str], domain: Optional[str], website: Optional[str]) -> List[Dict]:
    posts: List[Dict] = []
    if website:
        try:
            r = _http_get_with_retry(website, headers=UA_HEADERS, allow_redirects=True)
            if r.status_code < 400 and r.text:
                for feed_url in _try_extract_rss_links(r.text, website):
                    posts.extend(_fetch_feed_items(feed_url))
                    if len(posts) >= MAX_POSTS:
                        return posts[:MAX_POSTS]
        except Exception:
            pass

    q = (company or domain or "") or ""
    if q:
        def _news_pass(pass_gl: str):
            try:
                news = serper_news(q, num=8, gl=pass_gl)
            except Exception:
                return []
            out=[]
            for it in (news.get("news") or []) + (news.get("organic") or []):
                title = (it.get("title") or "").strip()
                link  = (it.get("link") or "").strip()
                date  = (it.get("date") or it.get("published") or "").strip()
                if title and link:
                    out.append({"title": title, "link": link, "published": date})
                    if len(out) >= MAX_POSTS:
                        break
            return out

        def _web_pass(pass_gl: str):
            try:
                data = serper_search(f"{q} news", num=10, gl=pass_gl)
            except Exception:
                return []
            out=[]
            for it in (data.get("organic") or []):
                title = (it.get("title") or "").strip()
                link  = (it.get("link") or "").strip()
                if title and link:
                    out.append({"title": title, "link": link, "published": it.get('date') or ""})
                    if len(out) >= MAX_POSTS:
                        break
            return out

        gl_pref = "pk" if (domain or "").endswith(".pk") else os.getenv("SERPER_GL","us").lower()
        posts.extend(_news_pass(gl_pref))
        if not posts and gl_pref != "us":
            posts.extend(_news_pass("us"))
        if not posts:
            posts.extend(_web_pass(gl_pref))
            if not posts and gl_pref != "us":
                posts.extend(_web_pass("us"))

    return posts[:MAX_POSTS]

def _build_prompt(company: str, domain: str, website: Optional[str], posts: List[Dict]) -> str:
    parts = []
    if company: parts.append(f"Company: {company}")
    if domain:  parts.append(f"Domain: {domain}")
    if website: parts.append(f"Website: {website}")
    if posts:
        parts.append("Recent items:")
        for p in posts[:MAX_POSTS]:
            parts.append(f"- {p.get('title','').strip()} ({p.get('published','')}) — {p.get('link','')}")
    return "\n".join(parts)

def build_summary_from_posts(company: str, domain: str, website: Optional[str], posts: List[Dict]) -> str:
    prompt = _build_prompt(company, domain, website, posts)
    summary = ""
    try:
        if summarize_text:
            summary = summarize_text(prompt, word_bounds=(SUMMARY_MIN, SUMMARY_MAX)) or ""
    except Exception:
        summary = ""
    if not summary:
        try:
            summary = gemini_summarize(prompt) or ""
        except Exception:
            summary = ""
    summary = _enforce_word_window(summary, SUMMARY_MIN, SUMMARY_MAX)

    if len(summary.split()) < SUMMARY_MIN:
        try:
            retry = gemini_summarize(prompt + "\n\nWrite a concise ~120-word executive summary (neutral tone).") or ""
            if retry:
                summary = _enforce_word_window(retry, SUMMARY_MIN, SUMMARY_MAX)
        except Exception:
            pass
    return summary

def get_recent_posts(company: str, domain: str, website: Optional[str]) -> List[Dict]:
    items = _pull_rss_or_news(company, domain, website)[:MAX_POSTS]
    if items:
        return items
    return [{
        "source": None,
        "title": "No Posts to Show",
        "url": None,
        "published": None,
        "placeholder": True
    }]
