from typing import Optional, Tuple, Dict, List, Set
import os, time, re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
import extruct
from w3lib.html import get_base_url
from .serper import serper_search, normalize_domain, get_company_kg

DEFAULT_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "1"))

def _http_get_with_retry(session: requests.Session, url: str, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = DEFAULT_TIMEOUT
    last_err = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            return session.get(url, **kwargs)
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    raise last_err

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36 Findelix/1.0"),
    "Accept-Language": "en-US,en;q=0.9",
}

PLATFORMS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "linkedin.com": "linkedin",
    "x.com": "twitter",
    "twitter.com": "twitter",
}
_NOISY_PATH_PARTS = ("/status","/with_replies","/watch","/reel","/video","/shorts",
                     "/photos","/about","/life","/careers","/jobs","/events")

_BAD_SEGMENTS = {"sellercenter","life","university","careers","jobs","about"}

def _session() -> requests.Session:
    s = requests.Session(); s.headers.update(UA_HEADERS); s.max_redirects = 5; return s

def canonicalize(url: str) -> str:
    try:
        u = urlparse(url)
        path = u.path.rstrip("/") if u.path != "/" else "/"
        return urlunparse((u.scheme, u.netloc, path, "", "", ""))
    except Exception:
        return (url or "").strip()

def classify_platform(url: str):
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    for k, v in PLATFORMS.items():
        if k in host: return v
    return None

def looks_like_official_site(url: str, domain: str):
    if not url or not domain: return False
    host = (urlparse(url).netloc or "").lower()
    if any(s in host for s in PLATFORMS.keys()): return False
    return host.endswith(normalize_domain(domain))

def _handle_from_url(url: str) -> str:
    try:
        path = (urlparse(url).path or "/").strip("/")
        seg = path.split("/", 1)[0]
        return seg.lower()
    except Exception:
        return ""

def extract_from_jsonld(html: str, base_url: str) -> Tuple[Dict[str, str], Optional[str]]:
    def _safe_extract_structured_data():
        try:
            base = get_base_url(html, base_url)
            return extruct.extract(html, base_url=base, syntaxes=["json-ld"], uniform=True, errors="ignore") or {}
        except Exception:
            return {}
    def _iter_jsonld_objects(data: dict):
        items = data.get("json-ld") or []
        for obj in items:
            if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                for g in obj["@graph"]: yield g
            else:
                yield obj
    out, site = {}, None
    data = _safe_extract_structured_data()
    for node in _iter_jsonld_objects(data):
        try:
            typ = node.get("@type")
            types = [typ] if isinstance(typ, str) else (typ or [])
            if ("Organization" in types) or node.get("sameAs") or node.get("url"):
                same = node.get("sameAs") or []
                if isinstance(same, str): same = [same]
                for url in same:
                    plat = classify_platform(url)
                    if plat and plat not in out: out[plat] = canonicalize(url)
                for key in ("url", "mainEntityOfPage"):
                    val = node.get(key)
                    if isinstance(val, str) and val.startswith(("http://", "https://")) and not site:
                        site = canonicalize(val)
        except Exception:
            continue
    return out, site

def extract_from_links(html: str, base_url: str):
    out = {}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return out
    for tag in soup.select("a[href]"):
        href = tag.get("href") or ""
        url = urljoin(base_url, href)
        plat = classify_platform(url)
        if not plat: continue
        low = url.lower()
        if any(p in low for p in _NOISY_PATH_PARTS): continue
        url = canonicalize(url)
        seg = _handle_from_url(url)
        if seg and seg not in _BAD_SEGMENTS and plat not in out:
            out[plat] = url
    return out

def _merge_set(target: Dict[str, Set[str]], src: Dict[str, str]):
    for k, v in (src or {}).items():
        if not v: continue
        target.setdefault(k, set()).add(v)

def _score_handle(url: str, brand: str, is_pk: bool) -> int:
    seg = _handle_from_url(url)
    s = 0
    if seg == brand: s += 6
    if seg.startswith(brand): s += 4
    if brand in seg: s += 3
    if is_pk and ("pk" in seg or "pakistan" in seg): s += 2
    if any(bad in seg for bad in _BAD_SEGMENTS): s -= 3
    return s

