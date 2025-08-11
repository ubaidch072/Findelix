import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import extruct
from w3lib.html import get_base_url
from .serper import serper_search, normalize_domain

UA = {"User-Agent": "Findelix/1.0 (+https://example.com/findelix)"}

PLATFORMS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "linkedin.com": "linkedin",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "youtube.com": "youtube",
}

def _get(url):
    # Slightly lower timeout; keeps bulk responsive
    return requests.get(url, headers=UA, timeout=10)

def canonicalize(url: str) -> str:
    try:
        u = urlparse(url)
        path = u.path.rstrip("/")
        return f"{u.scheme}://{u.netloc}{path}"
    except Exception:
        return url

def get_socials(company: str, domain: str):
    website = None
    links = {"website": None, "instagram": None, "facebook": None, "linkedin": None, "twitter": None, "youtube": None}

    if domain:
        website = f"https://{normalize_domain(domain)}"

    # 1) Try first-party site: JSON-LD + anchor links (safe extract)
    if website:
        try:
            r = _get(website)
            if r.status_code < 400 and r.text:
                html = r.text
                _merge(links, extract_from_jsonld(html, website))
                _merge(links, extract_from_links(html, website))
        except Exception:
            pass

    # 2) Search fallbacks for socials + canonical website
    qset = set()
    if company:
        for p in [
            "site:linkedin.com/company",
            "site:x.com",
            "site:twitter.com",
            "site:instagram.com",
            "site:facebook.com",
            "site:youtube.com",
        ]:
            qset.add(f"{company} {p}")
        qset.add(f"{company} official site")
    if domain:
        qset.add(domain)

    for q in qset:
        try:
            data = serper_search(q, num=10)
        except Exception:
            continue
        for it in data.get("organic", []):
            link = it.get("link", "")
            if not link:
                continue
            plat = classify_platform(link)
            if plat:
                # skip noisy subpaths
                if any(x in link.lower() for x in ("/status", "/with_replies", "/watch", "/reel", "/video", "/photos", "/about", "/life", "/careers", "/jobs")):
                    continue
                link = canonicalize(link)
                if not links.get(plat):
                    links[plat] = link
            if not website and looks_like_official_site(link, domain):
                website = canonicalize(link)

    links["website"] = website or None
    return {"website": website, "links": {k: v for k, v in links.items() if v}}

def classify_platform(url: str):
    try:
        u = urlparse(url).netloc.lower()
    except Exception:
        return None
    for host, key in PLATFORMS.items():
        if host in u:
            return key
    return None

def looks_like_official_site(url: str, domain: str):
    if not url:
        return False
    return bool(domain and normalize_domain(domain) in url)

# ------------ SAFE JSON-LD EXTRACTOR (fixes your crash) ----------------

def _safe_extract_structured_data(html: str, base_url: str) -> dict:
    """
    Use extruct in tolerant mode so malformed JSON-LD never crashes the run.
    Restrict syntaxes and request uniform output for simpler handling.
    """
    try:
        base = get_base_url(html, base_url)
        data = extruct.extract(
            html,
            base_url=base,
            syntaxes=["json-ld"],  # keep focused; add "microdata","opengraph" if you want more
            uniform=True,
            errors="ignore",       # <-- key fix: ignore malformed JSON
        )
        return data or {}
    except Exception:
        return {}

def extract_from_jsonld(html: str, base_url: str):
    out = {}
    data = _safe_extract_structured_data(html, base_url)
    items = data.get("json-ld", []) or []
    for obj in items:
        try:
            same = obj.get("sameAs") or []
            if isinstance(same, str):
                same = [same]
            for url in same:
                plat = classify_platform(url)
                if plat and plat not in out:
                    out[plat] = canonicalize(url)
        except Exception:
            # ignore bad objects
            continue
    return out

# -----------------------------------------------------------------------

def extract_from_links(html: str, base_url: str):
    out = {}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return out
    for tag in soup.select("a[href]"):
        href = tag.get("href") or ""
        plat = classify_platform(href)
        if plat:
            url = canonicalize(urljoin(base_url, href))
            if plat not in out:
                l = url.lower()
                if any(x in l for x in ("/status", "/with_replies", "/watch", "/reel", "/video", "/photos", "/about", "/life", "/careers", "/jobs")):
                    continue
                out[plat] = url
    return out

def _merge(target, src):
    for k, v in (src or {}).items():
        if v and not target.get(k):
            target[k] = v
