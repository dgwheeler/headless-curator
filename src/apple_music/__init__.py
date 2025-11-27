"""Apple Music API integration."""

from .auth import AppleMusicAuth
from .client import AppleMusicClient
from .models import Artist, Track, Playlist, LibraryTrack

__all__ = ["AppleMusicAuth", "AppleMusicClient", "Artist", "Track", "Playlist", "LibraryTrack"]
