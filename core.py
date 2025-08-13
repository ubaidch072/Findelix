# core.py
import os, time
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional

from providers.socials import get_socials
from providers.contacts import get_contacts
from providers.executives import get_executives
from providers.posts import get_recent_posts, build_summary_from_posts
from providers.serper import categorize_with_gemini_or_rules, normalize_domain

DEBUG = str(os.getenv("DEBUG", "")).lower() in ("1", "true", "yes")

_ALLOWED_SOCIALS = {"facebook", "instagram", "twitter", "linkedin"}

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def _log(section: str, msg: str) -> None:
    if DEBUG:
        print(f"[core:{section}] {msg}")

def _safe_call(fn, section: str, default, *args, **kwargs):
    t0 = time.time()
    try:
        out = fn(*args, **kwargs)
        _log(section, f"ok in {int((time.time() - t0)*1000)} ms")
        return out if out is not None else default
    except Exception as e:
        _log(section, f"ERROR: {e}")
        return default

def _ensure_website(domain: str, website: Optional[str]) -> Optional[str]:
    w = (website or "").strip()
    if w:
        return w
    d = (domain or "").strip().lstrip(".")
    if not d:
        return None
    if not d.startswith("http://") and not d.startswith("https://"):
        return f"https://{d}"
    return d

def _dedupe_list(items: List[Any]) -> List[Any]:
    seen, out = set(), []
    for x in items or []:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _dedupe_dicts(items: List[Dict], key: str) -> List[Dict]:
    seen, out = set(), []
    for it in items or []:
        k = (it or {}).get(key)
        if not k:
            continue
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out

# ---- ranking helper (fallback) ----------------------------------------------
def _rank(person: Dict[str, Any]) -> int:
    t = (person.get("job_title") or "").lower()
    if "ceo" in t: return 0
    if "chief" in t: return 1
    if "president" in t: return 2
    if any(k in t for k in ("cfo","cto","coo","cmo","cio")): return 3
    if "chair" in t or "board" in t or "director" in t: return 4
    if "vp" in t or "svp" in t or "evp" in t: return 5
    if "founder" in t: return 6
    return 7
# -----------------------------------------------------------------------------

# --- very light address guard (contacts provider already strict) -------------
_BAD_ADDR_TOKENS = ("terms", "cookie", "policy", "listen", "playlist", "privacy")

def _looks_like_address(txt: str) -> bool:
    if not txt:
        return False
    t = " ".join(txt.split()).strip(", ").strip()
    if len(t) < 8 or len(t) > 200:
        return False
    low = t.lower()
    if any(b in low for b in _BAD_ADDR_TOKENS):
        return False
    return ("," in t) or any(c.isdigit() for c in t)

def _sanitize_contacts(contacts: Dict[str, Any]) -> Dict[str, Any]:
    contacts = contacts or {}
    emails = _dedupe_list([e for e in (contacts.get("emails") or []) if isinstance(e, str) and "@" in e])
    phones = _dedupe_list([p for p in (contacts.get("phones") or []) if isinstance(p, str)])
    addrs_in = contacts.get("addresses") or []
    addrs: List[Dict[str, Optional[str]]] = []
    for a in addrs_in:
        if not isinstance(a, dict):
            continue
        v = (a.get("value") or "").strip()
        s = (a.get("source") or "").strip()
        if _looks_like_address(v):
            addrs.append({"value": v, "source": s or None})
    addrs = _dedupe_dicts(addrs, key="value")
    return {"emails": emails, "phones": phones, "addresses": addrs}

def _sanitize_socials(socials: Dict[str, Any]) -> Dict[str, Any]:
    socials = socials or {}
    # keep only required platforms
    links = socials.get("links") or {}
    links = {k: v for k, v in links.items() if k in _ALLOWED_SOCIALS and v}
    return {"website": socials.get("website"), "links": links}

