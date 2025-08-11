import time
from datetime import datetime
from typing import Dict, List, Tuple

from providers.socials import get_socials
from providers.contacts import get_contacts
from providers.executives import get_executives
from providers.posts import get_recent_posts_and_summary
from providers.serper import categorize_with_gemini_or_rules, normalize_domain

def _now():
    """Return current UTC date as string."""
    return datetime.utcnow().strftime("%Y-%m-%d")

def build_profile(company: str, domain: str) -> Dict:
    """
    Build a full company profile including socials, contacts,
    executives, recent posts, category, and timing.
    """
    start = time.time()
    company = (company or "").strip()
    domain = normalize_domain((domain or "").strip())

    # Fetch socials (website + social links)
    socials = get_socials(company, domain)

    # Fetch contacts (emails, phones, addresses)
    contacts = get_contacts(domain, socials.get("website"))

    # Fetch executives (names, titles, LinkedIn)
    execs = get_executives(company, domain, socials.get("website"))

    # Fetch recent posts + summary
    posts = get_recent_posts_and_summary(company, domain, socials.get("website"))

    # Categorize company
    category = categorize_with_gemini_or_rules(company, domain, socials)

    return {
        "company": company or None,
        "domain": domain or None,
        "website": socials.get("website"),
        "socials": socials.get("links"),
        "contacts": contacts,
        "executives": execs,
        "summary": posts.get("summary") or "",
        "recent_posts": posts.get("posts"),
        "category": category,
        "generated_at": _now(),
        "latency_ms": int((time.time() - start) * 1000)
    }

def bulk_build_profiles(items: List[Tuple[str, str]]) -> List[Dict]:
    """Build multiple profiles at once."""
    return [build_profile(n, d) for n, d in items]
