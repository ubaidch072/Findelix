import os, time, re
import requests
import phonenumbers
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Tuple
import extruct
from w3lib.html import get_base_url
from .serper import serper_search, get_company_kg, normalize_domain

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120 Safari/537.36 Findelix/1.0",
    "Accept-Language": "en-US,en;q=0.9",
}

_SKIP_HOST_PREFIXES = ("open.", "player.", "play.", "shop.", "store.")
EMAIL_RE = re.compile(r'(?<![\w.-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])')
SNIPPET_PHONE_RE = re.compile(r'(\+?\d[\d\s().-]{7,})(?!\S)')

_BAD_ADDR_TOKENS = (
    "terms","cookie","policy","privacy","imprint","copyright",
    "wishlist","orders","returns","sale","discount","rs","%","deal","cart"
)
BRAND_NOISE_WORDS = {"cart","wishlist","orders","returns","sale","discount","deal","reel","shorts","noodle","pasta"}

def _session():
    s = requests.Session()
    s.headers.update(UA_HEADERS)
    s.max_redirects = 5
    return s

def _brand_from_domain(d: str) -> str:
    d = normalize_domain(d or "")
    return d.split(".")[0] if d else ""

def _same_brand_email(email: str, domain: str) -> bool:
    try:
        host = email.split("@",1)[1].lower()
    except Exception:
        return False
    target = normalize_domain(domain) if domain else ""
    brand = _brand_from_domain(target)
    return (target and host.endswith(target)) or (brand and host.startswith(brand))

def _norm_phone(candidate: str):
    candidate = (candidate or "").replace("\u00a0", " ").strip()
    candidate = re.sub(r"^(?:tel|phone)[:\s-]+", "", candidate, flags=re.I)
    try:
        num = phonenumbers.parse(candidate, None)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass
    return None

def _dedupe_addresses(addr_list):
    seen = set(); out = []
    for a in addr_list:
        v = (a.get("value") or "").strip()
        if v and v not in seen:
            seen.add(v); out.append(a)
    return out

def _is_plausible_address(txt: str) -> bool:
    if not txt:
        return False
    t = " ".join(txt.split()).strip(", ").strip()
    if len(t) < 10 or len(t) > 200:
        return False
    if ";" in t:
        return False
    low = t.lower()
    if any(b in low for b in _BAD_ADDR_TOKENS):
        return False
    if "," not in t:
        return False
    if not any(c.isdigit() for c in t):
        return False
    parts = [p.strip() for p in t.split(",") if p.strip()]
    if len(parts) < 2:
        return False
    tail = parts[-1]
    if len(tail) < 3 and tail.upper() not in {"US","USA","UK","UAE","EU"}:
        return False
    nonlatin = sum(1 for ch in t if ord(ch) > 127)
    if nonlatin > len(t) * 0.25:
        return False
    if any(w in low for w in BRAND_NOISE_WORDS):
        return False
    return True

def _extract_from_structured(html: str, url: str) -> Tuple[set, set, list]:
    emails, phones, addresses = set(), set(), []
    try:
        data = extruct.extract(
            html,
            base_url=get_base_url(html, url),
            syntaxes=["json-ld","microdata","rdfa"],
            errors="log",
        )
    except Exception:
        return emails, phones, addresses

    def _iter_objs(payload):
        if isinstance(payload, dict):
            g = payload.get("@graph")
            if isinstance(g, list):
                for x in g: yield x
            else:
                yield payload
        elif isinstance(payload, list):
            for x in payload: yield x

    for syntax in ("json-ld","microdata","rdfa"):
        for obj in data.get(syntax, []) or []:
            for node in _iter_objs(obj):
                addr = node.get("address")
                def _push_addr(a):
                    parts = [
                        a.get("streetAddress",""),
                        a.get("addressLocality",""),
                        a.get("addressRegion",""),
                        a.get("postalCode",""),
                        a.get("addressCountry",""),
                    ]
                    val = ", ".join([p for p in parts if p]).strip(", ")
                    if val and _is_plausible_address(val):
                        addresses.append({"value": val, "source": url})

                if isinstance(addr, dict):
                    _push_addr(addr)
                elif isinstance(addr, list):
                    for a in addr:
                        if isinstance(a, dict):
                            _push_addr(a)

                tel = node.get("telephone")
                if tel:
                    n = _norm_phone(tel)
                    if n: phones.add(n)

                mail = node.get("email")
                if mail:
                    for m in EMAIL_RE.findall(str(mail)):
                        emails.add(m.lower())
    return emails, phones, addresses