def _sanitize_executives(execs: List[Dict]) -> List[Dict]:
    # keep unique (name, title), attach rank, sort by rank
    out = []
    seen = set()
    for e in execs or []:
        if not isinstance(e, dict):
            continue
        name = (e.get("name") or "").strip()
        title = (e.get("job_title") or e.get("title") or "").strip() or None
        li = (e.get("linkedin") or e.get("url") or "").strip() or None
        if not name:
            continue
        key = (name.lower(), (title or "").lower())
        if key in seen:
            continue
        seen.add(key)
        rank = e.get("rank")
        if not isinstance(rank, int):
            rank = _rank({"job_title": title or ""})
        out.append({"name": name, "job_title": title, "linkedin": li, "rank": rank})
    out.sort(key=lambda x: x["rank"])
    return out[:12]

def _sanitize_posts(posts: List[Dict]) -> List[Dict]:
    """
    Keep shape compatible with providers.posts (uses 'link' key).
    """
    clean = []
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        title = (p.get("title") or "").strip()
        link = (p.get("link") or p.get("url") or "")
        link = link.strip() if isinstance(link, str) else ""
        if not title or not link:
            if p.get("placeholder"):
                clean.append(
                    {
                        "source": p.get("source"),
                        "title": "No Posts to Show",
                        "link": None,
                        "published": None,
                        "placeholder": True,
                    }
                )
            continue
        clean.append({"source": p.get("source"), "title": title, "link": link, "published": p.get("published")})
    clean = _dedupe_dicts(clean, key="link")
    return clean[:5] or [
        {"source": None, "title": "No Posts to Show", "link": None, "published": None, "placeholder": True}
    ]

def build_profile(company: str, domain: str) -> Dict:
    start = time.time()
    company = (company or "").strip()
    domain = normalize_domain((domain or "").strip())

    # ---- Socials / website ----
    raw_socials = _safe_call(
        get_socials,
        "socials",
        default={"website": _ensure_website(domain, None), "links": {}},
        company=company,
        domain=domain,
    )
    socials = _sanitize_socials(raw_socials)
    website = _ensure_website(domain, socials.get("website"))
    socials["website"] = website

    if not company and domain:
        company = domain.split(".")[0].capitalize()

    # ---- Contacts ----
    raw_contacts = _safe_call(
        get_contacts,
        "contacts",
        default={"emails": [], "phones": [], "addresses": []},
        domain=domain,
        website=website,
    )
    contacts = _sanitize_contacts(raw_contacts)
    if domain and not contacts["emails"]:
        contacts["emails"] = [f"info@{domain}"]  # last-resort

    # ---- Executives ----
    raw_execs = _safe_call(
        get_executives,
        "executives",
        default=[],
        company=company,
        domain=domain,
        website=website,
    )
    execs = _sanitize_executives(raw_execs)

    # ---- Posts & Summary ----
    raw_posts = _safe_call(
        get_recent_posts,
        "posts",
        default=[],
        company=company,
        domain=domain,
        website=website,
    )
    posts_list = _sanitize_posts(raw_posts)

    summary = _safe_call(
        build_summary_from_posts,
        "summary",
        default="",
        posts=posts_list,
        company=company,
        domain=domain,
        website=website,
    )

    # ---- Category ----
    category = _safe_call(
        categorize_with_gemini_or_rules,
        "category",
        default="Other",
        name=company,
        domain=domain,
        socials={"website": website, "links": socials.get("links")},
    )

    payload = {
        "company": company or None,
        "domain": domain or None,
        "website": website,
        "socials": {"website": website, "links": socials.get("links")},
        "contacts": contacts,
        "executives": execs,
        "summary": summary or "",
        "recent_posts": posts_list,
        "category": category,
        "generated_at": _now(),
        "latency_ms": int((time.time() - start) * 1000),
    }

    _log(
        "result",
        f"latency={payload['latency_ms']}ms "
        f"emails={len(contacts['emails'])} phones={len(contacts['phones'])} "
        f"addresses={len(contacts['addresses'])} execs={len(execs)} posts={len(posts_list)}",
    )
    return payload

def bulk_build_profiles(items: List[Tuple[str, str]]) -> List[Dict]:
    return [build_profile(n, d) for n, d in items]
