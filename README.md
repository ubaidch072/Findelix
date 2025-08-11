
# Findelix — Backend Fixed (Real-time, CSV/PDF)

- Real-time social/contact discovery via SERPER + site crawl
- Executives: ONLY official pages, curated C-suite titles
- Gemini summaries (no OpenAI dependency)
- CSV/PDF export

## Run (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env  # then add your keys
python app.py   # open http://localhost:8000
