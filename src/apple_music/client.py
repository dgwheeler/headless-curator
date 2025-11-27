"""Apple Music API client with async support."""

import asyncio
from typing import Any

import httpx

from src.utils.logging import get_logger

from .auth import AppleMusicAuth
from .models import (
    Artist,
    LibraryPlaylist,
    LibraryTrack,
    Playlist,
    SearchResults,
    Track,
)

logger = get_logger(__name__)

BASE_URL = "https://api.music.apple.com/v1"
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # Exponential backoff base


class AppleMusicError(Exception):
    """Base exception for Apple Music API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(AppleMusicError):
    """Rate limit exceeded."""

    pass


class AuthenticationError(AppleMusicError):
    """Authentication failed."""

    pass


class AppleMusicClient:
    """Async client for Apple Music API."""

    def __init__(self, auth: AppleMusicAuth, storefront: str = "us") -> None:
        """Initialize the client.

        Args:
            auth: AppleMusicAuth instance for authentication
            storefront: Country storefront code (e.g., 'us', 'gb')
        """
        self.auth = auth
        self.storefront = storefront
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AppleMusicClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=30.0,
                http2=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        require_user_token: bool = False,
    ) -> dict[str, Any]:
        """Make an authenticated API request with retry logic.

        Args:
            method: HTTP method
            path: API path (relative to base URL)
            params: Query parameters
            json: JSON body
            require_user_token: If True, fail if no user token available

        Returns:
            JSON response data
        """
        client = await self._ensure_client()
        headers = self.auth.get_auth_headers()

        if require_user_token and "Music-User-Token" not in headers:
            raise AuthenticationError("User token required but not available", 401)

        for attempt in range(MAX_RETRIES):
            try:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers=headers,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(
                        "rate_limited",
                        retry_after=retry_after,
                        attempt=attempt + 1,
                    )
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(retry_after)
                        continue
                    raise RateLimitError("Rate limit exceeded", 429)

                if response.status_code == 401:
                    raise AuthenticationError("Authentication failed", 401)

                if response.status_code == 403:
                    raise AuthenticationError("Access forbidden", 403)

                response.raise_for_status()

                if response.status_code == 204:
                    return {}

                return response.json()

            except httpx.HTTPStatusError as e:
                logger.error(
                    "http_error",
                    status_code=e.response.status_code,
                    path=path,
                    attempt=attempt + 1,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF_BASE ** attempt)
                    continue
                raise AppleMusicError(str(e), e.response.status_code) from e

            except httpx.RequestError as e:
                logger.error("request_error", error=str(e), path=path, attempt=attempt + 1)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF_BASE ** attempt)
                    continue
                raise AppleMusicError(str(e)) from e

        raise AppleMusicError("Max retries exceeded")

    # Catalog endpoints

    async def search(
        self,
        term: str,
        types: list[str] | None = None,
        limit: int = 25,
    ) -> SearchResults:
        """Search the Apple Music catalog.

        Args:
            term: Search query
            types: Resource types to search (artists, songs, albums)
            limit: Maximum results per type

        Returns:
            SearchResults with artists and songs
        """
        if types is None:
            types = ["artists", "songs"]

        data = await self._request(
            "GET",
            f"/catalog/{self.storefront}/search",
            params={
                "term": term,
                "types": ",".join(types),
                "limit": limit,
            },
        )

        results = SearchResults()

        if "results" in data:
            if "artists" in data["results"] and "data" in data["results"]["artists"]:
                results.artists = [Artist(**a) for a in data["results"]["artists"]["data"]]
            if "songs" in data["results"] and "data" in data["results"]["songs"]:
                results.songs = [Track(**s) for s in data["results"]["songs"]["data"]]

        return results

    async def get_artist(self, artist_id: str) -> Artist | None:
        """Get artist details by ID."""
        try:
            data = await self._request(
                "GET",
                f"/catalog/{self.storefront}/artists/{artist_id}",
            )
            if data.get("data"):
                return Artist(**data["data"][0])
        except AppleMusicError:
            pass
        return None

    async def get_related_artists(self, artist_id: str, limit: int = 10) -> list[Artist]:
        """Get artists related to the given artist.

        Args:
            artist_id: Apple Music artist ID
            limit: Maximum number of related artists

        Returns:
            List of related artists
        """
        # Try fetching artist with similar-artists view
        try:
            data = await self._request(
                "GET",
                f"/catalog/{self.storefront}/artists/{artist_id}",
                params={"views": "similar-artists"},
            )
            if data.get("data"):
                artist_data = data["data"][0]
                views = artist_data.get("views", {})
                similar = views.get("similar-artists", {}).get("data", [])
                if similar:
                    return [Artist(**a) for a in similar[:limit]]
        except AppleMusicError as e:
            logger.debug("similar_artists_view_error", artist_id=artist_id, error=str(e))

        # Fallback: try the direct relationship endpoint
        try:
            data = await self._request(
                "GET",
                f"/catalog/{self.storefront}/artists/{artist_id}/similar-artists",
                params={"limit": limit},
            )
            if data.get("data"):
                return [Artist(**a) for a in data["data"]]
        except AppleMusicError as e:
            logger.warning("related_artists_error", artist_id=artist_id, error=str(e))

        return []

    async def get_artist_top_songs(
        self,
        artist_id: str,
        limit: int = 10,
    ) -> list[Track]:
        """Get top songs for an artist.

        Args:
            artist_id: Apple Music artist ID
            limit: Maximum number of songs

        Returns:
            List of top tracks
        """
        try:
            data = await self._request(
                "GET",
                f"/catalog/{self.storefront}/artists/{artist_id}/songs",
                params={"limit": limit},
            )
            if data.get("data"):
                return [Track(**t) for t in data["data"]]
        except AppleMusicError as e:
            logger.warning("artist_songs_error", artist_id=artist_id, error=str(e))
        return []

    async def get_track(self, track_id: str) -> Track | None:
        """Get track details by ID."""
        try:
            data = await self._request(
                "GET",
                f"/catalog/{self.storefront}/songs/{track_id}",
            )
            if data.get("data"):
                return Track(**data["data"][0])
        except AppleMusicError:
            pass
        return None

    async def get_new_releases(
        self,
        genre_id: str | None = None,
        limit: int = 25,
    ) -> list[Track]:
        """Get new music releases.

        Args:
            genre_id: Optional genre ID to filter by
            limit: Maximum number of results

        Returns:
            List of new release tracks
        """
        params: dict[str, Any] = {"limit": limit}
        if genre_id:
            params["genre"] = genre_id

        try:
            # Use charts endpoint for new releases
            data = await self._request(
                "GET",
                f"/catalog/{self.storefront}/charts",
                params={"types": "songs", "chart": "most-played", **params},
            )
            if data.get("results", {}).get("songs"):
                songs_data = data["results"]["songs"][0].get("data", [])
                return [Track(**t) for t in songs_data]
        except AppleMusicError as e:
            logger.warning("new_releases_error", error=str(e))
        return []

    # Library endpoints (require user token)

    async def get_library_playlists(self, limit: int = 100) -> list[LibraryPlaylist]:
        """Get user's library playlists.

        Returns:
            List of library playlists
        """
        data = await self._request(
            "GET",
            "/me/library/playlists",
            params={"limit": limit},
            require_user_token=True,
        )

        if data.get("data"):
            return [LibraryPlaylist(**p) for p in data["data"]]
        return []

    async def get_library_playlist_by_name(self, name: str) -> LibraryPlaylist | None:
        """Find a library playlist by name.

        Args:
            name: Playlist name to search for

        Returns:
            LibraryPlaylist if found, None otherwise
        """
        playlists = await self.get_library_playlists()
        for playlist in playlists:
            if playlist.name == name:
                return playlist
        return None

    async def get_library_playlist_tracks(self, playlist_id: str, limit: int = 100) -> list[LibraryTrack]:
        """Get tracks from a library playlist.

        Args:
            playlist_id: Library playlist ID
            limit: Maximum tracks to return

        Returns:
            List of library tracks in the playlist
        """
        try:
            data = await self._request(
                "GET",
                f"/me/library/playlists/{playlist_id}/tracks",
                params={"limit": limit},
                require_user_token=True,
            )

            tracks = []
            for item in data.get("data", []):
                tracks.append(LibraryTrack(**item))
            return tracks
        except Exception as e:
            logger.warning("get_playlist_tracks_error", playlist_id=playlist_id, error=str(e))
            return []

    async def create_library_playlist(
        self,
        name: str,
        description: str = "",
        track_ids: list[str] | None = None,
    ) -> LibraryPlaylist:
        """Create a new library playlist.

        Args:
            name: Playlist name
            description: Optional description
            track_ids: Optional list of catalog track IDs to add

        Returns:
            Created playlist
        """
        payload: dict[str, Any] = {
            "attributes": {
                "name": name,
                "description": description,
            },
        }

        if track_ids:
            payload["relationships"] = {
                "tracks": {
                    "data": [{"id": tid, "type": "songs"} for tid in track_ids],
                },
            }

        data = await self._request(
            "POST",
            "/me/library/playlists",
            json=payload,
            require_user_token=True,
        )

        if data.get("data"):
            return LibraryPlaylist(**data["data"][0])

        raise AppleMusicError("Failed to create playlist")

    async def delete_library_playlist(self, playlist_id: str) -> bool:
        """Delete a library playlist.

        Args:
            playlist_id: Library playlist ID

        Returns:
            True if deleted successfully
        """
        try:
            await self._request(
                "DELETE",
                f"/me/library/playlists/{playlist_id}",
                require_user_token=True,
            )
            logger.info("playlist_deleted", playlist_id=playlist_id)
            return True
        except AppleMusicError as e:
            logger.warning("playlist_delete_failed", playlist_id=playlist_id, error=str(e))
            return False

    async def add_tracks_to_library_playlist(
        self,
        playlist_id: str,
        track_ids: list[str],
    ) -> None:
        """Add tracks to a library playlist.

        Args:
            playlist_id: Library playlist ID
            track_ids: List of catalog track IDs to add
        """
        payload = {
            "data": [{"id": tid, "type": "songs"} for tid in track_ids],
        }

        await self._request(
            "POST",
            f"/me/library/playlists/{playlist_id}/tracks",
            json=payload,
            require_user_token=True,
        )

        logger.info(
            "tracks_added_to_playlist",
            playlist_id=playlist_id,
            track_count=len(track_ids),
        )

    async def remove_track_from_library_playlist(
        self,
        playlist_id: str,
        library_track_id: str,
    ) -> bool:
        """Remove a track from a library playlist.

        Uses DELETE with query parameter as discovered from Apple's web interface.

        Args:
            playlist_id: Library playlist ID (e.g., p.XXXXX)
            library_track_id: Library track ID (e.g., i.XXXXX)

        Returns:
            True if removed successfully
        """
        try:
            await self._request(
                "DELETE",
                f"/me/library/playlists/{playlist_id}/tracks",
                params={"ids[library-songs]": library_track_id, "mode": "all"},
                require_user_token=True,
            )
            logger.info(
                "track_removed_from_playlist",
                playlist_id=playlist_id,
                track_id=library_track_id,
            )
            return True
        except AppleMusicError as e:
            logger.warning(
                "track_removal_failed",
                playlist_id=playlist_id,
                track_id=library_track_id,
                error=str(e),
            )
            return False

    async def replace_playlist_tracks(
        self,
        playlist_id: str,
        track_ids: list[str],
    ) -> None:
        """Replace all tracks in a library playlist.

        Args:
            playlist_id: Library playlist ID
            track_ids: List of catalog track IDs
        """
        # Apple Music API requires tracks data in the body
        payload = {
            "data": [{"id": tid, "type": "songs"} for tid in track_ids],
        }

        await self._request(
            "PUT",
            f"/me/library/playlists/{playlist_id}/tracks",
            json=payload,
            require_user_token=True,
        )

        logger.info(
            "playlist_tracks_replaced",
            playlist_id=playlist_id,
            track_count=len(track_ids),
        )

    async def get_library_songs(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LibraryTrack]:
        """Get songs from user's library.

        Args:
            limit: Maximum songs to return (max 100)
            offset: Pagination offset

        Returns:
            List of library tracks with play counts
        """
        data = await self._request(
            "GET",
            "/me/library/songs",
            params={"limit": min(limit, 100), "offset": offset},
            require_user_token=True,
        )

        if data.get("data"):
            return [LibraryTrack(**t) for t in data["data"]]
        return []

    async def get_all_library_songs(self) -> list[LibraryTrack]:
        """Get all songs from user's library (handles pagination).

        Returns:
            Complete list of library tracks
        """
        all_tracks: list[LibraryTrack] = []
        offset = 0
        limit = 100

        while True:
            tracks = await self.get_library_songs(limit=limit, offset=offset)
            if not tracks:
                break

            all_tracks.extend(tracks)
            if len(tracks) < limit:
                break

            offset += limit
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)

        logger.info("library_songs_fetched", total=len(all_tracks))
        return all_tracks

    async def get_recently_played(self, limit: int = 25) -> list[Track]:
        """Get recently played tracks.

        Args:
            limit: Maximum number of tracks

        Returns:
            List of recently played tracks
        """
        data = await self._request(
            "GET",
            "/me/recent/played/tracks",
            params={"limit": limit},
            require_user_token=True,
        )

        if data.get("data"):
            return [Track(**t) for t in data["data"]]
        return []

    async def get_playlist_tracks(
        self,
        playlist_id: str,
        limit: int = 100,
    ) -> list[Track]:
        """Get tracks from a library playlist.

        Args:
            playlist_id: Library playlist ID
            limit: Maximum tracks to return

        Returns:
            List of tracks in the playlist
        """
        data = await self._request(
            "GET",
            f"/me/library/playlists/{playlist_id}/tracks",
            params={"limit": limit},
            require_user_token=True,
        )

        if data.get("data"):
            return [Track(**t) for t in data["data"]]
        return []
