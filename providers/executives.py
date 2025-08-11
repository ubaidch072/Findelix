import requests
import re
from .serper import serper_search

def get_executives(company: str, domain: str, website: str):
    executives = []

    # Targeted CEO search
    ceo_name = search_ceo_name(company)
    if ceo_name:
        executives.append({
            "name": ceo_name,
            "title": "CEO",
            "linkedin": search_ceo_linkedin(company),
            "email": None
        })

    return executives

def search_ceo_name(company: str):
    """Search Google for the company's CEO name using SERPER API."""
    query = f"{company} CEO"
    try:
        data = serper_search(query, num=3)
        for it in data.get("organic", []):
            snippet = (it.get("snippet") or "").strip()
            # Simple pattern for capitalized name (2-4 words)
            m = re.search(r'([A-Z][a-z]+(?:\s[A-Z][a-z]+){1,3})', snippet)
            if m:
                return m.group(1)
    except:
        pass
    return None

def search_ceo_linkedin(company: str):
    """Find CEO LinkedIn URL using SERPER."""
    query = f"site:linkedin.com/in CEO {company}"
    try:
        data = serper_search(query, num=5)
        for it in data.get("organic", []):
            link = it.get("link", "")
            if "linkedin.com/in" in link:
                return link
    except:
        pass
    return None
