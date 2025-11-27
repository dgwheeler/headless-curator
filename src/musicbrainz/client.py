"""MusicBrainz API client for artist metadata."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.utils.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "HeadlessCurator/1.0 (https://github.com/dave/headless-curator)"
CACHE_DIR = Path.home() / ".cache" / "headless-curator" / "musicbrainz"
CACHE_TTL_DAYS = 30

# Rate limiting: MusicBrainz allows 1 request per second
RATE_LIMIT_DELAY = 1.1  # seconds between requests


class ArtistInfo:
    """Artist metadata from MusicBrainz."""

    def __init__(
        self,
        mbid: str,
        name: str,
        gender: str | None = None,
        country: str | None = None,
        begin_year: int | None = None,
        end_year: int | None = None,
        disambiguation: str | None = None,
    ) -> None:
        self.mbid = mbid
        self.name = name
        self.gender = gender
        self.country = country
        self.begin_year = begin_year
        self.end_year = end_year
        self.disambiguation = disambiguation

    @property
    def is_active(self) -> bool:
        """Check if artist is currently active (no end date)."""
        return self.end_year is None

    def has_recent_release(self, min_year: int) -> bool:
        """Check if artist has releases after a certain year."""
        # If no end year, they might still be active
        if self.end_year is None:
            return True
        return self.end_year >= min_year

    def matches_filters(
        self,
        gender: str | None = None,
        countries: list[str] | None = None,
        min_release_year: int | None = None,
    ) -> bool:
        """Check if artist matches the given filters.

        Args:
            gender: Required gender (case-insensitive)
            countries: List of allowed country codes
            min_release_year: Minimum year for recent activity

        Returns:
            True if artist matches all specified filters
        """
        if gender and self.gender:
            if self.gender.lower() != gender.lower():
                return False

        if countries and self.country:
            if self.country not in countries:
                return False

        if min_release_year:
            if not self.has_recent_release(min_release_year):
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for caching."""
        return {
            "mbid": self.mbid,
            "name": self.name,
            "gender": self.gender,
            "country": self.country,
            "begin_year": self.begin_year,
            "end_year": self.end_year,
            "disambiguation": self.disambiguation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtistInfo":
        """Create from dictionary."""
        return cls(**data)


class MusicBrainzClient:
    """Async client for MusicBrainz API with caching."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self) -> "MusicBrainzClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _get_cache_path(self, key: str) -> Path:
        """Get cache file path for a key."""
        # Hash the key to create a valid filename
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
        return CACHE_DIR / f"{key_hash}.json"

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        """Read from cache if not expired."""
        cache_path = self._get_cache_path(key)
        if not cache_path.exists():
            return None

        try:
            data = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(data.get("cached_at", ""))
            if datetime.now() - cached_at < timedelta(days=CACHE_TTL_DAYS):
                return data.get("value")
        except (json.JSONDecodeError, ValueError):
            pass

        return None

    def _write_cache(self, key: str, value: dict[str, Any]) -> None:
        """Write to cache with timestamp."""
        cache_path = self._get_cache_path(key)
        data = {
            "cached_at": datetime.now().isoformat(),
            "value": value,
        }
        cache_path.write_text(json.dumps(data))

    async def _rate_limited_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a rate-limited request to MusicBrainz.

        MusicBrainz rate limits to 1 request per second.
        """
        import time

        client = await self._ensure_client()

        # Enforce rate limiting
        now = time.time()
        time_since_last = now - self._last_request_time
        if time_since_last < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - time_since_last)

        params = params or {}
        params["fmt"] = "json"

        try:
            response = await client.request(method, path, params=params)
            self._last_request_time = time.time()

            if response.status_code == 503:
                # Service unavailable, wait and retry
                logger.warning("musicbrainz_rate_limited")
                await asyncio.sleep(5)
                return await self._rate_limited_request(method, path, params)

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                "musicbrainz_http_error",
                status_code=e.response.status_code,
                path=path,
            )
            raise
        except httpx.RequestError as e:
            logger.error("musicbrainz_request_error", error=str(e), path=path)
            raise

    async def search_artist(self, name: str) -> ArtistInfo | None:
        """Search for an artist by name.

        Args:
            name: Artist name to search for

        Returns:
            ArtistInfo if found, None otherwise
        """
        cache_key = f"search:{name.lower()}"
        cached = self._read_cache(cache_key)
        if cached:
            logger.debug("cache_hit", key=cache_key)
            return ArtistInfo.from_dict(cached)

        try:
            data = await self._rate_limited_request(
                "GET",
                "/artist",
                params={"query": f'artist:"{name}"', "limit": 5},
            )

            artists = data.get("artists", [])
            if not artists:
                return None

            # Find best match (exact name match preferred)
            best_match = None
            for artist in artists:
                if artist.get("name", "").lower() == name.lower():
                    best_match = artist
                    break
            if not best_match:
                best_match = artists[0]

            info = self._parse_artist(best_match)
            if info:
                self._write_cache(cache_key, info.to_dict())

            return info

        except Exception as e:
            logger.error("artist_search_error", name=name, error=str(e))
            return None

    async def get_artist(self, mbid: str) -> ArtistInfo | None:
        """Get artist details by MusicBrainz ID.

        Args:
            mbid: MusicBrainz artist ID

        Returns:
            ArtistInfo if found, None otherwise
        """
        cache_key = f"artist:{mbid}"
        cached = self._read_cache(cache_key)
        if cached:
            logger.debug("cache_hit", key=cache_key)
            return ArtistInfo.from_dict(cached)

        try:
            data = await self._rate_limited_request(
                "GET",
                f"/artist/{mbid}",
                params={"inc": "release-groups"},
            )

            info = self._parse_artist(data)
            if info:
                self._write_cache(cache_key, info.to_dict())

            return info

        except Exception as e:
            logger.error("artist_get_error", mbid=mbid, error=str(e))
            return None

    async def get_artist_release_years(self, mbid: str) -> tuple[int | None, int | None]:
        """Get the earliest and latest release years for an artist.

        Args:
            mbid: MusicBrainz artist ID

        Returns:
            Tuple of (earliest_year, latest_year)
        """
        cache_key = f"releases:{mbid}"
        cached = self._read_cache(cache_key)
        if cached:
            return cached.get("earliest"), cached.get("latest")

        try:
            data = await self._rate_limited_request(
                "GET",
                f"/release-group",
                params={
                    "artist": mbid,
                    "type": "album|single",
                    "limit": 100,
                },
            )

            release_groups = data.get("release-groups", [])
            years = []
            for rg in release_groups:
                date = rg.get("first-release-date", "")
                if date and len(date) >= 4:
                    try:
                        years.append(int(date[:4]))
                    except ValueError:
                        pass

            if years:
                earliest = min(years)
                latest = max(years)
                self._write_cache(cache_key, {"earliest": earliest, "latest": latest})
                return earliest, latest

        except Exception as e:
            logger.error("release_years_error", mbid=mbid, error=str(e))

        return None, None

    def _parse_artist(self, data: dict[str, Any]) -> ArtistInfo | None:
        """Parse artist data from MusicBrainz response."""
        if not data:
            return None

        mbid = data.get("id")
        name = data.get("name")
        if not mbid or not name:
            return None

        # Extract gender (for solo artists)
        gender = data.get("gender")

        # Extract country
        country = data.get("country") or data.get("area", {}).get("iso-3166-1-codes", [None])[0]

        # Extract life span
        life_span = data.get("life-span", {})
        begin_year = None
        end_year = None

        if begin_str := life_span.get("begin"):
            try:
                begin_year = int(begin_str[:4])
            except ValueError:
                pass

        if end_str := life_span.get("end"):
            try:
                end_year = int(end_str[:4])
            except ValueError:
                pass

        return ArtistInfo(
            mbid=mbid,
            name=name,
            gender=gender,
            country=country,
            begin_year=begin_year,
            end_year=end_year,
            disambiguation=data.get("disambiguation"),
        )

    async def filter_artists_by_criteria(
        self,
        artist_names: list[str],
        gender: str | None = None,
        countries: list[str] | None = None,
        min_release_year: int | None = None,
    ) -> list[str]:
        """Filter a list of artist names by MusicBrainz criteria.

        Args:
            artist_names: List of artist names to filter
            gender: Required gender
            countries: Allowed country codes
            min_release_year: Minimum year for recent releases

        Returns:
            List of artist names that match the criteria
        """
        matching: list[str] = []

        for name in artist_names:
            info = await self.search_artist(name)
            if not info:
                # If we can't find info, include by default
                matching.append(name)
                continue

            # For recent release check, we need to look at actual releases
            if min_release_year:
                _, latest_year = await self.get_artist_release_years(info.mbid)
                if latest_year and latest_year < min_release_year:
                    logger.debug(
                        "artist_filtered_by_year",
                        name=name,
                        latest_year=latest_year,
                        min_year=min_release_year,
                    )
                    continue

            if info.matches_filters(gender=gender, countries=countries):
                matching.append(name)
            else:
                logger.debug(
                    "artist_filtered",
                    name=name,
                    gender=info.gender,
                    country=info.country,
                )

        return matching
