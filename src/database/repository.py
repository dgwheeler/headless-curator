"""Database repository for CRUD operations."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.utils.logging import get_logger

from .models import (
    ArtistRecord,
    Base,
    PlaylistState,
    PreferenceRecord,
    SyncLog,
    TrackRecord,
)

logger = get_logger(__name__)


class Repository:
    """Async repository for database operations."""

    def __init__(self, database_url: str) -> None:
        """Initialize the repository.

        Args:
            database_url: SQLAlchemy database URL (e.g., sqlite+aiosqlite:///curator.db)
        """
        self.engine = create_async_engine(database_url, echo=False)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    async def init_db(self) -> None:
        """Initialize database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_initialized")

    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()

    # Artist operations

    async def get_artist_by_apple_id(self, apple_music_id: str) -> ArtistRecord | None:
        """Get artist by Apple Music ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(ArtistRecord).where(ArtistRecord.apple_music_id == apple_music_id)
            )
            return result.scalar_one_or_none()

    async def get_artist_by_name(self, name: str) -> ArtistRecord | None:
        """Get artist by name (case-insensitive)."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(ArtistRecord).where(func.lower(ArtistRecord.name) == name.lower())
            )
            return result.scalar_one_or_none()

    async def upsert_artist(
        self,
        apple_music_id: str,
        name: str,
        musicbrainz_id: str | None = None,
        gender: str | None = None,
        country: str | None = None,
        is_seed: bool = False,
    ) -> ArtistRecord:
        """Create or update an artist."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(ArtistRecord).where(ArtistRecord.apple_music_id == apple_music_id)
            )
            artist = result.scalar_one_or_none()

            if artist:
                artist.name = name
                if musicbrainz_id:
                    artist.musicbrainz_id = musicbrainz_id
                if gender:
                    artist.gender = gender
                if country:
                    artist.country = country
                artist.is_seed = is_seed or artist.is_seed
            else:
                artist = ArtistRecord(
                    apple_music_id=apple_music_id,
                    name=name,
                    musicbrainz_id=musicbrainz_id,
                    gender=gender,
                    country=country,
                    is_seed=is_seed,
                )
                session.add(artist)

            await session.commit()
            await session.refresh(artist)
            return artist

    async def get_seed_artists(self) -> list[ArtistRecord]:
        """Get all seed artists."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(ArtistRecord).where(ArtistRecord.is_seed == True)  # noqa: E712
            )
            return list(result.scalars().all())

    # Track operations

    async def get_track_by_apple_id(self, apple_music_id: str) -> TrackRecord | None:
        """Get track by Apple Music ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrackRecord).where(TrackRecord.apple_music_id == apple_music_id)
            )
            return result.scalar_one_or_none()

    async def get_track_by_name_artist(self, name: str, artist_name: str) -> TrackRecord | None:
        """Get track by name and artist (case-insensitive)."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrackRecord).where(
                    func.lower(TrackRecord.name) == name.lower(),
                    func.lower(TrackRecord.artist_name) == artist_name.lower(),
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def upsert_track(
        self,
        apple_music_id: str,
        name: str,
        artist_name: str,
        album_name: str | None = None,
        isrc: str | None = None,
        duration_ms: int = 0,
        release_date: datetime | None = None,
        category: str | None = None,
        artist_id: int | None = None,
    ) -> TrackRecord:
        """Create or update a track."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrackRecord).where(TrackRecord.apple_music_id == apple_music_id)
            )
            track = result.scalar_one_or_none()

            if track:
                track.name = name
                track.artist_name = artist_name
                if album_name:
                    track.album_name = album_name
                if isrc:
                    track.isrc = isrc
                if duration_ms:
                    track.duration_ms = duration_ms
                if release_date:
                    track.release_date = release_date
                if category:
                    track.category = category
                if artist_id:
                    track.artist_id = artist_id
            else:
                track = TrackRecord(
                    apple_music_id=apple_music_id,
                    name=name,
                    artist_name=artist_name,
                    album_name=album_name,
                    isrc=isrc,
                    duration_ms=duration_ms,
                    release_date=release_date,
                    category=category,
                    artist_id=artist_id,
                )
                session.add(track)

            await session.commit()
            await session.refresh(track)
            return track

    async def get_tracks_by_category(self, category: str, limit: int = 100) -> list[TrackRecord]:
        """Get tracks by category."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrackRecord).where(TrackRecord.category == category).limit(limit)
            )
            return list(result.scalars().all())

    async def get_tracks_with_preferences(
        self,
        category: str | None = None,
        min_weight: float = 0.0,
        limit: int = 100,
    ) -> list[tuple[TrackRecord, PreferenceRecord | None]]:
        """Get tracks with their preference records."""
        async with self.session_factory() as session:
            query = (
                select(TrackRecord, PreferenceRecord)
                .outerjoin(PreferenceRecord)
            )

            if category:
                query = query.where(TrackRecord.category == category)

            query = query.order_by(PreferenceRecord.weight.desc().nullslast())
            query = query.limit(limit)

            result = await session.execute(query)
            return [(row[0], row[1]) for row in result.all()]

    async def get_recent_tracks(
        self,
        days: int = 30,
        limit: int = 50,
    ) -> list[TrackRecord]:
        """Get tracks released within the last N days."""
        async with self.session_factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            result = await session.execute(
                select(TrackRecord)
                .where(TrackRecord.release_date >= cutoff)
                .order_by(TrackRecord.release_date.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # Preference operations

    async def get_preference(self, track_id: int) -> PreferenceRecord | None:
        """Get preference record for a track."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(PreferenceRecord).where(PreferenceRecord.track_id == track_id)
            )
            return result.scalar_one_or_none()

    async def upsert_preference(
        self,
        track_id: int,
        play_count: int | None = None,
        playlist_position: int | None = None,
        in_library: bool | None = None,
        is_rated: bool | None = None,
        weight: float | None = None,
        last_played_at: datetime | None = None,
    ) -> PreferenceRecord:
        """Create or update preference record."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(PreferenceRecord).where(PreferenceRecord.track_id == track_id)
            )
            pref = result.scalar_one_or_none()

            now = datetime.now(timezone.utc)

            if pref:
                if play_count is not None:
                    # Store previous count before updating
                    pref.play_count_previous = pref.play_count
                    pref.play_count = play_count
                    # If play count increased, update last played
                    if play_count > pref.play_count_previous:
                        pref.last_played_at = now
                if playlist_position is not None:
                    if pref.playlist_position is None:
                        pref.added_to_playlist_at = now
                    pref.playlist_position = playlist_position
                if in_library is not None:
                    pref.in_library = in_library
                if is_rated is not None:
                    pref.is_rated = is_rated
                if weight is not None:
                    pref.weight = weight
                if last_played_at is not None:
                    pref.last_played_at = last_played_at
            else:
                pref = PreferenceRecord(
                    track_id=track_id,
                    play_count=play_count or 0,
                    play_count_previous=0,
                    playlist_position=playlist_position,
                    added_to_playlist_at=now if playlist_position is not None else None,
                    in_library=in_library or False,
                    is_rated=is_rated or False,
                    weight=weight or 1.0,
                    last_played_at=last_played_at,
                )
                session.add(pref)

            await session.commit()
            await session.refresh(pref)
            return pref

    async def get_top_played_tracks(self, limit: int = 50) -> list[tuple[TrackRecord, PreferenceRecord]]:
        """Get tracks with highest play counts."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrackRecord, PreferenceRecord)
                .join(PreferenceRecord)
                .where(PreferenceRecord.play_count > 0)
                .order_by(PreferenceRecord.play_count.desc())
                .limit(limit)
            )
            return [(row[0], row[1]) for row in result.all()]

    async def get_high_weight_tracks(
        self,
        min_weight: float = 1.0,
        limit: int = 50,
    ) -> list[tuple[TrackRecord, PreferenceRecord]]:
        """Get tracks with weights above threshold."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrackRecord, PreferenceRecord)
                .join(PreferenceRecord)
                .where(PreferenceRecord.weight >= min_weight)
                .order_by(PreferenceRecord.weight.desc())
                .limit(limit)
            )
            return [(row[0], row[1]) for row in result.all()]

    async def get_unplayed_hot_zone_tracks(
        self,
        hot_zone_hours: int = 48,
        hot_zone_size: int = 10,
    ) -> list[tuple[TrackRecord, PreferenceRecord]]:
        """Get tracks in hot zone with no plays (negative signal)."""
        async with self.session_factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hot_zone_hours)
            result = await session.execute(
                select(TrackRecord, PreferenceRecord)
                .join(PreferenceRecord)
                .where(
                    PreferenceRecord.playlist_position <= hot_zone_size,
                    PreferenceRecord.added_to_playlist_at <= cutoff,
                    PreferenceRecord.play_count == PreferenceRecord.play_count_previous,
                )
            )
            return [(row[0], row[1]) for row in result.all()]

    async def get_decaying_tracks(
        self,
        decay_days: int = 14,
    ) -> list[tuple[TrackRecord, PreferenceRecord]]:
        """Get tracks that haven't been played in a while."""
        async with self.session_factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=decay_days)
            result = await session.execute(
                select(TrackRecord, PreferenceRecord)
                .join(PreferenceRecord)
                .where(
                    PreferenceRecord.last_played_at.isnot(None),
                    PreferenceRecord.last_played_at < cutoff,
                )
            )
            return [(row[0], row[1]) for row in result.all()]

    # Playlist state operations

    async def get_playlist_state(self) -> PlaylistState | None:
        """Get the current playlist state."""
        async with self.session_factory() as session:
            result = await session.execute(select(PlaylistState).limit(1))
            return result.scalar_one_or_none()

    async def upsert_playlist_state(
        self,
        playlist_id: str,
        playlist_name: str,
        track_count: int = 0,
    ) -> PlaylistState:
        """Create or update playlist state."""
        async with self.session_factory() as session:
            result = await session.execute(select(PlaylistState).limit(1))
            state = result.scalar_one_or_none()

            now = datetime.now(timezone.utc)

            if state:
                state.playlist_id = playlist_id
                state.playlist_name = playlist_name
                state.track_count = track_count
                state.last_refresh_at = now
            else:
                state = PlaylistState(
                    playlist_id=playlist_id,
                    playlist_name=playlist_name,
                    track_count=track_count,
                    last_refresh_at=now,
                )
                session.add(state)

            await session.commit()
            await session.refresh(state)
            return state

    # Sync log operations

    async def log_sync(
        self,
        sync_type: str,
        status: str,
        tracks_added: int = 0,
        tracks_removed: int = 0,
        duration_seconds: float = 0.0,
        error_message: str | None = None,
    ) -> SyncLog:
        """Log a synchronization event."""
        async with self.session_factory() as session:
            log = SyncLog(
                sync_type=sync_type,
                status=status,
                tracks_added=tracks_added,
                tracks_removed=tracks_removed,
                duration_seconds=duration_seconds,
                error_message=error_message,
            )
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    async def get_recent_sync_logs(self, limit: int = 10) -> list[SyncLog]:
        """Get recent sync logs."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SyncLog).order_by(SyncLog.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    async def clear_sync_logs(self) -> int:
        """Clear all sync logs.

        Returns:
            Number of logs deleted
        """
        async with self.session_factory() as session:
            result = await session.execute(select(func.count(SyncLog.id)))
            count = result.scalar() or 0
            await session.execute(SyncLog.__table__.delete())
            await session.commit()
            return count

    # Utility methods

    async def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        async with self.session_factory() as session:
            artist_count = await session.scalar(select(func.count(ArtistRecord.id)))
            track_count = await session.scalar(select(func.count(TrackRecord.id)))
            pref_count = await session.scalar(select(func.count(PreferenceRecord.id)))
            seed_count = await session.scalar(
                select(func.count(ArtistRecord.id)).where(ArtistRecord.is_seed == True)  # noqa: E712
            )

            return {
                "artists": artist_count or 0,
                "tracks": track_count or 0,
                "preferences": pref_count or 0,
                "seed_artists": seed_count or 0,
            }
