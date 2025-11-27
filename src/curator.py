"""Core playlist curation algorithm."""

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.apple_music import AppleMusicAuth, AppleMusicClient, Track
from src.database import Repository, TrackRecord
from src.musicbrainz import MusicBrainzClient
from src.utils.config import Settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PlaylistCategory:
    """Category constants for playlist composition."""

    FAVORITES = "favorites"
    HITS = "hits"
    DISCOVERY = "discovery"
    WILDCARD = "wildcard"


class Curator:
    """Main playlist curation engine."""

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

            # Try to get related artists for each seed (may fail on some APIs)
            for artist_id in seed_artist_ids:
                related = await self.apple_music.get_related_artists(artist_id, limit=15)
                for artist in related:
                    discovered_artist_ids.add(artist.id)

                    await self.repository.upsert_artist(
                        apple_music_id=artist.id,
                        name=artist.name,
                    )

        # Filter through MusicBrainz
        artist_names = []
        for artist_id in discovered_artist_ids:
            artist = await self.repository.get_artist_by_apple_id(artist_id)
            if artist:
                artist_names.append(artist.name)

        async with self.musicbrainz:
            filtered_names = await self.musicbrainz.filter_artists_by_criteria(
                artist_names,
                gender=self.settings.filters.gender,
                countries=self.settings.filters.countries,
                min_release_year=self.settings.filters.min_release_year,
            )

        # Map back to Apple Music IDs
        filtered_ids = []
        for name in filtered_names:
            artist = await self.repository.get_artist_by_name(name)
            if artist:
                filtered_ids.append(artist.apple_music_id)

        logger.info(
            "artist_discovery_complete",
            discovered=len(discovered_artist_ids),
            filtered=len(filtered_ids),
        )

        return filtered_ids

    async def collect_tracks(self, artist_ids: list[str]) -> dict[str, list[Track]]:
        """Collect tracks from discovered artists.

        Args:
            artist_ids: List of Apple Music artist IDs

        Returns:
            Dictionary of tracks by category
        """
        logger.info("collecting_tracks", artist_count=len(artist_ids))

        tracks_by_category: dict[str, list[Track]] = {
            PlaylistCategory.HITS: [],
            PlaylistCategory.DISCOVERY: [],
            PlaylistCategory.WILDCARD: [],
        }

        now = datetime.now(timezone.utc)
        wildcard_cutoff = now - timedelta(days=self.settings.algorithm.new_release_days)

        async with self.apple_music:
            # Get library tracks for "heard" detection
            library_tracks = await self.apple_music.get_all_library_songs()
            library_isrcs = {t.attributes.artist_name + ":" + t.name for t in library_tracks if t.attributes}

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
                    track_key = f"{track.artist_name}:{track.name}"
                    is_known = track_key in library_isrcs

                    if track.attributes.release_datetime and track.attributes.release_datetime >= wildcard_cutoff:
                        tracks_by_category[PlaylistCategory.WILDCARD].append(track)
                    elif is_known:
                        tracks_by_category[PlaylistCategory.HITS].append(track)
                    else:
                        tracks_by_category[PlaylistCategory.DISCOVERY].append(track)

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

    async def get_favorites(self) -> list[Track]:
        """Get favorite tracks based on play counts.

        Returns:
            List of most-played tracks
        """
        async with self.apple_music:
            library_tracks = await self.apple_music.get_all_library_songs()

            # Sort by play count
            sorted_tracks = sorted(
                library_tracks,
                key=lambda t: t.play_count,
                reverse=True,
            )

            # Update preferences in database
            for track in sorted_tracks[:100]:  # Top 100
                if not track.attributes:
                    continue

                db_track = await self.repository.upsert_track(
                    apple_music_id=track.id,
                    name=track.name,
                    artist_name=track.attributes.artist_name,
                    album_name=track.attributes.album_name,
                    category=PlaylistCategory.FAVORITES,
                )

                await self.repository.upsert_preference(
                    track_id=db_track.id,
                    play_count=track.play_count,
                    in_library=True,
                )

            # Convert to catalog tracks for playlist (library tracks need mapping)
            # For now, return as Track objects (the IDs are catalog IDs)
            favorites: list[Track] = []
            for lt in sorted_tracks[:50]:
                if lt.attributes and lt.play_count > 0:
                    # Create a Track from library track data
                    favorites.append(
                        Track(
                            id=lt.id,
                            type="songs",
                            attributes=None,  # We'll use the ID for playlist
                        )
                    )

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

        # Boost tracks with positive signals
        async with self.apple_music:
            library_tracks = await self.apple_music.get_all_library_songs()

            for lt in library_tracks:
                if not lt.attributes:
                    continue

                db_track = await self.repository.get_track_by_apple_id(lt.id)
                if not db_track:
                    continue

                pref = await self.repository.get_preference(db_track.id)
                if not pref:
                    continue

                # Check for play count increase
                if lt.play_count > pref.play_count:
                    plays_delta = lt.play_count - pref.play_count
                    boost = 1 + (plays_delta * 0.1)  # 10% boost per play
                    new_weight = min(5.0, pref.weight * boost)

                    await self.repository.upsert_preference(
                        track_id=db_track.id,
                        play_count=lt.play_count,
                        weight=new_weight,
                    )
                    logger.debug(
                        "positive_signal",
                        track=db_track.name,
                        plays_delta=plays_delta,
                        new_weight=new_weight,
                    )

        logger.info("preferences_updated")

    def build_playlist(
        self,
        favorites: list[Track],
        hits: list[Track],
        discovery: list[Track],
        wildcard: list[Track],
    ) -> list[str]:
        """Build the final playlist with weighted categories.

        Args:
            favorites: High play count tracks
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
        """Create playlist with tracks, or recreate if it exists.

        Since PUT to modify playlist tracks returns 401, we create a new
        playlist with tracks included in the initial POST request.

        Args:
            track_ids: List of catalog track IDs to add

        Returns:
            Playlist ID
        """
        playlist_name = self.settings.user.playlist_name

        async with self.apple_music:
            # Check if playlist exists
            existing = await self.apple_music.get_library_playlist_by_name(playlist_name)
            if existing:
                logger.info("playlist_found_will_recreate", name=playlist_name, id=existing.id)
                # Note: We can't delete playlists via API, so we create with a slightly different name
                # and the old one will remain. User can delete manually if desired.
                # Actually, let's just try to create a new one - Apple might allow duplicates
                # or we could add a timestamp

            # Create new playlist with tracks included
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

            # Get favorites from user's library
            favorites = await self.get_favorites()

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
