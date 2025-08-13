from typing import Optional, List, Dict
import os, time, re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

from .serper import serper_search, get_company_kg

# ---- HTTP behavior -----------------------------------------------------------
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
# -----------------------------------------------------------------------------

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36 Findelix/1.0"),
    "Accept-Language": "en-US,en;q=0.9",
}

TITLE_HINTS = (
    "chief","ceo","cto","cfo","coo","cmo","cio","vp","svp","evp",
    "president","founder","co-founder","chair","chairman","director","head","board"
)
LEADERSHIP_PATHS = (
    "/leadership","/team","/management","/executive-team","/leadership-team","/leadership/","/board",
    "/board-of-directors","/about/leadership","/about/team","/about/company","/company/leadership",
    "/company/team","/people","/who-we-are","/our-team","/investors/corporate-governance"
)

STOPNAME = {"save","more","sell","support","help","center","contact","customer","manage","account",
            "orders","wishlist","reviews","returns","care","buy","all","login","signup","track","cart","shop","deals"}

NAME_VERB_STOPS = {"succeeds","appointed","appoints","joins","leaves","replaces","to","as","the","is","was","will","has","he","she"}

def _clip_name_tokens(s: str) -> Optional[str]:
    toks = re.findall(r"[A-Z][a-z]+", s or "")
    out = []
    for t in toks:
        if t.lower() in NAME_VERB_STOPS:
            break
        out.append(t)
    if 1 < len(out) <= 4:
        return " ".join(out)
    return None

def _session():
    s = requests.Session()
    s.headers.update(UA_HEADERS)
    s.max_redirects = 5
    return s

def _normalize_title(t: Optional[str]) -> Optional[str]:
    if not t: return None
    t = re.sub(r"\s+", " ", t).strip(" :,-|\u2013\u2014")
    if len(t) > 120: t = t[:120].rstrip()
    return t or None

def _bad_ui_name(s: str) -> bool:
    low = (s or "").strip().lower()
    parts = re.split(r"\s+", low)
    return any(p in STOPNAME for p in parts)

def _looks_like_name(s: str) -> bool:
    if not s: return False
    s = s.strip()
    if len(s) < 3 or len(s) > 80: return False
    if s.isupper(): return False
    if _bad_ui_name(s): return False
    parts = re.split(r"\s+", s)
    return (1 <= len(parts) <= 5) and parts[0][0].isupper()

def _rank(person: Dict) -> int:
    t = (person.get("job_title") or "").lower()
    if "ceo" in t: return 0
    if "chief" in t: return 1
    if "president" in t: return 2
    if any(k in t for k in ("cfo","cto","coo","cmo","cio")): return 3
    if "chair" in t or "board" in t or "director" in t: return 4
    if "vp" in t or "svp" in t or "evp" in t: return 5
    if "founder" in t: return 6
    return 7

def _uniq(items: List[Dict]) -> List[Dict]:
    seen=set(); out=[]
    for p in items:
        key=((p.get("name") or "").lower(), (p.get("job_title") or "").lower())
        if p.get("name") and key not in seen:
            seen.add(key)
            out.append({
                "name": p.get("name"),
                "job_title": _normalize_title(p.get("job_title")),
                "linkedin": p.get("linkedin")
            })
    return out[:12]

def _discover_leadership_urls(domain: str, website: Optional[str]) -> List[str]:
    urls: List[str] = []
    host = urlparse(website).netloc if website else domain
    queries = [
        f"site:{host} leadership", f"site:{host} team", f"site:{host} executive team",
        f"site:{host} board of directors", f"site:{host} management"
    ]
    for q in queries:
        try:
            data = serper_search(q, num=6)
            for it in (data.get("organic") or []):
                link = (it.get("link") or "").strip()
                if link: urls.append(link)
        except Exception:
            continue
    if website:
        base = website.rstrip("/")
        parsed = urlparse(base)
        scheme, host = parsed.scheme, parsed.netloc
        for sub in ("investors","newsroom","press"):
            root = f"{scheme}://{sub}.{host}"
            urls.append(root + "/")
            for p in LEADERSHIP_PATHS:
                urls.append(urljoin(root, p))
        for p in LEADERSHIP_PATHS:
            urls.append(urljoin(base, p))
    seen=set(); out=[]
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    out = [u for u in out if not any(x in u.lower() for x in ("/help","/support","/customer","/account","/orders","/wishlist"))]
    return out[:12]