def _pick_best(cands: Set[str], brand: str, is_pk: bool) -> Optional[str]:
    if not cands: return None
    best = None; best_s = -10**9
    for u in cands:
        sc = _score_handle(u, brand, is_pk)
        if sc > best_s:
            best_s = sc; best = u
    return best

def get_socials(company: str, domain: str):
    website = None
    brand = normalize_domain(domain or "").split(".")[0] if domain else (company or "").split()[0].lower()
    is_pk = (domain or "").endswith(".pk")

    if domain:
        website = f"https://{normalize_domain(domain)}"

    sess = _session()

    # Collect candidates from multiple sources
    cands: Dict[str, Set[str]] = {"instagram": set(), "facebook": set(), "twitter": set(), "linkedin": set()}

    # 1) homepage JSON-LD + anchors
    if website:
        try:
            r = _http_get_with_retry(sess, website, allow_redirects=True)
            if r and r.status_code < 400 and r.text:
                html = r.text
                social_from_jsonld, site_from_jsonld = extract_from_jsonld(html, website)
                if site_from_jsonld and looks_like_official_site(site_from_jsonld, domain):
                    website = site_from_jsonld
                _merge_set(cands, social_from_jsonld)
                _merge_set(cands, extract_from_links(html, website))
        except Exception:
            pass

    # 1b) newsroom/investors anchors
    if website:
        try:
            parsed = urlparse(website)
            scheme, host = parsed.scheme, parsed.netloc
            for root in (f"{scheme}://newsroom.{host}/", f"{scheme}://investors.{host}/"):
                rr = _http_get_with_retry(sess, root, allow_redirects=True)
                if rr and rr.status_code < 400 and rr.text:
                    _merge_set(cands, extract_from_links(rr.text, root))
        except Exception:
            pass

    # 1c) Google KG socials
    try:
        kg = get_company_kg(domain or company or "")
        for plat, url in (kg.get("socials") or {}).items():
            if plat in cands:
                cands[plat].add(canonicalize(url))
    except Exception:
        pass

    # 2) Serper fallback — PK preference, gather many then rank
    qset = set()
    base_terms = [company, brand, f"{brand} pakistan", f"{brand} pk", f"{brand} official"]
    for b in filter(None, base_terms):
        for p in [
            "site:linkedin.com/company", "site:x.com", "site:twitter.com",
            "site:instagram.com", "site:facebook.com",
            "official twitter", "official instagram", "official linkedin", "official facebook",
        ]:
            qset.add(f"{b} {p}")

    def _serper_pass(pass_gl: str):
        for q in qset:
            try:
                data = serper_search(q, num=10, gl=pass_gl)
            except Exception:
                continue
            for it in (data.get("organic") or []):
                link = (it.get("link") or "").strip()
                if not link: continue
                plat = classify_platform(link)
                if not plat or plat not in cands: continue
                low = link.lower()
                if any(p in low for p in _NOISY_PATH_PARTS): continue
                cands[plat].add(canonicalize(link))

    gl_pref = "pk" if is_pk else os.getenv("SERPER_GL","us").lower()
    try:
        _serper_pass(gl_pref)
        if gl_pref != "us":
            _serper_pass("us")
    except Exception:
        pass

    # 3) Final guesses for .pk brands if still empty
    if is_pk:
        guess_map = {
            "facebook": [brand, f"{brand}pk", f"{brand}.pk", f"{brand}_pk", f"{brand}pakistan"],
            "instagram": [brand, f"{brand}.pk", f"{brand}_pk", f"{brand}pk", f"{brand}pakistan"],
            "twitter": [brand, f"{brand}pk", f"{brand}_pk", f"{brand}pakistan"],
            "linkedin": [f"company/{brand}-pakistan", f"company/{brand}-pk", f"company/{brand}"],
        }
        bases = {
            "facebook": "https://www.facebook.com/",
            "instagram": "https://www.instagram.com/",
            "twitter": "https://x.com/",
            "linkedin": "https://www.linkedin.com/",
        }
        for plat, pats in guess_map.items():
            if not cands[plat]:
                base = bases[plat]
                # just add first plausible guess (no HTTP check, some sites block bots)
                cands[plat].add(canonicalize(base + pats[0]))

    # Pick best per platform
    picked = {}
    for plat, urls in cands.items():
        best = _pick_best(urls, brand, is_pk)
        if best:
            picked[plat] = best

    return {"website": website, "links": picked}
