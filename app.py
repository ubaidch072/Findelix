import io, csv, os, json
from flask import Flask, render_template, request, send_file, jsonify

# 🔐 Load environment variables FIRST (before importing core/providers)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

# (Optional) lightweight response caching for outbound HTTP (SERPER/site fetches)
try:
    import requests_cache
    requests_cache.install_cache(
        cache_name="findelix_cache",
        backend="sqlite",
        expire_after=24 * 60 * 60,  # 24h
    )
except Exception:
    pass

from core import build_profile, bulk_build_profiles
from export import to_csv_bytes, to_pdf_bytes

# For health and optional model warm-up
try:
    from providers.posts import _get_summarizer
except Exception:
    _get_summarizer = None

app = Flask(__name__, template_folder="templates")


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/lookup")
def lookup():
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get("company") or "").strip()
    domain = (data.get("domain") or "").strip()

    try:
        res = build_profile(name, domain)
        return app.response_class(
            response=json.dumps(res, indent=4),  # pretty printed JSON
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        return app.response_class(
            response=json.dumps({"error": str(e)}, indent=2),
            status=500,
            mimetype="application/json",
        )


@app.post("/bulk")
def bulk():
    try:
        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]
            text = f.read().decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            items = [(r.get("company", "").strip(), r.get("domain", "").strip()) for r in reader]
        else:
            text = (request.form.get("list") or "").strip()
            items = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                if "," in line:
                    company, domain = line.split(",", 1)
                else:
                    company, domain = line, ""
                items.append((company.strip(), domain.strip()))

        results = bulk_build_profiles(items)
        return app.response_class(
            response=json.dumps(results, indent=4),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        return app.response_class(
            response=json.dumps({"error": str(e)}, indent=2),
            status=500,
            mimetype="application/json",
        )


@app.get("/export")
def export():
    fmt = (request.args.get("format", "csv") or "csv").lower()
    name = (request.args.get("company", "") or "").strip()
    domain = (request.args.get("domain", "") or "").strip()

    try:
        profile = build_profile(name, domain)

        if fmt == "pdf":
            # export expects a list of profiles; generate single-page PDF
            pdf_bytes = to_pdf_bytes([profile])
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"profile_{profile.get('domain') or profile.get('company') or 'company'}.pdf",
            )

        # default: CSV
        csv_bytes = to_csv_bytes([profile])
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"profile_{profile.get('domain') or profile.get('company') or 'company'}.csv",
        )
    except Exception as e:
        return app.response_class(
            response=json.dumps({"error": str(e)}, indent=2),
            status=500,
            mimetype="application/json",
        )


@app.get("/healthz")
def healthz():
    """
    Lightweight health check. Reports time and whether the ML summarizer is enabled/loaded.
    """
    status = {"ok": True}
    try:
        from datetime import datetime

        status["time"] = datetime.utcnow().isoformat() + "Z"
        # report env flag and load state
        use_local = os.getenv("USE_LOCAL_SUMMARIZER", "0").lower() in ("1", "true", "yes")
        status["ml_requested"] = use_local
        if _get_summarizer:
            s = _get_summarizer()
            status["ml_loaded"] = bool(s)
        else:
            status["ml_loaded"] = False
    except Exception as e:
        status["ok"] = False
        status["error"] = str(e)
    return jsonify(status)


# ---- Optional: warm up the ML summarizer at boot if requested ----
if _get_summarizer and os.getenv("USE_LOCAL_SUMMARIZER", "0").lower() in ("1", "true", "yes"):
    try:
        _ = _get_summarizer()
    except Exception:
        pass


if __name__ == "__main__":
    # Respect PORT env var (Render provides it), default to 8000 locally.
    port = int(os.environ.get("PORT", "8000"))
    debug = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
