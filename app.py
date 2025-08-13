# app.py
import io
import os
import csv
import json
import threading
import re
import importlib
from datetime import datetime
from typing import List, Tuple

from flask import Flask, render_template, request, send_file, jsonify

# 🔐 Load environment variables FIRST
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

# (Optional) lightweight response caching for outbound HTTP (SERPER/site fetches)
try:
    import requests_cache
    # Use env TTL; 0 = no cache (best for real-time)
    ttl = int(os.getenv("HTTP_CACHE_TTL_SECONDS", "0"))
    if ttl > 0:
        requests_cache.install_cache(
            cache_name="findelix_cache",
            backend="sqlite",
            expire_after=ttl,
        )
except Exception:
    pass

from core import build_profile, bulk_build_profiles

# For health and optional model warm-up
try:
    from providers.posts import _get_summarizer
except Exception:
    _get_summarizer = None  # noqa: N816

app = Flask(__name__, template_folder="templates")

DEBUG = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")

# ---- Basic env validation (helps in local/Render) ----
def _validate_env():
    missing = []
    # Summary: Gemini by default (unless local summarizer enabled)
    use_local = os.getenv("USE_LOCAL_SUMMARIZER", "0").lower() in ("1", "true", "yes")
    if not use_local and not os.getenv("GOOGLE_API_KEY"):
        missing.append("GOOGLE_API_KEY (or set USE_LOCAL_SUMMARIZER=1)")
    if not os.getenv("SERPER_API_KEY"):
        missing.append("SERPER_API_KEY")
    if missing and DEBUG:
        print("[Findelix][WARN] Missing env:", ", ".join(missing))

_validate_env()


# -- Optional CORS without triggering Pylance errors -------------------------
def _maybe_enable_cors(flask_app: Flask) -> None:
    """
    Enable CORS only if ENABLE_CORS is truthy.
    Avoid a hard import so static analyzers don't complain when the package
    isn't installed.
    """
    if os.getenv("ENABLE_CORS", "0").lower() not in ("1", "true", "yes"):
        return

    try:
        mod = importlib.import_module("flask_cors")
        CORS = getattr(mod, "CORS", None)
        if CORS:
            CORS(
                flask_app,
                resources={
                    r"/lookup": {"origins": "*"},
                    r"/bulk": {"origins": "*"},
                    r"/export": {"origins": "*"},
                },
            )
    except Exception:
        # If flask-cors isn't installed, just skip silently
        # pip install flask-cors  (if you want to enable CORS)
        pass


_maybe_enable_cors(app)
# ---------------------------------------------------------------------------


def _json_response(payload, status=200):
    """Consistent JSON responses; pretty in DEBUG only."""
    if DEBUG:
        return app.response_class(
            response=json.dumps(payload, indent=4, ensure_ascii=False),
            status=status,
            mimetype="application/json",
        )
    return app.response_class(
        response=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        status=status,
        mimetype="application/json",
    )


def _safe_filename(s: str, default="company"):
    """Create a safe filename component."""
    s = (s or "").strip()
    if not s:
        return default
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s or default


def _parse_single_input(data: dict) -> Tuple[str, str]:
    """Normalize input for single lookup."""
    name = (data.get("company") or "").strip()
    domain = (data.get("domain") or "").strip()
    return name, domain


def _read_csv_safely(raw: bytes) -> List[Tuple[str, str]]:
    """
    Read CSV for bulk:
    - Handles BOM
    - Detects delimiter
    - Accepts headers: company, domain (case-insensitive)
    - Falls back to simple split if no headers
    """
    text = raw.decode("utf-8-sig", errors="ignore")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    items: List[Tuple[str, str]] = []

    if reader.fieldnames and any(h.lower() in ("company", "domain") for h in reader.fieldnames):
        for r in reader:
            items.append(((r.get("company") or "").strip(), (r.get("domain") or "").strip()))
    else:
        # Fallback: each line is either "company,domain" or just "company"
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "," in line:
                company, domain = line.split(",", 1)
            else:
                company, domain = line, ""
            items.append((company.strip(), domain.strip()))

    return items


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/lookup")
def lookup():
    data = request.get_json(silent=True) or request.form or {}
    name, domain = _parse_single_input(data)

    if not name and not domain:
        return _json_response({"error": "Provide at least a company or a domain."}, status=400)

    try:
        res = build_profile(name, domain)
        return _json_response(res, status=200)
    except Exception as e:
        if DEBUG:
            return _json_response({"error": str(e)}, status=500)
        return _json_response({"error": "Internal error while building profile."}, status=500)


