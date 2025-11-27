"""AppleScript interface to the Music app on macOS."""

import subprocess
import json
from dataclasses import dataclass
from typing import Any

from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LibrarySong:
    """A song from the user's library."""

    name: str
    artist: str
    album: str
    play_count: int
    duration_seconds: float
    database_id: int | None = None


@dataclass
class PlaylistInfo:
    """Information about a playlist."""

    name: str
    id: int | None = None
    track_count: int = 0


class AppleScriptError(Exception):
    """Error executing AppleScript."""
    pass


class MusicApp:
    """Interface to control the Music app via AppleScript."""

    def _run_applescript(self, script: str) -> str:
        """Execute an AppleScript and return the result.

        Args:
            script: AppleScript code to execute

        Returns:
            Output from the script

        Raises:
            AppleScriptError: If script execution fails
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip()
                logger.error("applescript_error", error=error_msg, script=script[:100])
                raise AppleScriptError(f"AppleScript failed: {error_msg}")

            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            raise AppleScriptError("AppleScript timed out")
        except Exception as e:
            raise AppleScriptError(f"Failed to run AppleScript: {e}")

    def _run_applescript_file(self, script: str) -> str:
        """Execute a longer AppleScript via temp file."""
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript', delete=False) as f:
            f.write(script)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["osascript", temp_path],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip()
                logger.error("applescript_error", error=error_msg)
                raise AppleScriptError(f"AppleScript failed: {error_msg}")

            return result.stdout.strip()
        finally:
            os.unlink(temp_path)

    def get_library_songs(self, limit: int = 1000) -> list[LibrarySong]:
        """Get songs from the user's Music library with play counts.

        Args:
            limit: Maximum number of songs to retrieve

        Returns:
            List of library songs sorted by play count (descending)
        """
        script = f'''
        tell application "Music"
            set songList to {{}}
            set allTracks to (every track of library playlist 1 whose media kind is song)
            set trackCount to count of allTracks
            if trackCount > {limit} then set trackCount to {limit}

            repeat with i from 1 to trackCount
                set t to item i of allTracks
                set songName to name of t
                set songArtist to artist of t
                set songAlbum to album of t
                set songPlays to played count of t
                set songDuration to duration of t
                set songId to database ID of t

                set end of songList to songName & "|||" & songArtist & "|||" & songAlbum & "|||" & (songPlays as string) & "|||" & (songDuration as string) & "|||" & (songId as string)
            end repeat

            set AppleScript's text item delimiters to "###"
            return songList as string
        end tell
        '''

        try:
            result = self._run_applescript_file(script)
            if not result:
                return []

            songs = []
            for line in result.split("###"):
                if not line.strip():
                    continue
                parts = line.split("|||")
                if len(parts) >= 6:
                    songs.append(LibrarySong(
                        name=parts[0],
                        artist=parts[1],
                        album=parts[2],
                        play_count=int(parts[3]) if parts[3] else 0,
                        duration_seconds=float(parts[4]) if parts[4] else 0,
                        database_id=int(parts[5]) if parts[5] else None,
                    ))

            # Sort by play count descending
            songs.sort(key=lambda s: s.play_count, reverse=True)
            logger.info("library_songs_retrieved", count=len(songs))
            return songs

        except AppleScriptError as e:
            logger.error("get_library_songs_failed", error=str(e))
            return []

    def get_playlist(self, name: str) -> PlaylistInfo | None:
        """Get a playlist by name.

        Args:
            name: Playlist name

        Returns:
            PlaylistInfo if found, None otherwise
        """
        # Escape quotes in name
        safe_name = name.replace('"', '\\"')

        script = f'''
        tell application "Music"
            try
                set p to user playlist "{safe_name}"
                set trackCount to count of tracks of p
                set pId to id of p
                return (pId as string) & "|||" & (trackCount as string)
            on error
                return "NOT_FOUND"
            end try
        end tell
        '''

        result = self._run_applescript(script)
        if result == "NOT_FOUND":
            return None

        parts = result.split("|||")
        return PlaylistInfo(
            name=name,
            id=int(parts[0]) if parts[0] else None,
            track_count=int(parts[1]) if len(parts) > 1 and parts[1] else 0,
        )

    def create_playlist(self, name: str, description: str = "") -> PlaylistInfo:
        """Create a new playlist.

        Args:
            name: Playlist name
            description: Optional description

        Returns:
            Created playlist info
        """
        safe_name = name.replace('"', '\\"')
        safe_desc = description.replace('"', '\\"')

        script = f'''
        tell application "Music"
            set newPlaylist to make new user playlist with properties {{name:"{safe_name}", description:"{safe_desc}"}}
            return id of newPlaylist as string
        end tell
        '''

        result = self._run_applescript(script)
        logger.info("playlist_created", name=name, id=result)

        return PlaylistInfo(name=name, id=int(result) if result else None)

    def delete_playlist(self, name: str) -> bool:
        """Delete a playlist by name.

        Args:
            name: Playlist name

        Returns:
            True if deleted, False if not found
        """
        safe_name = name.replace('"', '\\"')

        script = f'''
        tell application "Music"
            try
                delete user playlist "{safe_name}"
                return "OK"
            on error
                return "NOT_FOUND"
            end try
        end tell
        '''

        result = self._run_applescript(script)
        if result == "OK":
            logger.info("playlist_deleted", name=name)
            return True
        return False

    def clear_playlist(self, name: str) -> bool:
        """Remove all tracks from a playlist.

        Args:
            name: Playlist name

        Returns:
            True if cleared successfully
        """
        safe_name = name.replace('"', '\\"')

        script = f'''
        tell application "Music"
            try
                set p to user playlist "{safe_name}"
                delete every track of p
                return "OK"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''

        result = self._run_applescript(script)
        return result == "OK"

    def add_track_to_playlist(
        self,
        playlist_name: str,
        song_name: str,
        artist_name: str,
    ) -> bool:
        """Add a track to a playlist by searching for it.

        Searches the Apple Music catalog for the song and adds it to the playlist.

        Args:
            playlist_name: Target playlist name
            song_name: Song title to search for
            artist_name: Artist name to search for

        Returns:
            True if track was added successfully
        """
        safe_playlist = playlist_name.replace('"', '\\"')
        safe_song = song_name.replace('"', '\\"').replace("'", "'")
        safe_artist = artist_name.replace('"', '\\"').replace("'", "'")

        # Search and add from Apple Music catalog
        script = f'''
        tell application "Music"
            try
                set p to user playlist "{safe_playlist}"

                -- Search for the song in Apple Music
                set searchResults to search playlist "Library" for "{safe_song} {safe_artist}" only songs

                if (count of searchResults) > 0 then
                    -- Find best match (artist name contains our artist)
                    repeat with t in searchResults
                        if (artist of t) contains "{safe_artist}" or "{safe_artist}" contains (artist of t) then
                            duplicate t to p
                            return "OK"
                        end if
                    end repeat
                    -- If no artist match, try first result
                    duplicate item 1 of searchResults to p
                    return "OK"
                else
                    return "NOT_FOUND"
                end if
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''

        result = self._run_applescript(script)
        if result == "OK":
            return True
        elif result == "NOT_FOUND":
            logger.debug("track_not_found_in_library", song=song_name, artist=artist_name)
            return False
        else:
            logger.warning("add_track_failed", song=song_name, error=result)
            return False

    def add_tracks_to_playlist_batch(
        self,
        playlist_name: str,
        tracks: list[tuple[str, str]],
    ) -> int:
        """Add multiple tracks to a playlist.

        Args:
            playlist_name: Target playlist name
            tracks: List of (song_name, artist_name) tuples

        Returns:
            Number of tracks successfully added
        """
        added = 0
        for song_name, artist_name in tracks:
            if self.add_track_to_playlist(playlist_name, song_name, artist_name):
                added += 1

        logger.info("tracks_added_to_playlist", playlist=playlist_name, added=added, total=len(tracks))
        return added

    def search_and_add_apple_music_track(
        self,
        playlist_name: str,
        song_name: str,
        artist_name: str,
    ) -> bool:
        """Search Apple Music catalog and add track to playlist.

        This searches the Apple Music streaming catalog (not local library).

        Args:
            playlist_name: Target playlist name
            song_name: Song title
            artist_name: Artist name

        Returns:
            True if track was added
        """
        safe_playlist = playlist_name.replace('"', '\\"')
        # Clean up song/artist names for search
        safe_song = song_name.replace('"', '\\"').replace("'", "")
        safe_artist = artist_name.replace('"', '\\"').replace("'", "")
        search_term = f"{safe_song} {safe_artist}"

        script = f'''
        tell application "Music"
            try
                set p to user playlist "{safe_playlist}"

                -- Search Apple Music subscription catalog
                set searchTerm to "{search_term}"
                set foundTracks to search playlist "Library" for searchTerm only songs

                if (count of foundTracks) > 0 then
                    duplicate item 1 of foundTracks to p
                    return "LIBRARY"
                end if

                return "NOT_IN_LIBRARY"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''

        result = self._run_applescript(script)

        if result == "LIBRARY":
            return True
        elif result == "NOT_IN_LIBRARY":
            # Track not in library - we'll need to add it via URL or different method
            logger.debug("track_not_in_library", song=song_name, artist=artist_name)
            return False
        else:
            logger.warning("search_add_failed", song=song_name, error=result)
            return False

    def add_track_by_apple_music_url(
        self,
        playlist_name: str,
        apple_music_url: str,
    ) -> bool:
        """Add a track to playlist using its Apple Music URL.

        Args:
            playlist_name: Target playlist name
            apple_music_url: Full Apple Music URL for the song

        Returns:
            True if added successfully
        """
        safe_playlist = playlist_name.replace('"', '\\"')

        script = f'''
        tell application "Music"
            try
                set p to user playlist "{safe_playlist}"
                open location "{apple_music_url}"
                delay 2
                set currentTrack to current track
                duplicate currentTrack to p
                stop
                return "OK"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''

        result = self._run_applescript(script)
        return result == "OK"