def _parse_contacts_from_html(html: str, url: str):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","svg","noscript","path"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    s_emails, s_phones, s_addresses = _extract_from_structured(html, url)

    emails = set(s_emails)
    for m in EMAIL_RE.finditer(text or ""):
        emails.add(m.group(1).lower())

    phones = set(s_phones)
    for m in phonenumbers.PhoneNumberMatcher(text or "", None):
        try:
            num = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
            if len(num) >= 8: phones.add(num)
        except Exception:
            pass
    for a in soup.select('a[href^="tel:"]'):
        cand = a.get("href","")[4:]
        n = _norm_phone(cand)
        if n: phones.add(n)

    addresses = list(s_addresses)
    if not addresses:
        m = re.search(
            r"(\d{1,6}\s+[^\n,]{4,60},\s*[A-Za-z\-\s]{2,40}(?:,\s*[A-Za-z\-\s]{2,40})?(?:,\s*[A-Z]{2}\s*\d[\dA-Z\- ]+)?)",
            text,
        )
        if m:
            val = " ".join(m.group(1).split())
            if _is_plausible_address(val):
                addresses.append({"value": val, "source": url})

    addresses = [a for a in addresses if _is_plausible_address(a.get("value"))]
    return emails, phones, addresses

def _wiki_headquarters(company_or_domain: str) -> List[Dict]:
    try:
        q = (company_or_domain or "").strip()
        brand = ""
        if q:
            if "." in q:
                brand = normalize_domain(q).split(".")[0]
            else:
                brand = q.split()[0]
        queries = []
        if q:
            queries.append(f"{q} site:wikipedia.org")
        if brand and brand.lower() != q.lower():
            queries += [f"{brand} site:wikipedia.org", f"{brand} group site:wikipedia.org", f"{brand} (company) site:wikipedia.org"]

        sess = _session()
        LABELS = {"headquarters","headquarter","headquarters location","head office","head office location","hq"}
        for qq in queries:
            data = serper_search(qq, num=3)
            for it in (data.get("organic") or []):
                link = (it.get("link") or "")
                if "wikipedia.org" not in link:
                    continue
                r = _http_get_with_retry(sess, link, allow_redirects=True)
                if r.status_code >= 400 or not r.text:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                heading = (soup.select_one("#firstHeading") or soup.title or None)
                heading_text = (heading.get_text(" ", strip=True) if heading else "").lower()
                # brand gate (parent-company pages reject)
                if brand and brand.lower() not in heading_text:
                    continue
                box = soup.select_one("table.infobox")
                if not box:
                    continue
                for row in box.select("tr"):
                    th=row.find("th"); td=row.find("td")
                    if not th or not td:
                        continue
                    label = th.get_text(" ", strip=True).lower()
                    if any(k in label for k in LABELS):
                        val = td.get_text(" ", strip=True)
                        if _is_plausible_address(val):
                            return [{"value": val, "source": link}]
    except Exception:
        pass
    return []

# --- NEW: snippet-based address finder (fallback) -----------------------------
_ADDR_REGEX = re.compile(
    r"(\d{1,5}[^\n,]{4,60},\s*[A-Za-z\-\s]{2,40}(?:,\s*[A-Za-z\-\s]{2,40}){0,2}(?:,\s*[A-Za-z\-\s]{2,40})?)"
)

