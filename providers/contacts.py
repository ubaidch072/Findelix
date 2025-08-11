# providers/contacts.py
import re, requests, phonenumbers
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import extruct
from w3lib.html import get_base_url
from .serper import serper_search

UA = {"User-Agent": "Findelix/1.0 (+https://example.com/findelix)"}

EMAIL_RE = re.compile(r'(?<![\w.-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])')

# very loose phone capture for snippets; we still validate with phonenumbers
SNIPPET_PHONE_RE = re.compile(r'(\+?\d[\d\s().-]{7,})')

def _get(url, timeout=20):
    return requests.get(url, headers=UA, timeout=timeout)

def _norm_phone(candidate: str):
    candidate = (candidate or "").replace("\u00a0", " ").strip()
    try:
        num = phonenumbers.parse(candidate, None)  # auto-detect region
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass
    return None

def get_contacts(domain: str, website: str):
    emails, phones, addresses = set(), set(), []

    # ---------- 1) Google snippet pass (fast) ----------
    def snippet_pick_many(queries, pattern):
        out = []
        for q in queries:
            try:
                data = serper_search(q, num=8)
                for it in data.get("organic", []) or []:
                    text = f"{it.get('title','')} {it.get('snippet','')}"
                    for m in re.finditer(pattern, text):
                        out.append(m.group(1) if m.lastindex else m.group(0))
            except Exception:
                pass
        return out

    # emails
    for em in snippet_pick_many(
        [f"{domain} press email", f"{domain} media email", f"{domain} contact email"],
        EMAIL_RE
    ):
        emails.add(em)

    # phones (try many query phrasings)
    phone_candidates = snippet_pick_many(
        [
            f"{domain} contact phone",
            f"{domain} customer service phone",
            f"{domain} support phone",
            f"{domain} press phone",
            f"{domain} media phone",
            f"{domain} investor relations phone",
            f"{domain} headquarters phone",
        ],
        SNIPPET_PHONE_RE
    )
    for cand in phone_candidates:
        normalized = _norm_phone(cand)
        if normalized:
            phones.add(normalized)

    # address quick hit
    addr_hit = None
    try:
        data = serper_search(f"{domain} headquarters address", num=6)
        for it in data.get("organic", []) or []:
            snippet = f"{it.get('title','')} {it.get('snippet','')}"
            m = re.search(r"(\d{1,6}\s+[^\n,]{4,60},\s*[A-Za-z\-\s]{2,40}(?:,\s*[A-Za-z\-\s]{2,40})?(?:,\s*[A-Z]{2}\s*\d[\dA-Z\- ]+)?)", snippet)
            if m:
                addr_hit = m.group(1).strip()
                break
    except Exception:
        pass
    if addr_hit:
        addresses.append({"value": addr_hit, "source": "search"})

    # ---------- 2) Page scraping fallback (deeper) ----------
    pages = []
    if website:
        base = website.rstrip("/")
        pages.extend([
            base,
            urljoin(base, "/contact"),
            urljoin(base, "/contact-us"),
            urljoin(base, "/contacts"),
            urljoin(base, "/about"),
            urljoin(base, "/press"),
            urljoin(base, "/newsroom"),
            urljoin(base, "/investors"),
            urljoin(base, "/impressum"),
            urljoin(base, "/legal"),
            urljoin(base, "/company"),
        ])

    if domain:
        # find more pages via search
        for q in [
            f"site:{domain} contact",
            f"site:{domain} about",
            f"site:{domain} press",
            f"site:{domain} investor relations",
            f"site:{domain} media contacts",
            f"site:{domain} newsroom",
        ]:
            try:
                data = serper_search(q, num=8)
                for it in data.get("organic", []) or []:
                    link = (it.get("link") or "").strip()
                    if link:
                        pages.append(link)
            except Exception:
                pass

    seen = set()
    for url in pages:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            r = _get(url)
            if r.status_code >= 400 or not r.text:
                continue
            e, p, a = _parse_contacts_from_html(r.text, r.url)
            emails |= e
            phones |= p
            addresses.extend(a)
            # stop early if we captured at least one of each kind
            if emails and phones and addresses:
                break
        except Exception:
            continue

    return {
        "emails": sorted(emails),
        "phones": sorted(phones),
        "addresses": _dedupe_addresses(addresses)[:3],
    }

def _dedupe_addresses(addr_list):
    seen = set(); out = []
    for a in addr_list:
        v = (a.get("value") or "").strip()
        if v and v not in seen:
            seen.add(v); out.append(a)
    return out

def _parse_contacts_from_html(html: str, url: str):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","svg","noscript","path"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    # Emails
    emails = set(m.group(0) for m in EMAIL_RE.finditer(text or ""))

    # Phones from visible text (international)
    phones = set()
    for m in phonenumbers.PhoneNumberMatcher(text or "", None):
        try:
            num = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
            if len(num) >= 8:
                phones.add(num)
        except Exception:
            pass

    # Phones from tel: links (many sites only expose phone this way)
    for a in soup.select('a[href^="tel:"]'):
        cand = a.get("href", "")[4:]  # strip 'tel:'
        normalized = _norm_phone(cand)
        if normalized:
            phones.add(normalized)

    # Structured data (addresses & telephone)
    addresses = []
    try:
        data = extruct.extract(html, base_url=get_base_url(html, url),
                               syntaxes=['json-ld','microdata','rdfa'], errors='log')
        for syntax in ('json-ld','microdata','rdfa'):
            for obj in data.get(syntax, []) or []:
                addr = obj.get("address")
                if isinstance(addr, dict):
                    formatted = ", ".join([addr.get(k,"") for k in
                        ("streetAddress","addressLocality","addressRegion","postalCode","addressCountry")
                        if addr.get(k)]).strip(", ")
                    if formatted:
                        addresses.append({"value": formatted, "source": url})
                tel = obj.get("telephone") or (
                    obj.get("contactPoint",{}).get("telephone")
                    if isinstance(obj.get("contactPoint"), dict) else None
                )
                if tel:
                    normalized = _norm_phone(tel)
                    if normalized:
                        phones.add(normalized)
    except Exception:
        pass

    # Heuristic address fallback if none detected
    if not addresses:
        m = re.search(r"(\d{1,6}\s+[^\n,]{4,60},\s*[A-Za-z\-\s]{2,40}(?:,\s*[A-Za-z\-\s]{2,40})?(?:,\s*[A-Z]{2}\s*\d[\dA-Z\- ]+)?)", text)
        if m:
            addresses.append({"value": m.group(1).strip(), "source": url})

    return emails, phones, addresses