def _parse_people_from_dom(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","svg","path"]): tag.decompose()

    people: List[Dict] = []
    cards = soup.find_all(["article","div","li","section"])
    for c in cards:
        text = " ".join(c.stripped_strings)
        if not text or len(text) < 8: continue

        name = None
        for h in c.find_all(re.compile(r"^h[1-6]$")):
            nm = h.get_text(" ", strip=True)
            if _looks_like_name(nm):
                name = nm; break
        if not name:
            m = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,4})\b", text)
            cand = m.group(1) if m else None
            if cand:
                cand = _clip_name_tokens(cand) or cand
            if cand and _looks_like_name(cand):
                name = cand
        if not name: continue

        title = None
        for h in c.find_all(re.compile(r"^h[1-6]$")):
            if name in h.get_text(" ", strip=True):
                sib = h.find_next_sibling()
                if sib:
                    t = sib.get_text(" ", strip=True)
                    if t and any(k in t.lower() for k in TITLE_HINTS):
                        title = _normalize_title(t); break
        if not title:
            low = text.lower()
            if any(k in low for k in TITLE_HINTS):
                for tok in TITLE_HINTS:
                    pos = low.find(tok)
                    if pos != -1:
                        start=max(0, pos-40); end=min(len(text), pos+80)
                        title=_normalize_title(text[start:end]); break

        if not title: continue

        linkedin=None
        for a in c.find_all("a", href=True):
            href = a["href"]
            if "linkedin.com/in" in href:
                linkedin = href; break

        people.append({"name": name, "job_title": title, "linkedin": linkedin})

    return _uniq(people)

def _wikipedia_people(company_or_domain: str) -> List[Dict]:
    try:
        data = serper_search(f"{company_or_domain} site:wikipedia.org", num=3)
        sess = _session()
        for it in (data.get("organic") or []):
            link = (it.get("link") or "")
            if "wikipedia.org" not in link: continue
            r = _http_get_with_retry(sess, link, allow_redirects=True)
            if r.status_code >= 400 or not r.text: continue
            soup = BeautifulSoup(r.text, "lxml")
            box = soup.select_one("table.infobox")
            if not box: continue
            people: List[Dict] = []
            for row in box.select("tr"):
                th=row.find("th"); td=row.find("td")
                if not th or not td: continue
                label=th.get_text(" ", strip=True).lower()
                if any(k in label for k in ["key people","founders","founder","ceo","chief executive","chairman","chairperson","president"]):
                    chunks=[t for t in re.split(r"\n|•|·|;| , ", td.get_text("\n", strip=True)) if t.strip()]
                    for ch in chunks:
                        if "–" in ch:
                            nm, role = ch.split("–",1)
                        elif " - " in ch:
                            nm, role = ch.split(" - ",1)
                        else:
                            nm, role = ch, th.get_text(" ", strip=True)
                        nm=nm.strip(); role=_normalize_title(role)
                        if _looks_like_name(nm):
                            people.append({"name": nm, "job_title": role})
            if people:
                return _uniq(people)
    except Exception:
        pass
    return []

def _fill_linkedin_via_search(name: str, company: Optional[str]) -> Optional[str]:
    if not name: return None
    q = f'site:linkedin.com/in "{name}" {company or ""}'.strip()
    try:
        data = serper_search(q, num=5)
        for it in (data.get("organic") or []):
            link = (it.get("link") or "")
            if "linkedin.com/in" in link: return link
    except Exception:
        pass
    return None

def _serper_role_probe(company: str) -> List[Dict]:
    if not company: return []
    roles = ["CEO","CFO","CTO","COO","CMO","CIO","President","Chairman","Chairperson"]
    people: List[Dict] = []
    for role in roles:
        q = f"{company} {role}"
        try:
            data = serper_search(q, num=5)
        except Exception:
            continue
        for it in (data.get("organic") or []):
            t = " ".join([(it.get("title") or ""), (it.get("snippet") or "")])
            m = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,4})\b", t)
            nm = _clip_name_tokens(m.group(1)) if m else None
            if nm:
                nm = nm.strip()
                if _looks_like_name(nm):
                    people.append({"name": nm, "job_title": role})
                    break
    return people

def get_executives(company: str, domain: str, website: Optional[str]) -> List[Dict]:
    results: List[Dict] = []
    sess = _session()

    for url in _discover_leadership_urls(domain or "", website or ""):
        try:
            r = _http_get_with_retry(sess, url, allow_redirects=True)
            if r.status_code >= 400 or not r.text: continue
            people = _parse_people_from_dom(r.text)
            results.extend(people)
            if len(results) >= 10: break
        except Exception:
            continue

    if len(results) < 6:
        results.extend(_wikipedia_people(company or domain))

    if len(results) < 6:
        results.extend(_serper_role_probe(company or (domain or "")))

    if len(results) < 6:
        kg = get_company_kg(company or domain or "")
        for p in (kg.get("executives") or []):
            if p.get("name"):
                results.append({"name": p["name"], "job_title": p.get("job_title"), "linkedin": None})

    for p in results:
        if not p.get("linkedin"):
            li = _fill_linkedin_via_search(p.get("name",""), company)
            if li: p["linkedin"] = li

    results = _uniq(results)
    for p in results:
        p["rank"] = _rank(p)
    results.sort(key=lambda x: x["rank"])
    return results[:12]
