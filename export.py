import csv, io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from textwrap import wrap

# ---------- CSV ----------

def to_csv_bytes(results):
    """
    Returns UTF-8 **bytes** (not str) so it can be wrapped in BytesIO and downloaded.
    """
    buf = io.StringIO(newline="")
    w = csv.writer(buf)

    # Header
    w.writerow([
        "company", "domain", "website",
        "instagram", "facebook", "linkedin", "twitter", "youtube",
        "emails", "phones", "addresses",
        "category"
    ])

    for r in results:
        s = (r.get("socials") or {})
        c = (r.get("contacts") or {})
        emails = "; ".join(c.get("emails") or [])
        phones = "; ".join(c.get("phones") or [])
        addrs = "; ".join([a.get("value", "") for a in (c.get("addresses") or [])])

        w.writerow([
            r.get("company", "") or "",
            r.get("domain", "") or "",
            r.get("website", "") or "",
            s.get("instagram", "") or "",
            s.get("facebook", "") or "",
            s.get("linkedin", "") or "",
            s.get("twitter", "") or "",
            s.get("youtube", "") or "",
            emails,
            phones,
            addrs,
            r.get("category", "") or "",
        ])

    # IMPORTANT: encode to bytes for BytesIO + add BOM so Excel detects UTF-8
    text = buf.getvalue()
    return text.encode("utf-8-sig")


# ---------- PDF ----------

def to_pdf_bytes(profile):
    """
    Build a simple one-page PDF summary of the profile.
    Returns bytes suitable for send_file(BytesIO(...)).
    """
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=A4)
    width, height = A4

    x = 40
    y = height - 40
    leading = 16

    def line(txt, lead=None):
        nonlocal y
        c.drawString(x, y, txt)
        y -= (lead or leading)

    # Title
    c.setFont("Helvetica-Bold", 16)
    line(f"{profile.get('company') or profile.get('domain') or 'Profile'}")
    c.setFont("Helvetica", 12)

    # Basic
    line(f"Domain: {profile.get('domain','')}")
    line(f"Website: {profile.get('website','')}")
    line(f"Category: {profile.get('category','')}")
    line("")

    # Socials
    s = profile.get("socials") or {}
    line("Socials:")
    line(f"  Instagram: {s.get('instagram','')}", 14)
    line(f"  Facebook : {s.get('facebook','')}", 14)
    line(f"  LinkedIn : {s.get('linkedin','')}", 14)
    line(f"  X/Twitter: {s.get('twitter','')}", 14)
    line(f"  YouTube  : {s.get('youtube','')}", 14)
    line("")

    # Contacts
    cts = profile.get("contacts") or {}
    for e in (cts.get("emails") or [])[:4]:
        line(f"Email: {e}", 14)
    for p in (cts.get("phones") or [])[:3]:
        line(f"Phone: {p}", 14)
    for a in (cts.get("addresses") or [])[:2]:
        line(f"Address: {a.get('value','')[:95]}", 14)
    line("")

    # Executives
    line("Executives:")
    for e in (profile.get("executives") or [])[:6]:
        nm = e.get("name","")
        tt = e.get("title","")
        li = e.get("linkedin","") or ""
        line(f"  - {nm} — {tt}  {li}", 14)
    line("")

    # Summary
    summary = (profile.get("summary") or "").strip()
    if summary:
        line("Summary:")
        for part in wrap(summary, 95):
            line(part, 14)

    c.showPage()
    c.save()
    return b.getvalue()
