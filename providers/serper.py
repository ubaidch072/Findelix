# providers/serper.py
import os
import re
import time
import random
from typing import Dict, Any, List, Optional, Tuple

import requests

# -----------------------------
# Env / config
# -----------------------------
SERPER_KEY = os.getenv("SERPER_API_KEY", "").strip()
GOOGLE_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

# Locale for Serper (country code) and UI language
SERPER_GL = os.getenv("SERPER_GL", "us").lower()      # e.g., "us", "gb", "pk"
SERPER_HL = os.getenv("SERPER_HL", "en").lower()      # e.g., "en", "en-GB"

def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}

# Local summarizer / model
USE_LOCAL = _truthy(os.getenv("USE_LOCAL_SUMMARIZER"))
MODEL_CKPT = os.getenv("MODEL_CKPT", "mrm8488/t5-small-finetuned-summarize-news").strip()

# Gemini model (override if you like)
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()

SEARCH_URL = "https://google.serper.dev/search"
NEWS_URL   = "https://google.serper.dev/news"

HTTP_TIMEOUT = int(os.getenv("SERPER_TIMEOUT", "20"))
RETRIES      = int(os.getenv("SERPER_RETRIES", "3"))  # small backoff on transient errors
BACKOFF_BASE = 1.2
BACKOFF_CAP  = 5.0  # seconds

# cache for lazy-loaded objects
_lazy_cache: Dict[str, Any] = {}

# -----------------------------
# HTTP session & helpers
# -----------------------------
def _session() -> requests.Session:
    s = _lazy_cache.get("_sess")
    if s:
        return s  # type: ignore[return-value]
    sess = requests.Session()
    sess.headers.update({
        "X-API-KEY": SERPER_KEY,
        "Content-Type": "application/json",
        "User-Agent": "Findelix/1.0 (+https://example.com/findelix)"
    })
    _lazy_cache["_sess"] = sess
    return sess

def normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d.startswith("http://"):
        d = d[7:]
    elif d.startswith("https://"):
        d = d[8:]
    # strip credentials/paths if someone pasted a URL
    if "/" in d:
        d = d.split("/", 1)[0]
    return d.lstrip(".")

def _backoff_sleep(attempt: int) -> None:
    # exponential backoff with jitter
    delay = min(BACKOFF_CAP, (BACKOFF_BASE ** attempt)) + random.random() * 0.3
    time.sleep(delay)

