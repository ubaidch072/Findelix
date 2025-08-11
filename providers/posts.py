
import requests, feedparser
from urllib.parse import urljoin
from .serper import serper_news, gemini_summarize

UA = {"User-Agent":"Findelix/1.0 (+https://example.com/findelix)"}

def _get(url):
    return requests.get(url, headers=UA, timeout=12)

def get_recent_posts_and_summary(company: str, domain: str, website: str):
    posts = []

    if website:
        for path in ["/newsroom","/news","/press","/blog","/stories"]:
            url = urljoin(website.rstrip("/"), path)
            try:
                r = _get(url)
                if r.status_code < 400:
                    for rss in ["/feed","/rss","/atom.xml","/index.xml"]:
                        rr = _get(url.rstrip("/") + rss)
                        if rr.status_code < 400 and "xml" in rr.headers.get("Content-Type","").lower():
                            feed = feedparser.parse(rr.text)
                            for e in feed.entries[:6]:
                                posts.append({"source":"blog","title": e.get("title"), "url": e.get("link"), "published": str(e.get("published",""))})
                            break
            except Exception:
                pass

    q = company or domain or ""
    if q and not posts:
        data = serper_news(q, num=8)
        for n in data.get("news", []):
            posts.append({"source":"news","title": n.get("title"), "url": n.get("link"), "published": n.get("date")})

    summary = ""
    if posts:
        bullets = "\n".join([f"- {p['title']}" for p in posts[:8] if p.get("title")])
        prompt = f"Summarize the following recent items about {company or domain}. Write 120-150 words, neutral tone, focus on official product/feature announcements, partnerships, or financial updates. Avoid unrelated celebrity news.\n\n{bullets}"
        summary = gemini_summarize(prompt) or ""
    else:
        prompt = f"In 120-150 words, provide a neutral overview of {company or domain}: what it does, products/services, market position, and very recent developments if any."
        summary = gemini_summarize(prompt) or ""

    return {"posts": posts[:10], "summary": summary}
