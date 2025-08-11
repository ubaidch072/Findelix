import io, csv, os, json
from flask import Flask, render_template, request, send_file

# 🔐 Load environment variables FIRST (before importing core/providers)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

from core import build_profile, bulk_build_profiles
from export import to_csv_bytes, to_pdf_bytes

app = Flask(__name__, template_folder="templates")

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/lookup")
def lookup():
    data = request.get_json(silent=True) or request.form
    name = (data.get("company") or "").strip()
    domain = (data.get("domain") or "").strip()
    res = build_profile(name, domain)
    return app.response_class(
        response=json.dumps(res, indent=4),  # pretty printed JSON
        status=200,
        mimetype='application/json'
    )

@app.post("/bulk")
def bulk():
    if "file" in request.files:
        f = request.files["file"]
        text = f.read().decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(text))
        items = [(r.get("company","").strip(), r.get("domain","").strip()) for r in reader]
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
        mimetype='application/json'
    )

@app.get("/export")
def export():
    fmt = request.args.get("format","csv").lower()
    name = request.args.get("company","").strip()
    domain = request.args.get("domain","").strip()
    res = build_profile(name, domain)
    if fmt == "pdf":
        pdf = to_pdf_bytes(res)
        return send_file(
            io.BytesIO(pdf),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"profile_{domain or name}.pdf"
        )
    else:
        csvb = to_csv_bytes([res])
        return send_file(
            io.BytesIO(csvb),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"profile_{domain or name}.csv"
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
