"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_LI_TRACK = (
    '{"clientVersion":"1.13.0","mpVersion":"1.13.0","osName":"web",'
    '"timezoneOffset":0,"timezone":"UTC","deviceFormFactor":"DESKTOP",'
    '"mpName":"voyager-web","displayDensity":1,"displayWidth":1920,"displayHeight":1080}'
)
_DEFAULT_LI_PAGE_INSTANCE = "urn:li:page:d_flagship3_profile_view_base;AAAAAAAAAAAAAAAAAAAAAA=="


class Settings(BaseSettings):
    """Runtime configuration. All values come from the environment (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LinkedIn session cookies (backend credentials).
    li_at: str = ""
    jsessionid: str = ""

    # Access control.
    api_key: str = ""

    # --- Voyager GraphQL (captured from a live browser session) ---
    # queryId hashes are version-pinned by LinkedIn and change on their deploys,
    # so they live in config and can be refreshed without a code change.
    gql_profile_query_id: str = ""  # e.g. voyagerIdentityDashProfiles.<hash>
    gql_cards_query_id: str = ""  # optional: profile components/cards query
    # Browser tracking headers LinkedIn expects on GraphQL calls. Blank => sensible default.
    li_track: str = ""
    li_page_instance: str = ""

    @property
    def effective_li_track(self) -> str:
        return self.li_track or _DEFAULT_LI_TRACK

    @property
    def effective_li_page_instance(self) -> str:
        return self.li_page_instance or _DEFAULT_LI_PAGE_INSTANCE

    # --- Playwright (headless browser) fetch layer ---
    headless: bool = True
    nav_timeout_ms: int = 45_000  # per-navigation timeout
    scroll_passes: int = 8  # how many scroll steps to trigger SDUI lazy sections
    scroll_pause_ms: int = 900  # wait between scroll steps for content to load
    fetch_details_pages: bool = True  # also visit /details/* pages for full lists

    # Tuning.
    cache_ttl: int = 43_200  # 12 hours
    rate_limit_per_minute: int = 10
    cors_origins: str = "*"

    @property
    def graphql_configured(self) -> bool:
        return bool(self.gql_profile_query_id)

    @property
    def cookies_present(self) -> bool:
        return bool(self.li_at and self.jsessionid)

    @property
    def csrf_token(self) -> str:
        """LinkedIn's csrf-token header must equal the JSESSIONID value (minus quotes)."""
        return self.jsessionid.strip('"')

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
