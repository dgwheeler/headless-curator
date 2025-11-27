"""Pydantic models for Apple Music API responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Artwork(BaseModel):
    """Album or playlist artwork."""

    width: int | None = None
    height: int | None = None
    url: str | None = None
    bg_color: str | None = Field(None, alias="bgColor")
    text_color1: str | None = Field(None, alias="textColor1")


class PlayParameters(BaseModel):
    """Parameters for playing content."""

    id: str
    kind: str


class ArtistAttributes(BaseModel):
    """Attributes for an Artist resource."""

    name: str
    genre_names: list[str] = Field(default_factory=list, alias="genreNames")
    url: str | None = None
    artwork: Artwork | None = None


class Artist(BaseModel):
    """Apple Music Artist resource."""

    id: str
    type: str = "artists"
    href: str | None = None
    attributes: ArtistAttributes | None = None

    @property
    def name(self) -> str:
        return self.attributes.name if self.attributes else ""


class TrackAttributes(BaseModel):
    """Attributes for a Song/Track resource."""

    name: str
    artist_name: str = Field(alias="artistName")
    album_name: str = Field(alias="albumName")
    duration_in_millis: int = Field(alias="durationInMillis")
    release_date: str | None = Field(None, alias="releaseDate")
    genre_names: list[str] = Field(default_factory=list, alias="genreNames")
    isrc: str | None = None
    url: str | None = None
    artwork: Artwork | None = None
    play_params: PlayParameters | None = Field(None, alias="playParams")
    previews: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def release_datetime(self) -> datetime | None:
        if self.release_date:
            try:
                return datetime.fromisoformat(self.release_date)
            except ValueError:
                # Handle partial dates like "2024-01-01"
                try:
                    return datetime.strptime(self.release_date, "%Y-%m-%d")
                except ValueError:
                    return None
        return None


class Track(BaseModel):
    """Apple Music Song/Track resource."""

    id: str
    type: str = "songs"
    href: str | None = None
    attributes: TrackAttributes | None = None

    @property
    def name(self) -> str:
        return self.attributes.name if self.attributes else ""

    @property
    def artist_name(self) -> str:
        return self.attributes.artist_name if self.attributes else ""

    @property
    def album_name(self) -> str:
        return self.attributes.album_name if self.attributes else ""

    @property
    def isrc(self) -> str | None:
        return self.attributes.isrc if self.attributes else None


class LibraryTrackAttributes(BaseModel):
    """Attributes for a library track (includes play count)."""

    name: str = ""
    artist_name: str = Field("", alias="artistName")
    album_name: str = Field("", alias="albumName")
    play_count: int = Field(0, alias="playCount")
    date_added: str | None = Field(None, alias="dateAdded")
    duration_in_millis: int = Field(0, alias="durationInMillis")


class LibraryTrack(BaseModel):
    """Apple Music Library Track resource (user's library)."""

    id: str
    type: str = "library-songs"
    href: str | None = None
    attributes: LibraryTrackAttributes | None = None

    @property
    def name(self) -> str:
        return self.attributes.name if self.attributes else ""

    @property
    def play_count(self) -> int:
        return self.attributes.play_count if self.attributes else 0


class PlaylistAttributes(BaseModel):
    """Attributes for a Playlist resource."""

    name: str
    description: dict[str, str] | None = None
    is_public: bool = Field(False, alias="isPublic")
    can_edit: bool = Field(False, alias="canEdit")
    date_added: str | None = Field(None, alias="dateAdded")
    last_modified_date: str | None = Field(None, alias="lastModifiedDate")
    artwork: Artwork | None = None
    play_params: PlayParameters | None = Field(None, alias="playParams")


class Playlist(BaseModel):
    """Apple Music Playlist resource."""

    id: str
    type: str = "playlists"
    href: str | None = None
    attributes: PlaylistAttributes | None = None

    @property
    def name(self) -> str:
        return self.attributes.name if self.attributes else ""


class LibraryPlaylistAttributes(BaseModel):
    """Attributes for a library playlist."""

    name: str
    description: dict[str, str] | None = None
    can_edit: bool = Field(True, alias="canEdit")
    date_added: str | None = Field(None, alias="dateAdded")
    has_catalog: bool = Field(False, alias="hasCatalog")


class LibraryPlaylist(BaseModel):
    """Apple Music Library Playlist resource."""

    id: str
    type: str = "library-playlists"
    href: str | None = None
    attributes: LibraryPlaylistAttributes | None = None

    @property
    def name(self) -> str:
        return self.attributes.name if self.attributes else ""


class SearchResults(BaseModel):
    """Search results container."""

    artists: list[Artist] = Field(default_factory=list)
    songs: list[Track] = Field(default_factory=list)


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""

    data: list[Any] = Field(default_factory=list)
    next: str | None = None
    meta: dict[str, Any] | None = None
