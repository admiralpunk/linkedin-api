"""Optional API-key gate for the public endpoint."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests lacking a valid X-API-Key, unless API_KEY is unset (auth disabled)."""
    settings = get_settings()
    if not settings.api_key:
        return  # auth disabled
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key.",
        )
