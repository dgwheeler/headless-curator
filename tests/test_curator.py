"""Tests for the core curator algorithm."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.apple_music.models import Track, TrackAttributes
from src.curator import Curator, PlaylistCategory
from src.utils.config import Settings, AlgorithmWeights, AlgorithmConfig


class TestPlaylistBuilding:
    """Tests for playlist construction algorithm."""

    def create_mock_track(self, track_id: str, name: str = "Test Track") -> Track:
        """Create a mock Track object."""
        return Track(
            id=track_id,
            type="songs",
            attributes=TrackAttributes(
                name=name,
                artistName="Test Artist",
                albumName="Test Album",
                durationInMillis=200000,
            ),
        )

    def test_build_playlist_respects_weights(self):
        """Test that playlist respects category weight distribution."""
        settings = Settings()
        settings.algorithm.playlist_size = 100
        settings.algorithm.weights = AlgorithmWeights(
            favorites=0.40,
            hits=0.30,
            discovery=0.20,
            wildcard=0.10,
        )

        # Create mock tracks for each category
        favorites = [self.create_mock_track(f"fav_{i}") for i in range(50)]
        hits = [self.create_mock_track(f"hit_{i}") for i in range(50)]
        discovery = [self.create_mock_track(f"disc_{i}") for i in range(50)]
        wildcard = [self.create_mock_track(f"wild_{i}") for i in range(50)]

        # Build playlist without full Curator (just test the algorithm)
        from src.curator import Curator

        # Create a minimal curator instance for testing
        with patch.object(Curator, "__init__", lambda self, *args: None):
            curator = Curator(None, None)
            curator.settings = settings

            playlist = curator.build_playlist(favorites, hits, discovery, wildcard)

        # Check total size
        assert len(playlist) == 100

        # Check distribution (count by prefix)
        fav_count = sum(1 for tid in playlist if tid.startswith("fav_"))
        hit_count = sum(1 for tid in playlist if tid.startswith("hit_"))
        disc_count = sum(1 for tid in playlist if tid.startswith("disc_"))
        wild_count = sum(1 for tid in playlist if tid.startswith("wild_"))

        # Should approximately match weights (within a few tracks due to interleaving)
        assert fav_count == 40
        assert hit_count == 30
        assert disc_count == 20
        assert wild_count == 10

    def test_build_playlist_handles_insufficient_tracks(self):
        """Test playlist building when categories don't have enough tracks."""
        settings = Settings()
        settings.algorithm.playlist_size = 50
        settings.algorithm.weights = AlgorithmWeights(
            favorites=0.40,  # 20 tracks
            hits=0.30,  # 15 tracks
            discovery=0.20,  # 10 tracks
            wildcard=0.10,  # 5 tracks
        )

        # Only provide limited tracks per category
        favorites = [self.create_mock_track(f"fav_{i}") for i in range(10)]  # Only 10
        hits = [self.create_mock_track(f"hit_{i}") for i in range(15)]
        discovery = [self.create_mock_track(f"disc_{i}") for i in range(10)]
        wildcard = [self.create_mock_track(f"wild_{i}") for i in range(3)]  # Only 3

        with patch.object(Curator, "__init__", lambda self, *args: None):
            curator = Curator(None, None)
            curator.settings = settings

            playlist = curator.build_playlist(favorites, hits, discovery, wildcard)

        # Should use all available tracks
        assert len(playlist) == 38  # 10 + 15 + 10 + 3

    def test_build_playlist_interleaves_categories(self):
        """Test that playlist interleaves tracks from different categories."""
        settings = Settings()
        settings.algorithm.playlist_size = 20
        settings.algorithm.weights = AlgorithmWeights(
            favorites=0.25,
            hits=0.25,
            discovery=0.25,
            wildcard=0.25,
        )

        favorites = [self.create_mock_track(f"fav_{i}") for i in range(10)]
        hits = [self.create_mock_track(f"hit_{i}") for i in range(10)]
        discovery = [self.create_mock_track(f"disc_{i}") for i in range(10)]
        wildcard = [self.create_mock_track(f"wild_{i}") for i in range(10)]

        with patch.object(Curator, "__init__", lambda self, *args: None):
            curator = Curator(None, None)
            curator.settings = settings

            playlist = curator.build_playlist(favorites, hits, discovery, wildcard)

        # Check that same category doesn't appear consecutively too often
        max_consecutive = 0
        current_consecutive = 1
        last_prefix = None

        for tid in playlist:
            prefix = tid.split("_")[0]
            if prefix == last_prefix:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 1
            last_prefix = prefix

        # With proper interleaving, we shouldn't have more than 2 consecutive
        # from the same category (unless category runs out)
        assert max_consecutive <= 3

    def test_build_playlist_empty_categories(self):
        """Test playlist building with some empty categories."""
        settings = Settings()
        settings.algorithm.playlist_size = 20
        settings.algorithm.weights = AlgorithmWeights(
            favorites=0.40,
            hits=0.30,
            discovery=0.20,
            wildcard=0.10,
        )

        favorites = [self.create_mock_track(f"fav_{i}") for i in range(20)]
        hits = []  # Empty
        discovery = [self.create_mock_track(f"disc_{i}") for i in range(10)]
        wildcard = []  # Empty

        with patch.object(Curator, "__init__", lambda self, *args: None):
            curator = Curator(None, None)
            curator.settings = settings

            playlist = curator.build_playlist(favorites, hits, discovery, wildcard)

        # Should only contain tracks from non-empty categories
        assert len(playlist) == 18  # 8 favorites + 10 discovery (or less if limited by targets)
        assert all(tid.startswith(("fav_", "disc_")) for tid in playlist)


class TestPlaylistCategory:
    """Tests for PlaylistCategory constants."""

    def test_category_values(self):
        """Test that category constants have expected values."""
        assert PlaylistCategory.FAVORITES == "favorites"
        assert PlaylistCategory.HITS == "hits"
        assert PlaylistCategory.DISCOVERY == "discovery"
        assert PlaylistCategory.WILDCARD == "wildcard"


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_algorithm_weights_validation(self):
        """Test that weights are validated to be between 0 and 1."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AlgorithmWeights(favorites=1.5, hits=0.3, discovery=0.2, wildcard=0.1)

        with pytest.raises(ValidationError):
            AlgorithmWeights(favorites=-0.1, hits=0.3, discovery=0.2, wildcard=0.1)

    def test_default_settings(self):
        """Test default settings values."""
        settings = Settings()

        assert settings.user.name == "Grace"
        assert settings.user.playlist_name == "Grace's Station"
        assert settings.algorithm.playlist_size == 50
        assert settings.algorithm.weights.favorites == 0.40
        assert settings.schedule.refresh_time == "03:00"
        assert settings.schedule.timezone == "America/Los_Angeles"
