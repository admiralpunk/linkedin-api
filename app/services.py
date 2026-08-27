"""Orchestration: URL -> public_id -> Voyager fetch -> parse -> cache."""

from __future__ import annotations

from copy import deepcopy

from .cache import ProfileCache
from .config import Settings
from .schemas import Profile
from .utils import extract_public_id
from .voyager import endpoints
from .voyager.client import VoyagerClient
from .voyager.parser import parse_graphql, parse_profile_view


class ProfileService:
    def __init__(self, client: VoyagerClient, cache: ProfileCache, settings: Settings) -> None:
        self._client = client
        self._cache = cache
        self._settings = settings

    async def get_profile(self, url: str) -> Profile:
        """Return a structured profile for a LinkedIn URL, using the cache when warm."""
        public_id = extract_public_id(url)  # raises InvalidProfileURL

        cached = self._cache.get(public_id)
        if cached is not None:
            result = deepcopy(cached)
            result.cached = True
            return result

        if self._settings.graphql_configured:
            profile = await self._fetch_graphql(public_id)
        else:
            # Legacy REST path (returns 410 on current LinkedIn — kept for reference).
            raw = await self._client.get_json(endpoints.profile_view(public_id))
            profile = parse_profile_view(public_id, raw)

        self._cache.set(public_id, profile)
        return profile

    async def _fetch_graphql(self, public_id: str) -> Profile:
        variables = endpoints.profile_variables(public_id)
        responses = [
            await self._client.get_graphql(
                endpoints.graphql(self._settings.gql_profile_query_id, variables)
            )
        ]
        # Optional second query for the section cards (experience/education/skills/...).
        if self._settings.gql_cards_query_id:
            responses.append(
                await self._client.get_graphql(
                    endpoints.graphql(self._settings.gql_cards_query_id, variables)
                )
            )
        return parse_graphql(public_id, *responses)
