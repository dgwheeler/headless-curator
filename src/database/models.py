"""SQLAlchemy database models for preference tracking."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class ArtistRecord(Base):
    """Cached artist metadata."""

    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apple_music_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    musicbrainz_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    tracks: Mapped[list["TrackRecord"]] = relationship("TrackRecord", back_populates="artist")

    __table_args__ = (Index("idx_artists_name", "name"),)


class TrackRecord(Base):
    """Track metadata and preference data."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apple_music_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    isrc: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    artist_name: Mapped[str] = mapped_column(String(256), nullable=False)
    album_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    release_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Artist relationship
    artist_id: Mapped[int | None] = mapped_column(ForeignKey("artists.id"), nullable=True)
    artist: Mapped[ArtistRecord | None] = relationship("ArtistRecord", back_populates="tracks")

    # Category for playlist generation
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)  # favorites, hits, discovery, wildcard

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    preferences: Mapped[list["PreferenceRecord"]] = relationship(
        "PreferenceRecord", back_populates="track", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_tracks_artist_name", "artist_name"),
        Index("idx_tracks_isrc", "isrc"),
        Index("idx_tracks_category", "category"),
        Index("idx_tracks_release_date", "release_date"),
    )


class PreferenceRecord(Base):
    """User preference signals for tracks."""

    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False)
    track: Mapped[TrackRecord] = relationship("TrackRecord", back_populates="preferences")

    # Play count tracking
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    play_count_previous: Mapped[int] = mapped_column(Integer, default=0)

    # Playlist position tracking (for negative signal detection)
    playlist_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_to_playlist_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # User signals
    in_library: Mapped[bool] = mapped_column(Boolean, default=False)
    is_rated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Calculated weight
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    # Last time this track was played (for decay calculation)
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("idx_preferences_weight", "weight"),
        Index("idx_preferences_play_count", "play_count"),
    )


class PlaylistState(Base):
    """State of the managed playlist."""

    __tablename__ = "playlist_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[str] = mapped_column(String(64), nullable=False)
    playlist_name: Mapped[str] = mapped_column(String(256), nullable=False)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    track_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SyncLog(Base):
    """Log of synchronization events."""

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_type: Mapped[str] = mapped_column(String(32), nullable=False)  # refresh, discovery, learning
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # success, failure, partial
    tracks_added: Mapped[int] = mapped_column(Integer, default=0)
    tracks_removed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("idx_sync_logs_created", "created_at"),)
