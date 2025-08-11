
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import extruct
from w3lib.html import get_base_url
from .serper import serper_search, normalize_domain

UA = {"User-Agent":"Findelix/1.0 (+https://example.com/findelix)"}

PLATFORMS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "linkedin.com": "linkedin",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "youtube.com": "youtube",
}

def _get(url):
    return requests.get(url, headers=UA, timeout=15)

def canonicalize(url: str) -> str:
    try:
        u = urlparse(url)
        path = u.path.rstrip("/")
        return f"{u.scheme}://{u.netloc}{path}"
    except Exception:
        return url

def get_socials(company: str, domain: str):
    website = None
    links = { "website": None, "instagram": None, "facebook": None, "linkedin": None, "twitter": None, "youtube": None }

    if domain:
        website = f"https://{normalize_domain(domain)}"

    if website:
        try:
            r = _get(website)
            if r.status_code < 400 and r.text:
                html = r.text
                _merge(links, extract_from_jsonld(html, website))
                _merge(links, extract_from_links(html, website))
        except Exception:
            pass

    qset = set()
    if company:
        for p in ["site:linkedin.com/company", "site:x.com", "site:twitter.com", "site:instagram.com", "site:facebook.com", "site:youtube.com"]:
            qset.add(f"{company} {p}")
        qset.add(f"{company} official site")
    if domain:
        qset.add(domain)

    for q in qset:
        data = serper_search(q, num=10)
        for it in data.get("organic", []):
            link = it.get("link","")
            if not link: continue
            plat = classify_platform(link)
            if plat:
                if any(x in link.lower() for x in ["/status","/with_replies","/watch","/reel","/video","/photos","/about","/life","/careers","/jobs"]):
                    continue
                link = canonicalize(link)
                if not links.get(plat):
                    links[plat] = link
            if not website and looks_like_official_site(link, domain):
                website = canonicalize(link)

    links["website"] = website or None
    return {"website": website, "links": {k:v for k,v in links.items() if v}}

def classify_platform(url: str):
    u = urlparse(url).netloc.lower()
    for host, key in PLATFORMS.items():
        if host in u:
            return key
    return None

def looks_like_official_site(url: str, domain: str):
    if not url: return False
    return domain and normalize_domain(domain) in url

def extract_from_jsonld(html: str, base_url: str):
    out = {}
    try:
        data = extruct.extract(html, base_url=get_base_url(html, base_url), syntaxes=['json-ld'], errors='log')
        items = data.get('json-ld', []) or []
        for obj in items:
            same = obj.get("sameAs") or []
            if isinstance(same, str): same = [same]
            for url in same:
                plat = classify_platform(url)
                if plat and plat not in out:
                    out[plat] = canonicalize(url)
    except Exception:
        pass
    return out

def extract_from_links(html: str, base_url: str):
    out = {}
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select("a[href]"):
        href = tag.get("href") or ""
        plat = classify_platform(href)
        if plat:
            url = canonicalize(urljoin(base_url, href))
            if plat not in out:
                l = url.lower()
                if any(x in l for x in ["/status","/with_replies","/watch","/reel","/video","/photos","/about","/life","/careers","/jobs"]):
                    continue
                out[plat] = url
    return out

def _merge(target, src):
    for k,v in (src or {}).items():
        if v and not target.get(k):
            target[k] = v