@app.post("/bulk")
def bulk():
    # Guardrails
    MAX_ITEMS = int(os.getenv("BULK_LIMIT", "100"))
    items: List[Tuple[str, str]] = []

    try:
        if "file" in request.files and request.files["file"].filename:
            items = _read_csv_safely(request.files["file"].read())
        else:
            text = (request.form.get("list") or "").strip()
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "," in line:
                    company, domain = line.split(",", 1)
                else:
                    company, domain = line, ""
                items.append((company.strip(), domain.strip()))

        # remove empty rows
        items = [(c, d) for c, d in items if c or d]

        if not items:
            return _json_response({"error": "No valid rows found."}, status=400)

        if len(items) > MAX_ITEMS:
            return _json_response({"error": f"Too many rows. Limit is {MAX_ITEMS}."}, status=413)

        results = bulk_build_profiles(items)
        return _json_response(results, status=200)

    except Exception as e:
        if DEBUG:
            return _json_response({"error": str(e)}, status=500)
        return _json_response({"error": "Internal error in bulk processing."}, status=500)


@app.get("/export")
def export():
    from export import to_csv_bytes, to_pdf_bytes  # local import to keep startup light

    fmt = (request.args.get("format", "csv") or "csv").lower()
    name = (request.args.get("company", "") or "").strip()
    domain = (request.args.get("domain", "") or "").strip()

    if not name and not domain:
        return _json_response({"error": "Provide at least a company or a domain."}, status=400)

    try:
        profile = build_profile(name, domain)
        base = _safe_filename(profile.get("domain") or profile.get("company") or "company")

        if fmt == "pdf":
            pdf_bytes = to_pdf_bytes([profile])  # single-page PDF
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"profile_{base}.pdf",
            )

        # default: CSV
        csv_bytes = to_csv_bytes([profile])
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"profile_{base}.csv",
        )

    except Exception as e:
        if DEBUG:
            return _json_response({"error": str(e)}, status=500)
        return _json_response({"error": "Internal error during export."}, status=500)


@app.get("/healthz")
def healthz():
    """
    Lightweight health check. Reports time and whether the ML summarizer is enabled/loaded.
    """
    status = {"ok": True}
    try:
        status["time"] = datetime.utcnow().isoformat() + "Z"
        use_local = os.getenv("USE_LOCAL_SUMMARIZER", "0").lower() in ("1", "true", "yes")
        status["ml_requested"] = use_local
        if _get_summarizer:
            try:
                s = _get_summarizer()
                status["ml_loaded"] = bool(s)
            except Exception:
                status["ml_loaded"] = False
        else:
            status["ml_loaded"] = False
    except Exception as e:
        status["ok"] = False
        status["error"] = str(e)
    return jsonify(status)


# ---- Optional: warm up the ML summarizer at boot if requested (non-blocking) ----
def _warmup_summarizer_async():
    if _get_summarizer and os.getenv("USE_LOCAL_SUMMARIZER", "0").lower() in ("1", "true", "yes"):
        try:
            _get_summarizer()
        except Exception:
            pass


# Start warm-up in background to avoid blocking main thread on boot
threading.Thread(target=_warmup_summarizer_async, daemon=True).start()


if __name__ == "__main__":
    # Respect PORT env var (Render/Heroku-like), default to 8000 locally.
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
