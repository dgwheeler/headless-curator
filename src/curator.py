"""Core playlist curation algorithm."""

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.apple_music import AppleMusicAuth, AppleMusicClient, Track
from src.apple_music.client import AuthenticationError
from src.database import Repository
from src.musicbrainz import MusicBrainzClient
from src.utils.config import Settings
from src.utils.logging import get_logger
from src.utils.notifications import send_auth_failure_email

logger = get_logger(__name__)

# Patterns to strip from song names when checking for duplicates
NORMALIZE_PATTERNS = [
    r"\s*\(.*?(remix|acoustic|live|radio edit|edit|version|remaster|deluxe|bonus|explicit|clean|instrumental|extended|single|album|original|mix|feat\.?|ft\.?).*?\)\s*",
    r"\s*\[.*?(remix|acoustic|live|radio edit|edit|version|remaster|deluxe|bonus|explicit|clean|instrumental|extended|single|album|original|mix|feat\.?|ft\.?).*?\]\s*",
    r"\s*-\s*(remix|acoustic|live|radio edit|edit|remaster|remastered).*$",
]


def normalize_song_name(name: str) -> str:
    """Normalize song name for duplicate detection.

    Strips common suffixes like (Remix), (Acoustic Version), [Live], etc.
    """
    import re
    normalized = name.lower().strip()
    for pattern in NORMALIZE_PATTERNS:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)
    return normalized.strip()


class PlaylistCategory:
    """Category constants for playlist composition."""

    FAVORITES = "favorites"
    HITS = "hits"
    DISCOVERY = "discovery"
    WILDCARD = "wildcard"


@dataclass
class TrackInfo:
    """Minimal track info for playlist building."""

    id: str
    name: str
    artist_name: str
    album_name: str = ""


