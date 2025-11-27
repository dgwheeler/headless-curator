"""Tests for database models and repository."""

import pytest
from datetime import datetime, timedelta, timezone

from src.database.models import ArtistRecord, TrackRecord, PreferenceRecord
from src.database.repository import Repository


@pytest.fixture
async def repository():
    """Create an in-memory database for testing."""
    repo = Repository("sqlite+aiosqlite:///:memory:")
    await repo.init_db()
    yield repo
    await repo.close()


class TestArtistOperations:
    """Tests for artist CRUD operations."""

    @pytest.mark.asyncio
    async def test_upsert_artist_create(self, repository):
        """Test creating a new artist."""
        artist = await repository.upsert_artist(
            apple_music_id="12345",
            name="Test Artist",
            gender="Male",
            country="US",
            is_seed=True,
        )

        assert artist.id is not None
        assert artist.apple_music_id == "12345"
        assert artist.name == "Test Artist"
        assert artist.gender == "Male"
        assert artist.country == "US"
        assert artist.is_seed is True

    @pytest.mark.asyncio
    async def test_upsert_artist_update(self, repository):
        """Test updating an existing artist."""
        # Create initial
        await repository.upsert_artist(
            apple_music_id="12345",
            name="Test Artist",
        )

        # Update
        artist = await repository.upsert_artist(
            apple_music_id="12345",
            name="Updated Name",
            gender="Male",
        )

        assert artist.name == "Updated Name"
        assert artist.gender == "Male"

    @pytest.mark.asyncio
    async def test_get_artist_by_apple_id(self, repository):
        """Test retrieving artist by Apple Music ID."""
        await repository.upsert_artist(
            apple_music_id="12345",
            name="Test Artist",
        )

        artist = await repository.get_artist_by_apple_id("12345")
        assert artist is not None
        assert artist.name == "Test Artist"

        # Non-existent
        none_artist = await repository.get_artist_by_apple_id("99999")
        assert none_artist is None

    @pytest.mark.asyncio
    async def test_get_seed_artists(self, repository):
        """Test retrieving seed artists only."""
        await repository.upsert_artist(
            apple_music_id="1",
            name="Seed Artist 1",
            is_seed=True,
        )
        await repository.upsert_artist(
            apple_music_id="2",
            name="Seed Artist 2",
            is_seed=True,
        )
        await repository.upsert_artist(
            apple_music_id="3",
            name="Non-Seed Artist",
            is_seed=False,
        )

        seeds = await repository.get_seed_artists()
        assert len(seeds) == 2
        assert all(a.is_seed for a in seeds)


class TestTrackOperations:
    """Tests for track CRUD operations."""

    @pytest.mark.asyncio
    async def test_upsert_track_create(self, repository):
        """Test creating a new track."""
        track = await repository.upsert_track(
            apple_music_id="t12345",
            name="Test Song",
            artist_name="Test Artist",
            album_name="Test Album",
            isrc="USRC12345678",
            duration_ms=200000,
            category="favorites",
        )

        assert track.id is not None
        assert track.apple_music_id == "t12345"
        assert track.name == "Test Song"
        assert track.category == "favorites"

    @pytest.mark.asyncio
    async def test_get_tracks_by_category(self, repository):
        """Test retrieving tracks by category."""
        for i in range(5):
            await repository.upsert_track(
                apple_music_id=f"fav_{i}",
                name=f"Favorite {i}",
                artist_name="Artist",
                category="favorites",
            )
        for i in range(3):
            await repository.upsert_track(
                apple_music_id=f"hit_{i}",
                name=f"Hit {i}",
                artist_name="Artist",
                category="hits",
            )

        favorites = await repository.get_tracks_by_category("favorites")
        assert len(favorites) == 5

        hits = await repository.get_tracks_by_category("hits")
        assert len(hits) == 3


class TestPreferenceOperations:
    """Tests for preference tracking."""

    @pytest.mark.asyncio
    async def test_upsert_preference_create(self, repository):
        """Test creating a new preference record."""
        track = await repository.upsert_track(
            apple_music_id="t1",
            name="Test",
            artist_name="Artist",
        )

        pref = await repository.upsert_preference(
            track_id=track.id,
            play_count=10,
            in_library=True,
        )

        assert pref.id is not None
        assert pref.track_id == track.id
        assert pref.play_count == 10
        assert pref.in_library is True

    @pytest.mark.asyncio
    async def test_preference_play_count_tracking(self, repository):
        """Test that previous play count is tracked on update."""
        track = await repository.upsert_track(
            apple_music_id="t1",
            name="Test",
            artist_name="Artist",
        )

        # Initial preference
        await repository.upsert_preference(
            track_id=track.id,
            play_count=5,
        )

        # Update with higher count
        pref = await repository.upsert_preference(
            track_id=track.id,
            play_count=10,
        )

        assert pref.play_count == 10
        assert pref.play_count_previous == 5

    @pytest.mark.asyncio
    async def test_get_top_played_tracks(self, repository):
        """Test retrieving top played tracks."""
        for i in range(5):
            track = await repository.upsert_track(
                apple_music_id=f"t{i}",
                name=f"Track {i}",
                artist_name="Artist",
            )
            await repository.upsert_preference(
                track_id=track.id,
                play_count=(i + 1) * 10,  # 10, 20, 30, 40, 50
            )

        top = await repository.get_top_played_tracks(limit=3)

        assert len(top) == 3
        # Should be in descending order
        assert top[0][1].play_count == 50
        assert top[1][1].play_count == 40
        assert top[2][1].play_count == 30


class TestStats:
    """Tests for database statistics."""

    @pytest.mark.asyncio
    async def test_get_stats(self, repository):
        """Test retrieving database statistics."""
        # Add some data
        for i in range(3):
            await repository.upsert_artist(
                apple_music_id=f"a{i}",
                name=f"Artist {i}",
                is_seed=(i == 0),
            )

        for i in range(5):
            track = await repository.upsert_track(
                apple_music_id=f"t{i}",
                name=f"Track {i}",
                artist_name="Artist",
            )
            await repository.upsert_preference(track_id=track.id, play_count=i)

        stats = await repository.get_stats()

        assert stats["artists"] == 3
        assert stats["tracks"] == 5
        assert stats["preferences"] == 5
        assert stats["seed_artists"] == 1
