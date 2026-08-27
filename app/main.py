"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import __version__
from .cache import ProfileCache
from .config import get_settings
from .schemas import ErrorResponse, HealthResponse, Profile, ProfileRequest
from .security import require_api_key
from .services import ProfileService
from .utils import InvalidProfileURL
from .voyager.client import (
    CookieExpiredError,
    EndpointGoneError,
    ProfileNotFoundError,
    RateLimitedError,
    VoyagerClient,
    VoyagerError,
)

settings = get_settings()
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = VoyagerClient(settings)
    cache = ProfileCache(ttl=settings.cache_ttl)
    app.state.service = ProfileService(client, cache, settings)
    app.state.voyager_client = client
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(
    title="LinkedIn Profile API",
    version=__version__,
    description=(
        "Accepts a LinkedIn profile URL and returns the profile as structured JSON, "
        "sourced from LinkedIn's internal Voyager API. Educational use only — see README."
    ),
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})


def _to_http_error(exc: Exception) -> HTTPException:
    """Map internal exceptions to appropriate HTTP status codes."""
    if isinstance(exc, InvalidProfileURL):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, CookieExpiredError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LinkedIn session expired — backend cookie needs refreshing.",
        )
    if isinstance(exc, ProfileNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, EndpointGoneError):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "LinkedIn endpoint/queryId is stale (410) — re-capture the current queryId.",
        )
    if isinstance(exc, RateLimitedError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    if isinstance(exc, VoyagerError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error.")


async def _fetch(request: Request, url: str) -> Profile:
    service: ProfileService = request.app.state.service
    try:
        return await service.get_profile(url)
    except Exception as exc:  # noqa: BLE001 — remap to HTTP errors below
        raise _to_http_error(exc) from exc


_RESPONSES = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        cookies_present=settings.cookies_present,
        version=__version__,
    )


@app.get(
    "/api/v1/profile",
    response_model=Profile,
    responses=_RESPONSES,
    tags=["profile"],
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_profile_by_query(
    request: Request,
    url: str = Query(..., description="Full LinkedIn profile URL, e.g. https://www.linkedin.com/in/williamhgates/"),
) -> Profile:
    return await _fetch(request, url)


@app.post(
    "/api/v1/profile",
    response_model=Profile,
    responses=_RESPONSES,
    tags=["profile"],
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_profile_by_body(request: Request, body: ProfileRequest) -> Profile:
    return await _fetch(request, body.url)
