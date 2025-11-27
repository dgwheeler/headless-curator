"""Tests for MusicBrainz client."""

import pytest

from src.musicbrainz.client import ArtistInfo


class TestArtistInfo:
    """Tests for ArtistInfo class."""

    def test_is_active(self):
        """Test is_active property."""
        active = ArtistInfo(
            mbid="123",
            name="Active Artist",
            end_year=None,
        )
        assert active.is_active is True

        inactive = ArtistInfo(
            mbid="456",
            name="Inactive Artist",
            end_year=2020,
        )
        assert inactive.is_active is False

    def test_has_recent_release(self):
        """Test recent release detection."""
        artist = ArtistInfo(
            mbid="123",
            name="Artist",
            end_year=2023,
        )

        assert artist.has_recent_release(2020) is True
        assert artist.has_recent_release(2024) is False

        # Active artist always has potential for recent releases
        active = ArtistInfo(mbid="456", name="Active", end_year=None)
        assert active.has_recent_release(2024) is True

    def test_matches_filters_gender(self):
        """Test gender filter matching."""
        male = ArtistInfo(mbid="1", name="Male Artist", gender="Male")
        female = ArtistInfo(mbid="2", name="Female Artist", gender="Female")

        assert male.matches_filters(gender="Male") is True
        assert male.matches_filters(gender="Female") is False
        assert female.matches_filters(gender="Female") is True

        # Case insensitive
        assert male.matches_filters(gender="male") is True

    def test_matches_filters_countries(self):
        """Test country filter matching."""
        uk = ArtistInfo(mbid="1", name="UK Artist", country="GB")
        us = ArtistInfo(mbid="2", name="US Artist", country="US")
        jp = ArtistInfo(mbid="3", name="JP Artist", country="JP")

        allowed = ["GB", "US", "IE"]

        assert uk.matches_filters(countries=allowed) is True
        assert us.matches_filters(countries=allowed) is True
        assert jp.matches_filters(countries=allowed) is False

    def test_matches_filters_combined(self):
        """Test combined filter matching."""
        artist = ArtistInfo(
            mbid="1",
            name="Sam Smith",
            gender="Male",
            country="GB",
            end_year=None,
        )

        # All filters match
        assert artist.matches_filters(
            gender="Male",
            countries=["GB", "US"],
            min_release_year=2020,
        ) is True

        # Gender doesn't match
        assert artist.matches_filters(
            gender="Female",
            countries=["GB", "US"],
        ) is False

        # Country doesn't match
        assert artist.matches_filters(
            gender="Male",
            countries=["US", "CA"],
        ) is False

    def test_to_dict_and_from_dict(self):
        """Test serialization/deserialization."""
        original = ArtistInfo(
            mbid="12345",
            name="Test Artist",
            gender="Male",
            country="US",
            begin_year=1990,
            end_year=None,
            disambiguation="Singer",
        )

        data = original.to_dict()
        restored = ArtistInfo.from_dict(data)

        assert restored.mbid == original.mbid
        assert restored.name == original.name
        assert restored.gender == original.gender
        assert restored.country == original.country
        assert restored.begin_year == original.begin_year
        assert restored.end_year == original.end_year
        assert restored.disambiguation == original.disambiguation

    def test_matches_filters_no_data(self):
        """Test filters when artist has no metadata."""
        artist = ArtistInfo(
            mbid="1",
            name="Unknown Artist",
            gender=None,
            country=None,
        )

        # With no data, filters should pass (we can't exclude)
        assert artist.matches_filters(gender="Male") is True
        assert artist.matches_filters(countries=["US"]) is True