def _addresses_from_snippets(company: str, domain: str) -> List[Dict]:
    queries = []
    qbase = company or domain or ""
    if qbase:
        queries += [
            f"{qbase} head office address",
            f"{qbase} headquarters address",
            f"{qbase} registered office address",
            f"{domain} address",
            f"{domain} head office",
        ]
    out: List[Dict] = []
    for q in queries:
        try:
            data = serper_search(q, num=8)
        except Exception:
            continue
        for it in (data.get("organic") or []):
            text = f"{it.get('title','')} {it.get('snippet','')}"
            for m in _ADDR_REGEX.finditer(text):
                val = " ".join(m.group(1).split())
                if _is_plausible_address(val):
                    out.append({"value": val, "source": it.get("link")})
                    if len(out) >= 3:
                        return out
    return out
# -----------------------------------------------------------------------------

def get_contacts(domain: str, website: str):
    emails, phones, addresses = set(), set(), []
    sess = _session()

    # KG quick wins
    kg = get_company_kg(domain or website or "")
    if kg.get("phone"):
        p = _norm_phone(str(kg["phone"]))
        if p: phones.add(p)
    if kg.get("address"):
        if _is_plausible_address(kg["address"]):
            addresses.append({"value": kg["address"], "source": "google_kg"})

    # SERP snippets (emails/phones)
    def snippet_pick_many(queries, pattern):
        out=[]
        for q in queries:
            try:
                data = serper_search(q, num=8)
                for it in (data.get("organic") or []):
                    text = f"{it.get('title','')} {it.get('snippet','')}"
                    for m in re.finditer(pattern, text):
                        out.append(m.group(1) if m.lastindex else m.group(0))
            except Exception:
                continue
        return out

    if domain:
        for em in snippet_pick_many(
            [f"{domain} press email", f"{domain} media email", f"{domain} contact email"],
            EMAIL_RE):
            emails.add(em.lower())
        for cand in snippet_pick_many(
            [f"{domain} contact phone", f"{domain} support phone",
             f"{domain} press phone", f"{domain} headquarters phone"],
            SNIPPET_PHONE_RE):
            n = _norm_phone(cand)
            if n: phones.add(n)

    # crawl corporate pages
    pages=[]
    if website:
        base = website.rstrip("/")
        pages += [
            base,
            urljoin(base,"/contact"),
            urljoin(base,"/contact-us"),
            urljoin(base,"/contacts"),
            urljoin(base,"/about"),
            urljoin(base,"/press"),
            urljoin(base,"/newsroom"),
            urljoin(base,"/investors"),
            urljoin(base,"/impressum"),
            urljoin(base,"/legal"),
            urljoin(base,"/company"),
        ]
        parsed = urlparse(base)
        scheme, host = parsed.scheme, parsed.netloc
        for sub in ("newsroom","investors"):
            pages.append(f"{scheme}://{sub}.{host}/")

    seen=set()
    for url in pages:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            r = _http_get_with_retry(sess, url, allow_redirects=True)
            if r.status_code >= 400 or not r.text:
                continue
            host = urlparse(r.url).netloc.lower()
            if host.startswith(_SKIP_HOST_PREFIXES):
                e, p, _ = _extract_from_structured(r.text, r.url)
                emails |= e; phones |= p
                continue
            e, p, a = _parse_contacts_from_html(r.text, r.url)
            emails |= e; phones |= p; addresses.extend(a)
            if emails and phones and addresses:
                break
        except Exception:
            continue

    if not addresses:
        addresses.extend(_wiki_headquarters(domain or website or ""))

    if not addresses:
        addresses.extend(_addresses_from_snippets(domain, domain))

    dom_for_check = domain or (urlparse(website).netloc if website else "")
    emails = {e for e in emails if _same_brand_email(e, dom_for_check)}

    result = {
        "emails": sorted({e.strip().lower() for e in emails}),
        "phones": sorted(phones),
        "addresses": _dedupe_addresses([a for a in addresses if _is_plausible_address(a.get("value"))])[:3],
    }

    d = (domain or "").strip()
    if d and not result["emails"]:
        result["emails"] = [f"info@{normalize_domain(d)}"]

    return result
