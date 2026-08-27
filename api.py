#!/usr/bin/env python3
"""
api.py — minimal deployable HTTP API around linkedin_scrape.scrape_profile().

Endpoints
    GET  /health                      -> liveness + whether cookies are present
    GET  /profile?url=<linkedin url>  -> structured profile JSON  (needs API key)
    POST /profile   {"url": "..."}    -> same, URL in JSON body    (needs API key)

Auth
    Every /profile call must send the API key in the `X-API-Key` header
    (or `?api_key=` query param). The key is fixed: it comes from the API_KEY
    environment variable / .env, falling back to DEFAULT_API_KEY below.

    ==> CHANGE DEFAULT_API_KEY (or set API_KEY in .env) before deploying. <==

Run (local)
    pip install -r requirements.txt
    python -m uvicorn api:app --host 0.0.0.0 --port 8000

    Example:
        curl -H "X-API-Key: <key>" \
             "http://localhost:8000/profile?url=https://www.linkedin.com/in/aniketkolte79/"

FOR EDUCATIONAL / PERSONAL USE. Automating LinkedIn access violates their ToS;
hosting this publicly risks the backing account. Keep volume low.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from linkedin_scrape import AuthWallError, load_creds, load_env_file, scrape_profile

# Pull .env into os.environ so PM2 / bare uvicorn / systemd behave like Docker's
# --env-file (API_KEY, PROXY_URL, cookies all become available).
load_env_file()

# --- Fixed permanent API key ------------------------------------------------ #
# Overridable via the API_KEY env var / .env; otherwise this constant is used.
DEFAULT_API_KEY = "lk_9f2c7a4e8b1d4f60a3e5c8b2d7f16a9c"
API_KEY = os.environ.get("API_KEY", "").strip() or DEFAULT_API_KEY

app = FastAPI(title="LinkedIn Profile API", version="1.0.0")


class ProfileRequest(BaseModel):
    url: str


def _check_key(provided: str | None) -> None:
    if not provided or provided != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _scrape(url: str) -> JSONResponse:
    li_at, jsid = load_creds()
    try:
        data = scrape_profile(url, li_at, jsid)
        return JSONResponse(content=data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AuthWallError:
        raise HTTPException(
            status_code=503,
            detail="LinkedIn session expired — refresh li_at/JSESSIONID in .env.",
        )
    except RuntimeError as e:  # HTTP 999 bot-detection
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


@app.get("/health")
def health():
    li_at, jsid = load_creds()
    return {"status": "ok", "cookies_present": bool(li_at and jsid)}


@app.get("/profile")
def profile_get(
    url: str = Query(..., description="LinkedIn profile URL, e.g. .../in/<slug>/"),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
):
    _check_key(x_api_key or api_key)
    return _scrape(url)


@app.post("/profile")
def profile_post(
    body: ProfileRequest,
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
):
    _check_key(x_api_key or api_key)
    return _scrape(body.url)