class Curator:
    """Main playlist curation engine.

    Uses Apple Music API for all operations including:
    - Catalog searches (artist discovery, top songs)
    - Library access (favorites, heard detection)
    - Playlist management (create, update)
    """

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
    ) -> None:
        self.settings = settings
        self.repository = repository

        # Initialize API clients
        self.auth = AppleMusicAuth(
            team_id=settings.apple_music.team_id,
            key_id=settings.apple_music.key_id,
            private_key_path=settings.apple_music.private_key_path_resolved,
        )
        self.apple_music = AppleMusicClient(
            auth=self.auth,
            storefront=settings.apple_music.storefront,
        )
        self.musicbrainz = MusicBrainzClient()

    async def close(self) -> None:
        """Clean up resources."""
        await self.apple_music.close()
        await self.musicbrainz.close()
        await self.repository.close()

    async def discover_artists(self) -> list[str]:
        """Discover new artists based on seed artists.

        Returns:
            List of Apple Music artist IDs that match filters
        """
        logger.info("starting_artist_discovery", seed_count=len(self.settings.seeds.artists))

        discovered_artist_ids: set[str] = set()
        seed_artist_ids: list[str] = []

        async with self.apple_music:
            # Find seed artists in Apple Music catalog
            for seed_name in self.settings.seeds.artists:
                results = await self.apple_music.search(seed_name, types=["artists"], limit=1)
                if results.artists:
                    artist = results.artists[0]
                    seed_artist_ids.append(artist.id)

                    # Store as seed artist
                    await self.repository.upsert_artist(
                        apple_music_id=artist.id,
                        name=artist.name,
                        is_seed=True,
                    )

                    logger.debug("seed_artist_found", name=seed_name, apple_id=artist.id)

            # Include seed artists themselves in discovered list
            for artist_id in seed_artist_ids:
                discovered_artist_ids.add(artist_id)

            # Try to get related artists for each seed (may fail without user token)
            for artist_id in seed_artist_ids:
                related = await self.apple_music.get_related_artists(artist_id, limit=15)
                for artist in related:
                    discovered_artist_ids.add(artist.id)

                    await self.repository.upsert_artist(
                        apple_music_id=artist.id,
                        name=artist.name,
                    )

        # Filter only NON-SEED artists through MusicBrainz
        # Seed artists are always included - user explicitly chose them
        seed_artist_ids_set = set(seed_artist_ids)
        non_seed_ids = discovered_artist_ids - seed_artist_ids_set

        artist_names_to_filter = []
        for artist_id in non_seed_ids:
            artist = await self.repository.get_artist_by_apple_id(artist_id)
            if artist:
                artist_names_to_filter.append(artist.name)

        async with self.musicbrainz:
            filtered_names = await self.musicbrainz.filter_artists_by_criteria(
                artist_names_to_filter,
                countries=self.settings.filters.countries,
                min_release_year=self.settings.filters.min_release_year,
            )

        # Map filtered names back to Apple Music IDs
        filtered_ids = []
        for name in filtered_names:
            artist = await self.repository.get_artist_by_name(name)
            if artist:
                filtered_ids.append(artist.apple_music_id)

        # Always include seed artists (they bypass MusicBrainz filter)
        final_ids = list(seed_artist_ids_set) + [aid for aid in filtered_ids if aid not in seed_artist_ids_set]

        logger.info(
            "artist_discovery_complete",
            seeds=len(seed_artist_ids),
            discovered=len(non_seed_ids),
            filtered=len(filtered_ids),
            total=len(final_ids),
        )

        return final_ids

    async def collect_tracks(self, artist_ids: list[str]) -> dict[str, list[TrackInfo]]:
        """Collect tracks from discovered artists.

        Args:
            artist_ids: List of Apple Music artist IDs

        Returns:
            Dictionary of tracks by category
        """
        logger.info("collecting_tracks", artist_count=len(artist_ids))

        tracks_by_category: dict[str, list[TrackInfo]] = {
            PlaylistCategory.HITS: [],
            PlaylistCategory.DISCOVERY: [],
            PlaylistCategory.WILDCARD: [],
        }

        now = datetime.now(timezone.utc)
        wildcard_cutoff = now - timedelta(days=self.settings.algorithm.new_release_days)

        async with self.apple_music:
            # Get library tracks for "heard" detection
            library_tracks = await self.apple_music.get_all_library_songs()
            library_keys = {
                f"{t.attributes.artist_name.lower()}:{t.name.lower()}"
                for t in library_tracks
                if t.attributes
            }

            for artist_id in artist_ids:
                top_songs = await self.apple_music.get_artist_top_songs(artist_id, limit=10)

                for track in top_songs:
                    if not track.attributes:
                        continue

                    # Check release year filter
                    if track.attributes.release_datetime:
                        release_year = track.attributes.release_datetime.year
                        if release_year < self.settings.filters.min_release_year:
                            continue

                    # Categorize track
                    track_key = f"{track.artist_name.lower()}:{track.name.lower()}"
                    is_known = track_key in library_keys

                    track_info = TrackInfo(
                        id=track.id,
                        name=track.name,
                        artist_name=track.artist_name,
                        album_name=track.album_name,
                    )

                    if track.attributes.release_datetime and track.attributes.release_datetime >= wildcard_cutoff:
                        tracks_by_category[PlaylistCategory.WILDCARD].append(track_info)
                    elif is_known:
                        tracks_by_category[PlaylistCategory.HITS].append(track_info)
                    else:
                        tracks_by_category[PlaylistCategory.DISCOVERY].append(track_info)

                    # Store in database
                    await self.repository.upsert_track(
                        apple_music_id=track.id,
                        name=track.name,
                        artist_name=track.artist_name,
                        album_name=track.album_name,
                        isrc=track.isrc,
                        duration_ms=track.attributes.duration_in_millis,
                        release_date=track.attributes.release_datetime,
                    )

                # Small delay to avoid rate limiting
                await asyncio.sleep(0.1)

        logger.info(
            "tracks_collected",
            hits=len(tracks_by_category[PlaylistCategory.HITS]),
            discovery=len(tracks_by_category[PlaylistCategory.DISCOVERY]),
            wildcard=len(tracks_by_category[PlaylistCategory.WILDCARD]),
        )

        return tracks_by_category

    async def get_favorites(self, artist_ids: list[str]) -> list[TrackInfo]:
        """Get favorite tracks from discovered artists based on play counts.

        Args:
            artist_ids: List of Apple Music artist IDs to filter by

        Returns:
            List of most-played tracks from discovered artists
        """
        # Get artist names for filtering
        artist_names_lower = set()
        for artist_id in artist_ids:
            artist = await self.repository.get_artist_by_apple_id(artist_id)
            if artist:
                artist_names_lower.add(artist.name.lower())

        async with self.apple_music:
            library_tracks = await self.apple_music.get_all_library_songs()

            # Filter to only tracks from discovered artists and sort by play count
            matching_tracks = []
            for track in library_tracks:
                if not track.attributes:
                    continue
                if track.attributes.artist_name.lower() in artist_names_lower:
                    matching_tracks.append(track)

            sorted_tracks = sorted(
                matching_tracks,
                key=lambda t: t.play_count,
                reverse=True,
            )

            # Convert to TrackInfo
            favorites = []
            for lt in sorted_tracks[:50]:
                if lt.attributes and lt.play_count > 0:
                    favorites.append(TrackInfo(
                        id=lt.id,
                        name=lt.name,
                        artist_name=lt.attributes.artist_name,
                        album_name=lt.attributes.album_name,
                    ))

        logger.info("favorites_collected", count=len(favorites))
        return favorites

    async def update_preferences(self) -> None:
        """Update preference weights based on listening behavior."""
        logger.info("updating_preferences")

        # Detect negative signals (hot zone, no plays)
        unplayed = await self.repository.get_unplayed_hot_zone_tracks(
            hot_zone_hours=self.settings.algorithm.hot_zone_hours,
            hot_zone_size=self.settings.algorithm.hot_zone_size,
        )

        for track, pref in unplayed:
            new_weight = max(0.1, pref.weight * 0.7)  # Reduce by 30%
            await self.repository.upsert_preference(
                track_id=track.id,
                weight=new_weight,
            )
            logger.debug("negative_signal", track=track.name, new_weight=new_weight)

        # Apply decay to stale tracks
        decaying = await self.repository.get_decaying_tracks(
            decay_days=self.settings.algorithm.decay_days,
        )

        for track, pref in decaying:
            if pref.last_played_at:
                days_since = (datetime.now(timezone.utc) - pref.last_played_at).days
                decay_factor = max(0.5, 1 - (days_since - self.settings.algorithm.decay_days) * 0.02)
                new_weight = pref.weight * decay_factor
                await self.repository.upsert_preference(
                    track_id=track.id,
                    weight=new_weight,
                )

        logger.info("preferences_updated")

    def build_playlist(
        self,
        favorites: list[TrackInfo],
        hits: list[TrackInfo],
        discovery: list[TrackInfo],
        wildcard: list[TrackInfo],
    ) -> list[str]:
        """Build the final playlist with weighted categories.

        Args:
            favorites: High play count tracks from discovered artists
            hits: Top tracks from discovered artists (known)
            discovery: Tracks from similar artists (unknown)
            wildcard: New releases

        Returns:
            List of track IDs in interleaved order
        """
        playlist_size = self.settings.algorithm.playlist_size
        weights = self.settings.algorithm.weights

        # Calculate target counts for each category
        targets = {
            PlaylistCategory.FAVORITES: int(playlist_size * weights.favorites),
            PlaylistCategory.HITS: int(playlist_size * weights.hits),
            PlaylistCategory.DISCOVERY: int(playlist_size * weights.discovery),
            PlaylistCategory.WILDCARD: int(playlist_size * weights.wildcard),
        }

        # Shuffle each category
        random.shuffle(favorites)
        random.shuffle(hits)
        random.shuffle(discovery)
        random.shuffle(wildcard)

        # Select tracks up to target for each category
        selected: dict[str, list[str]] = {
            PlaylistCategory.FAVORITES: [t.id for t in favorites[: targets[PlaylistCategory.FAVORITES]]],
            PlaylistCategory.HITS: [t.id for t in hits[: targets[PlaylistCategory.HITS]]],
            PlaylistCategory.DISCOVERY: [t.id for t in discovery[: targets[PlaylistCategory.DISCOVERY]]],
            PlaylistCategory.WILDCARD: [t.id for t in wildcard[: targets[PlaylistCategory.WILDCARD]]],
        }

        # Interleave tracks to avoid clustering
        playlist: list[str] = []
        categories = [PlaylistCategory.FAVORITES, PlaylistCategory.HITS, PlaylistCategory.DISCOVERY, PlaylistCategory.WILDCARD]
        category_idx = 0

        while len(playlist) < playlist_size:
            # Try each category in rotation
            attempts = 0
            while attempts < len(categories):
                cat = categories[category_idx % len(categories)]
                if selected[cat]:
                    playlist.append(selected[cat].pop(0))
                    category_idx += 1
                    break
                category_idx += 1
                attempts += 1

            if attempts >= len(categories):
                # All categories empty
                break

        logger.info(
            "playlist_built",
            total=len(playlist),
            favorites=targets[PlaylistCategory.FAVORITES],
            hits=targets[PlaylistCategory.HITS],
            discovery=targets[PlaylistCategory.DISCOVERY],
            wildcard=targets[PlaylistCategory.WILDCARD],
        )

        return playlist

    async def create_or_update_playlist(self, track_ids: list[str]) -> str:
        """Create playlist with tracks, or update existing one.

        Args:
            track_ids: List of catalog track IDs to add

        Returns:
            Playlist ID
        """
        playlist_name = self.settings.user.playlist_name

        async with self.apple_music:
            # Find all playlists with this name and pick the best one
            all_playlists = await self.apple_music.get_library_playlists()
            matching = [p for p in all_playlists if p.name == playlist_name]

            if matching:
                # Use existing playlist - Apple Music API can't delete library playlists
                # Find the one with tracks if multiple exist, otherwise use first
                existing = matching[0]
                existing_tracks = []
                for p in matching:
                    tracks = await self.apple_music.get_library_playlist_tracks(p.id)
                    if tracks:
                        existing = p
                        existing_tracks = tracks
                        break

                logger.info("reusing_existing_playlist", name=playlist_name, id=existing.id, existing_tracks=len(existing_tracks))

                # Get existing track names to deduplicate (can't compare IDs - library vs catalog)
                # Normalize names to catch variants like "Song (Remix)" vs "Song (Acoustic)"
                existing_keys = set()
                for t in existing_tracks:
                    if t.attributes:
                        normalized_name = normalize_song_name(t.name)
                        key = f"{t.attributes.artist_name.lower()}:{normalized_name}"
                        existing_keys.add(key)

                # Filter to only new tracks
                new_track_ids = []
                for tid in track_ids:
                    # Look up track info from our database
                    track = await self.repository.get_track_by_apple_id(tid)
                    if track:
                        normalized_name = normalize_song_name(track.name)
                        key = f"{track.artist_name.lower()}:{normalized_name}"
                        if key not in existing_keys:
                            new_track_ids.append(tid)
                            existing_keys.add(key)  # Prevent duplicates within batch

                if new_track_ids:
                    # Note: PUT (replace) requires elevated permissions that web auth doesn't grant
                    # Using POST (add) instead
                    await self.apple_music.add_tracks_to_library_playlist(existing.id, new_track_ids)
                    logger.info("playlist_updated_with_tracks", name=playlist_name, id=existing.id,
                               new_tracks=len(new_track_ids), skipped_duplicates=len(track_ids) - len(new_track_ids))
                else:
                    logger.info("no_new_tracks_to_add", name=playlist_name, all_duplicates=len(track_ids))

                return existing.id
            else:
                # Create new playlist with tracks
                playlist = await self.apple_music.create_library_playlist(
                    name=playlist_name,
                    description=f"Personalized playlist for {self.settings.user.name}, curated by Headless Curator",
                    track_ids=track_ids,
                )

                logger.info("playlist_created_with_tracks", name=playlist_name, id=playlist.id, track_count=len(track_ids))
                return playlist.id

    async def refresh_playlist(self) -> dict:
        """Run a full playlist refresh.

        Returns:
            Summary of the refresh operation
        """
        start_time = time.time()
        logger.info("starting_playlist_refresh", user=self.settings.user.name)

        try:
            # Initialize database
            await self.repository.init_db()

            # Update preferences from listening data
            await self.update_preferences()

            # Discover artists
            artist_ids = await self.discover_artists()

            # Collect tracks by category
            tracks_by_cat = await self.collect_tracks(artist_ids)

            # Get favorites from discovered artists in user's library
            favorites = await self.get_favorites(artist_ids)

            # Build the playlist
            playlist_track_ids = self.build_playlist(
                favorites=favorites,
                hits=tracks_by_cat[PlaylistCategory.HITS],
                discovery=tracks_by_cat[PlaylistCategory.DISCOVERY],
                wildcard=tracks_by_cat[PlaylistCategory.WILDCARD],
            )

            # Create playlist with tracks (or recreate if exists)
            playlist_id = await self.create_or_update_playlist(playlist_track_ids)

            # Update playlist state
            await self.repository.upsert_playlist_state(
                playlist_id=playlist_id,
                playlist_name=self.settings.user.playlist_name,
                track_count=len(playlist_track_ids),
            )

            duration = time.time() - start_time

            # Log success
            await self.repository.log_sync(
                sync_type="refresh",
                status="success",
                tracks_added=len(playlist_track_ids),
                duration_seconds=duration,
            )

            summary = {
                "status": "success",
                "playlist_id": playlist_id,
                "track_count": len(playlist_track_ids),
                "duration_seconds": round(duration, 2),
                "artists_discovered": len(artist_ids),
            }

            logger.info("playlist_refresh_complete", **summary)
            return summary

        except AuthenticationError as e:
            duration = time.time() - start_time
            logger.error("playlist_refresh_failed_auth", error=str(e))

            await self.repository.log_sync(
                sync_type="refresh",
                status="auth_failure",
                error_message=str(e),
                duration_seconds=duration,
            )

            # Send email notification
            try:
                send_auth_failure_email(self.settings)
                logger.info("auth_failure_email_sent")
            except Exception as email_error:
                logger.error("auth_failure_email_failed", error=str(email_error))

            raise

        except Exception as e:
            duration = time.time() - start_time
            logger.error("playlist_refresh_failed", error=str(e))

            await self.repository.log_sync(
                sync_type="refresh",
                status="failure",
                error_message=str(e),
                duration_seconds=duration,
            )

            raise


async def run_curator(config_path: Path | str = "config.yaml") -> dict:
    """Convenience function to run a playlist refresh.

    Args:
        config_path: Path to configuration file

    Returns:
        Refresh summary
    """
    from src.utils.config import load_config

    settings = load_config(config_path)
    repository = Repository(settings.database.url)

    curator = Curator(settings, repository)
    try:
        return await curator.refresh_playlist()
    finally:
        await curator.close()
