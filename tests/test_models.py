"""Tests for Pydantic models."""

import pytest
from datetime import datetime

from src.apple_music.models import (
    Artist,
    ArtistAttributes,
    Track,
    TrackAttributes,
    LibraryTrack,
    LibraryTrackAttributes,
    Playlist,
    PlaylistAttributes,
    SearchResults,
)


class TestArtistModel:
    """Tests for Artist model."""

    def test_artist_from_dict(self):
        """Test creating Artist from API response dict."""
        data = {
            "id": "12345",
            "type": "artists",
            "href": "/v1/catalog/us/artists/12345",
            "attributes": {
                "name": "Sam Smith",
                "genreNames": ["Pop", "Soul"],
                "url": "https://music.apple.com/us/artist/sam-smith/12345",
            },
        }

        artist = Artist(**data)

        assert artist.id == "12345"
        assert artist.type == "artists"
        assert artist.name == "Sam Smith"
        assert artist.attributes.genre_names == ["Pop", "Soul"]

    def test_artist_without_attributes(self):
        """Test Artist with missing attributes."""
        artist = Artist(id="12345", type="artists")

        assert artist.name == ""


class TestTrackModel:
    """Tests for Track model."""

    def test_track_from_dict(self):
        """Test creating Track from API response dict."""
        data = {
            "id": "t12345",
            "type": "songs",
            "attributes": {
                "name": "Stay With Me",
                "artistName": "Sam Smith",
                "albumName": "In the Lonely Hour",
                "durationInMillis": 172000,
                "releaseDate": "2014-05-26",
                "genreNames": ["Pop"],
                "isrc": "GBUM71400903",
            },
        }

        track = Track(**data)

        assert track.id == "t12345"
        assert track.name == "Stay With Me"
        assert track.artist_name == "Sam Smith"
        assert track.album_name == "In the Lonely Hour"
        assert track.isrc == "GBUM71400903"
        assert track.attributes.duration_in_millis == 172000

    def test_track_release_datetime(self):
        """Test release date parsing."""
        data = {
            "id": "t12345",
            "type": "songs",
            "attributes": {
                "name": "Test",
                "artistName": "Artist",
                "albumName": "Album",
                "durationInMillis": 100000,
                "releaseDate": "2024-01-15",
            },
        }

        track = Track(**data)

        assert track.attributes.release_datetime is not None
        assert track.attributes.release_datetime.year == 2024
        assert track.attributes.release_datetime.month == 1
        assert track.attributes.release_datetime.day == 15


class TestLibraryTrackModel:
    """Tests for LibraryTrack model."""

    def test_library_track_with_play_count(self):
        """Test LibraryTrack with play count."""
        data = {
            "id": "l12345",
            "type": "library-songs",
            "attributes": {
                "name": "Favorite Song",
                "artistName": "Great Artist",
                "albumName": "Best Album",
                "playCount": 42,
                "durationInMillis": 200000,
            },
        }

        track = LibraryTrack(**data)

        assert track.id == "l12345"
        assert track.name == "Favorite Song"
        assert track.play_count == 42

    def test_library_track_default_play_count(self):
        """Test LibraryTrack with missing play count."""
        data = {
            "id": "l12345",
            "type": "library-songs",
            "attributes": {
                "name": "New Song",
                "artistName": "Artist",
                "albumName": "Album",
                "durationInMillis": 180000,
            },
        }

        track = LibraryTrack(**data)

        assert track.play_count == 0


class TestSearchResults:
    """Tests for SearchResults model."""

    def test_empty_search_results(self):
        """Test empty search results."""
        results = SearchResults()

        assert results.artists == []
        assert results.songs == []

    def test_search_results_with_data(self):
        """Test search results with artists and songs."""
        results = SearchResults(
            artists=[
                Artist(id="a1", type="artists"),
                Artist(id="a2", type="artists"),
            ],
            songs=[
                Track(id="t1", type="songs"),
            ],
        )

        assert len(results.artists) == 2
        assert len(results.songs) == 1
