# LinkedIn Profile Extractor

Turns a `linkedin.com/in/<slug>/` URL into structured JSON using your own
session cookies. FastAPI backend + single-page web UI. Educational/personal
use only — see [reverse_engineering.md](reverse_engineering.md).

## Run

```bash
pip install -r requirements.txt
```

Create a `.env` with your LinkedIn cookies:

```env
LI_AT=<li_at cookie value>
JSESSIONID="ajax:<JSESSIONID cookie value>"
API_KEY=<pick a random string>
```

Start the server:

```bash
python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/**, paste a profile URL, click **Fetch Profile**.

Or via curl:

```bash
curl -H "X-API-Key: <API_KEY>" "http://localhost:8000/profile?url=<profile url>"
```