def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST JSON with small retry on transient failures.
    Returns {} on failure.
    """
    if not SERPER_KEY:
        return {}
    sess = _session()
    for attempt in range(RETRIES + 1):
        try:
            r = sess.post(url, json=payload, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return {}
            # handle 4xx/5xx politely
            if r.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                _backoff_sleep(attempt + 1)
                continue
            return {}
        except requests.RequestException:
            if attempt < RETRIES:
                _backoff_sleep(attempt + 1)
                continue
            return {}
    return {}

# -----------------------------
# Serper wrappers
# -----------------------------
def serper_search(q: str, num: int = 10, gl: Optional[str] = None, hl: Optional[str] = None) -> Dict[str, Any]:
    """General web search via Serper."""
    gl = (gl or SERPER_GL).lower()
    hl = (hl or SERPER_HL).lower()
    payload = {"q": q, "num": max(1, min(num, 10)), "gl": gl, "hl": hl}
    data = _post_json(SEARCH_URL, payload)
    return data or {"organic": []}

def serper_news(q: str, num: int = 10, gl: Optional[str] = None, hl: Optional[str] = None) -> Dict[str, Any]:
    """News search via Serper."""
    gl = (gl or SERPER_GL).lower()
    hl = (hl or SERPER_HL).lower()
    payload = {"q": q, "num": max(1, min(num, 10)), "gl": gl, "hl": hl}
    data = _post_json(NEWS_URL, payload)
    return data or {"news": []}

# -----------------------------
# Summarization
# -----------------------------
def _chunk(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    # try to cut at a sentence boundary close to limit
    cut = text[: max_chars + 1]
    last_punct = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if last_punct >= int(max_chars * 0.7):
        return cut[: last_punct + 1].strip()
    return cut[:max_chars].rstrip() + "…"

def _get_local_summarizer():
    """
    Lazy-load a transformers summarization pipeline (single instance).
    Returns None if unavailable.
    """
    if "_local_summarizer" in _lazy_cache:
        return _lazy_cache["_local_summarizer"]
    try:
        from transformers import pipeline  # type: ignore
        summarizer = pipeline("summarization", model=MODEL_CKPT)
        _lazy_cache["_local_summarizer"] = summarizer
        return summarizer
    except Exception:
        _lazy_cache["_local_summarizer"] = None
        return None

def _get_gemini_model():
    """Lazy-init the Gemini model (single instance)."""
    if not GOOGLE_KEY:
        return None
    if "_gemini_model" in _lazy_cache:
        return _lazy_cache["_gemini_model"]
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=GOOGLE_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        _lazy_cache["_gemini_model"] = model
        return model
    except Exception:
        return None

def summarize_text(text: str, max_chars: int = 1200, word_bounds: Optional[Tuple[int, int]] = None) -> str:
    """
    Summarize a text blob.
      - If USE_LOCAL_SUMMARIZER is truthy -> try local transformers, then Gemini fallback.
      - Else -> try Gemini first, then local fallback.
    `word_bounds`=(min,max) optionally enforces target word count in the prompt (Gemini) and trims output.
    """
    txt = _chunk(text or "", max_chars)
    if not txt:
        return ""

    def _trim_words(s: str, bounds: Optional[Tuple[int, int]]) -> str:
        if not s or not bounds:
            return s or ""
        wmin, wmax = bounds
        words = s.split()
        if len(words) > wmax:
            s = " ".join(words[:wmax]).rstrip()
            if not s.endswith((".", "!", "?")):
                s += "."
        return s

    # Preferred path
    if USE_LOCAL:
        try:
            summarizer = _get_local_summarizer()
            if summarizer:
                out = summarizer(txt, max_length=160, min_length=24, do_sample=False)
                s = (out[0].get("summary_text") or "").strip()
                if s:
                    return _trim_words(s, word_bounds)
        except Exception:
            pass  # fall through to Gemini

        try:
            model = _get_gemini_model()
            if model:
                if word_bounds:
                    wmin, wmax = word_bounds
                    prompt = f"Summarize the following in {wmin}-{wmax} words, neutral tone:\n\n{txt}"
                else:
                    prompt = f"Summarize in 2–3 sentences:\n\n{txt}"
                resp = model.generate_content(prompt)
                s = (getattr(resp, "text", "") or "").strip()
                return _trim_words(s, word_bounds)
        except Exception:
            return ""
        return ""

    # Cloud first (Gemini), then local fallback
    try:
        model = _get_gemini_model()
        if model:
            if word_bounds:
                wmin, wmax = word_bounds
                prompt = f"Summarize the following in {wmin}-{wmax} words, neutral tone:\n\n{txt}"
            else:
                prompt = f"Summarize in 2–3 sentences:\n\n{txt}"
            resp = model.generate_content(prompt)
            s = (getattr(resp, "text", "") or "").strip()
            if s:
                return _trim_words(s, word_bounds)
    except Exception:
        pass

    try:
        summarizer = _get_local_summarizer()
        if summarizer:
            out = summarizer(txt, max_length=160, min_length=24, do_sample=False)
            s = (out[0].get("summary_text") or "").strip()
            return _trim_words(s, word_bounds)
    except Exception:
        return ""

    return ""

# --- Back-compat alias so older imports keep working ---
def gemini_summarize(prompt: str) -> str:
    """
    Compatibility shim for older code paths.
    We just delegate to summarize_text(prompt).
    Treat `prompt` as the text to summarize.
    """
    return summarize_text(prompt)

# -----------------------------
# Categorization
# -----------------------------
_RULES: List[Tuple[str, List[str]]] = [
    ("Tech", ["software", "saas", "ai", "cloud", "tech", "it", "data", "developer", "app", "platform", "stream", "music"]),
    ("Retail", ["store", "shop", "retail", "ecommerce", "fashion", "apparel"]),
    ("Health", ["health", "clinic", "medical", "pharma", "biotech", "hospital", "wellness"]),
    ("Finance", ["bank", "fintech", "trading", "investment", "insurance", "accounting"]),
    ("Education", ["school", "university", "academy", "education", "edtech", "training"]),
    ("Hospitality", ["hotel", "restaurant", "cafe", "resort", "hospitality", "food"]),
    ("Real Estate", ["real estate", "property", "realtor", "housing"]),
    ("Manufacturing", ["manufactur", "factory", "industrial", "automation", "hardware"]),
]
_ALLOWED = {c for c, _ in _RULES} | {"Other"}

def categorize_with_gemini_or_rules(name: str, domain: str, socials: Dict[str, Any]) -> str:
    """
    Fast keyword rules first; if no match, ask the model.
    Returns one of the known categories or 'Other'.
    """
    # Compose a small context string for rules
    social_keys = list((socials.get("links") or {}).keys())
    t = " ".join([name or "", domain or ""] + social_keys).lower()

    for cat, keys in _RULES:
        if any(k in t for k in keys):
            return cat

    # Ask the model only when rules don't match
    prompt = (
        "Categorize the company into one of exactly these labels: "
        "Tech, Retail, Health, Finance, Education, Hospitality, Real Estate, Manufacturing, Other. "
        f"Company: '{name}'. Domain: {domain}. "
        "Return only the label, nothing else."
    )

    # Prefer Gemini (cheap + fast); fallback to local (short prompt)
    try:
        model = _get_gemini_model()
        if model:
            r = model.generate_content(prompt)
            out = (getattr(r, "text", "") or "").strip().split()[0].strip(",.;")
            return out if out in _ALLOWED else "Other"
    except Exception:
        pass

    if USE_LOCAL:
        try:
            summarizer = _get_local_summarizer()
            if summarizer:
                # Local models are not classifiers; compress prompt and match rules again.
                s = summarizer(prompt, max_length=24, min_length=8, do_sample=False)[0]["summary_text"].lower()
                for cat, _ in _RULES:
                    if cat.lower() in s:
                        return cat
        except Exception:
            pass

    return "Other"

# -----------------------------
# Knowledge Graph helpers
# -----------------------------
def _split_people(val: Any) -> List[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        parts = [p.strip() for p in re.split(r",|&| and ", val) if p.strip()]
        return parts or [val.strip()]
    return []

def _read_kg(query: str) -> Dict[str, Any]:
    data = serper_search(query, num=1)
    kg = (data or {}).get("knowledgeGraph") or {}
    return kg if isinstance(kg, dict) else {}

def get_company_kg(query: str) -> Dict[str, Any]:
    """
    Returns a compact dict pulled from Google's Knowledge Graph via Serper:
      {
        "website": str|None,
        "phone": str|None,
        "address": str|None,
        "socials": {platform:url},
        "executives": [{"name","job_title"}]
      }
    We try multiple query shapes so KG works even when the plain name doesn’t trigger it.
    """
    queries: List[str] = []
    q = (query or "").strip()
    if q:
        queries.append(q)
        if "." in q:  # looks like domain
            bare = normalize_domain(q).split(".")[0]
            queries += [bare, f"{bare} company", f"{bare} headquarters"]
        else:
            queries += [f"{q} company", f"{q} headquarters", f"{q} official site"]

    kg: Dict[str, Any] = {}
    for qq in queries:
        kg = _read_kg(qq)
        if kg:
            break

    out: Dict[str, Any] = {"website": None, "phone": None, "address": None, "socials": {}, "executives": []}
    if not kg:
        return out

    # top-level fields commonly present in KG
    for k, v in kg.items():
        kl = str(k).lower()
        if out["website"] is None and kl in ("website", "url"):
            out["website"] = v
        if out["phone"] is None and ("phone" in kl or "telephone" in kl):
            out["phone"] = v
        if out["address"] is None and ("address" in kl or "headquarters" in kl):
            out["address"] = v

    # socials frequently appear as explicit properties
    for k, v in kg.items():
        kl = str(k).lower()
        if not isinstance(v, str):
            continue
        if   "twitter"   in kl: out["socials"]["twitter"]   = v
        elif "instagram" in kl: out["socials"]["instagram"] = v
        elif "linkedin"  in kl: out["socials"]["linkedin"]  = v
        elif "facebook"  in kl: out["socials"]["facebook"]  = v
        elif "youtube"   in kl: out["socials"]["youtube"]   = v

    # executive-like fields
    def add_people(keys: List[str], title: str) -> None:
        for k, v in kg.items():
            if any(p in str(k).lower() for p in keys):
                for name in _split_people(v):
                    if name:
                        out["executives"].append({"name": name, "job_title": title})

    add_people(["ceo", "chief executive"], "CEO")
    add_people(["founder"], "Founder")
    add_people(["chair", "chairman"], "Chairman")
    add_people(["president"], "President")

    # de-dupe
    seen: set = set()
    uniq: List[Dict[str, str]] = []
    for p in out["executives"]:
        name = (p.get("name") or "").strip()
        title = (p.get("job_title") or "").strip()
        if not name:
            continue
        key = (name.lower(), title.lower())
        if key not in seen:
            seen.add(key)
            uniq.append({"name": name, "job_title": title})
    out["executives"] = uniq[:12]
    return out
