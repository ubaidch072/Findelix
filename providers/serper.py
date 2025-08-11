
import os, requests

SERPER_KEY = os.getenv("SERPER_API_KEY")
HEADERS = {"X-API-KEY": SERPER_KEY or "", "Content-Type": "application/json"}
SEARCH_URL = "https://google.serper.dev/search"
NEWS_URL = "https://google.serper.dev/news"

def normalize_domain(domain: str) -> str:
    return domain.lower().replace("https://","").replace("http://","").strip("/")

def serper_search(q: str, num: int = 10, gl: str = "us"):
    if not SERPER_KEY:
        return {"organic": []}
    r = requests.post(SEARCH_URL, headers=HEADERS, json={"q": q, "num": num, "gl": gl}, timeout=20)
    if r.status_code == 200:
        return r.json()
    return {"organic": []}

def serper_news(q: str, num: int = 10, gl: str = "us"):
    if not SERPER_KEY:
        return {"news": []}
    r = requests.post(NEWS_URL, headers=HEADERS, json={"q": q, "num": num, "gl": gl}, timeout=20)
    if r.status_code == 200:
        return r.json()
    return {"news": []}

def gemini_summarize(prompt: str) -> str:
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        return ""
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        r = model.generate_content(prompt)
        return (r.text or "").strip()
    except Exception:
        return ""

def categorize_with_gemini_or_rules(name: str, domain: str, socials: dict) -> str:
    t = " ".join([name or "", domain or ""] + list((socials.get("links") or {}).keys())).lower()
    rules = [
        ("Tech", ["software","saas","ai","cloud","tech","it","data","developer","app","platform","streaming","music"]),
        ("Retail", ["store","shop","retail","ecommerce","fashion","apparel"]),
        ("Health", ["health","clinic","medical","pharma","biotech","hospital","wellness"]),
        ("Finance", ["bank","fintech","trading","investment","insurance","accounting"]),
        ("Education", ["school","university","academy","education","edtech","training"]),
        ("Hospitality", ["hotel","restaurant","cafe","resort","hospitality","food"]),
        ("Real Estate", ["real estate","property","realtor","housing"]),
        ("Manufacturing", ["manufactur","factory","industrial","automation","hardware"]),
    ]
    for cat, keys in rules:
        if any(k in t for k in keys):
            return cat
    out = gemini_summarize(f"Categorize '{name}' (domain {domain}) into: Tech, Retail, Health, Finance, Education, Hospitality, Real Estate, Manufacturing, Other. Return only the category.")
    return out.split()[0] if out else "Other"
