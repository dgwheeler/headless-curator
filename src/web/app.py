"""FastAPI web application for Headless Curator management."""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.apple_music import AppleMusicAuth
from src.database import Repository
from src.utils.config import Settings, load_config, save_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Template directory
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(config_path: str = "config.yaml") -> FastAPI:
    """Create the FastAPI application.

    Args:
        config_path: Path to configuration file

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(
        title="Headless Curator",
        description="Apple Music playlist curator management interface",
        version="1.0.0",
    )

    # Store config path in app state
    app.state.config_path = config_path

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """Main dashboard page."""
        settings = load_config(app.state.config_path)
        repository = Repository(settings.database.url)

        try:
            await repository.init_db()
            stats = await repository.get_stats()
            sync_logs = await repository.get_recent_sync_logs(limit=5)
            playlist_state = await repository.get_playlist_state()
        finally:
            await repository.close()

        # Check auth status
        auth_status = await _check_auth_status(settings)

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "settings": settings,
                "stats": stats,
                "sync_logs": sync_logs,
                "playlist_state": playlist_state,
                "auth_status": auth_status,
            },
        )

    @app.get("/artists", response_class=HTMLResponse)
    async def artists_page(request: Request) -> HTMLResponse:
        """Seed artists management page."""
        settings = load_config(app.state.config_path)

        return templates.TemplateResponse(
            "artists.html",
            {
                "request": request,
                "artists": settings.seeds.artists,
                "songs": settings.seeds.songs,
            },
        )

    @app.post("/artists/add")
    async def add_artist(artist: str = Form(...)) -> RedirectResponse:
        """Add a seed artist."""
        settings = load_config(app.state.config_path)

        if artist.strip() and artist.strip() not in settings.seeds.artists:
            settings.seeds.artists.append(artist.strip())
            save_config(settings, app.state.config_path)
            logger.info("artist_added", artist=artist.strip())

        return RedirectResponse(url="/artists", status_code=303)

    @app.post("/artists/remove")
    async def remove_artist(artist: str = Form(...)) -> RedirectResponse:
        """Remove a seed artist."""
        settings = load_config(app.state.config_path)

        if artist in settings.seeds.artists:
            settings.seeds.artists.remove(artist)
            save_config(settings, app.state.config_path)
            logger.info("artist_removed", artist=artist)

        return RedirectResponse(url="/artists", status_code=303)

    @app.post("/songs/add")
    async def add_song(song: str = Form(...)) -> RedirectResponse:
        """Add a seed song."""
        settings = load_config(app.state.config_path)

        if song.strip() and song.strip() not in settings.seeds.songs:
            settings.seeds.songs.append(song.strip())
            save_config(settings, app.state.config_path)
            logger.info("song_added", song=song.strip())

        return RedirectResponse(url="/artists", status_code=303)

    @app.post("/songs/remove")
    async def remove_song(song: str = Form(...)) -> RedirectResponse:
        """Remove a seed song."""
        settings = load_config(app.state.config_path)

        if song in settings.seeds.songs:
            settings.seeds.songs.remove(song)
            save_config(settings, app.state.config_path)
            logger.info("song_removed", song=song)

        return RedirectResponse(url="/artists", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        """Settings page."""
        settings = load_config(app.state.config_path)

        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "settings": settings,
            },
        )

    @app.post("/settings/update")
    async def update_settings(
        playlist_name: str = Form(...),
        playlist_size: int = Form(...),
        weight_favorites: float = Form(...),
        weight_hits: float = Form(...),
        weight_discovery: float = Form(...),
        weight_wildcard: float = Form(...),
    ) -> RedirectResponse:
        """Update algorithm settings."""
        settings = load_config(app.state.config_path)

        settings.user.playlist_name = playlist_name
        settings.algorithm.playlist_size = playlist_size
        settings.algorithm.weights.favorites = weight_favorites
        settings.algorithm.weights.hits = weight_hits
        settings.algorithm.weights.discovery = weight_discovery
        settings.algorithm.weights.wildcard = weight_wildcard

        save_config(settings, app.state.config_path)
        logger.info("settings_updated")

        return RedirectResponse(url="/settings", status_code=303)

    @app.get("/auth", response_class=HTMLResponse)
    async def auth_page(request: Request) -> HTMLResponse:
        """Authentication status page."""
        settings = load_config(app.state.config_path)
        auth_status = await _check_auth_status(settings)

        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "auth_status": auth_status,
                "settings": settings,
            },
        )

    @app.post("/auth/start")
    async def start_auth() -> dict[str, str]:
        """Start Apple Music authentication flow."""
        settings = load_config(app.state.config_path)

        auth = AppleMusicAuth(
            team_id=settings.apple_music.team_id,
            key_id=settings.apple_music.key_id,
            private_key_path=settings.apple_music.private_key_path_resolved,
        )

        # Generate developer token
        dev_token = auth.generate_developer_token()

        # Return the auth URL for the user to visit
        auth_url = f"https://authorize.music.apple.com/?clientId={settings.apple_music.team_id}&responseType=code&state=curator"

        return {
            "status": "redirect",
            "message": "Please run 'curator auth' in terminal to complete authentication",
            "note": "Web-based auth requires running the CLI command",
        }

    @app.post("/refresh")
    async def trigger_refresh() -> dict[str, Any]:
        """Trigger a manual playlist refresh."""
        from src.curator import Curator

        settings = load_config(app.state.config_path)
        repository = Repository(settings.database.url)

        curator = Curator(settings, repository)
        try:
            result = await curator.refresh_playlist()
            return {"status": "success", "result": result}
        except Exception as e:
            logger.error("refresh_failed", error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            await curator.close()

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        """API endpoint for status check."""
        settings = load_config(app.state.config_path)
        auth_status = await _check_auth_status(settings)

        repository = Repository(settings.database.url)
        try:
            await repository.init_db()
            stats = await repository.get_stats()
            playlist_state = await repository.get_playlist_state()
        finally:
            await repository.close()

        return {
            "auth": auth_status,
            "stats": stats,
            "playlist": {
                "name": playlist_state.playlist_name if playlist_state else None,
                "track_count": playlist_state.track_count if playlist_state else 0,
                "last_refresh": playlist_state.last_refresh_at.isoformat() if playlist_state else None,
            },
        }

    @app.post("/api/logs/clear")
    async def clear_logs() -> dict[str, Any]:
        """Clear all sync logs."""
        settings = load_config(app.state.config_path)
        repository = Repository(settings.database.url)

        try:
            await repository.init_db()
            count = await repository.clear_sync_logs()
            logger.info("sync_logs_cleared", count=count)
            return {"status": "success", "cleared": count}
        except Exception as e:
            logger.error("clear_logs_failed", error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            await repository.close()

    @app.get("/playlist", response_class=HTMLResponse)
    async def playlist_page(request: Request) -> HTMLResponse:
        """View current playlist tracks."""
        settings = load_config(app.state.config_path)
        auth_status = await _check_auth_status(settings)

        tracks = []
        playlist_name = settings.user.playlist_name
        playlist_id = None
        error_message = None

        if auth_status["valid"]:
            try:
                from src.apple_music import AppleMusicAuth, AppleMusicClient

                auth = AppleMusicAuth(
                    team_id=settings.apple_music.team_id,
                    key_id=settings.apple_music.key_id,
                    private_key_path=settings.apple_music.private_key_path_resolved,
                )
                client = AppleMusicClient(
                    auth=auth,
                    storefront=settings.apple_music.storefront,
                )

                async with client:
                    # Get all playlists and find the one with tracks
                    all_playlists = await client.get_library_playlists()
                    matching = [p for p in all_playlists if p.name == playlist_name]

                    # If multiple playlists with same name, find one with tracks
                    playlist = None
                    for p in matching:
                        playlist_tracks = await client.get_library_playlist_tracks(p.id)
                        if playlist_tracks:
                            playlist = p
                            playlist_id = p.id
                            for t in playlist_tracks:
                                if t.attributes:
                                    tracks.append({
                                        "id": t.id,
                                        "name": t.name,
                                        "artist": t.attributes.artist_name,
                                        "album": t.attributes.album_name,
                                    })
                            break

                    if not playlist and matching:
                        playlist = matching[0]
            except Exception as e:
                error_message = str(e)
                logger.error("playlist_fetch_failed", error=str(e))

        return templates.TemplateResponse(
            "playlist.html",
            {
                "request": request,
                "tracks": tracks,
                "playlist_name": playlist_name,
                "playlist_id": playlist_id,
                "auth_status": auth_status,
                "error_message": error_message,
            },
        )

    @app.get("/api/search")
    async def search_tracks(q: str) -> dict[str, Any]:
        """Search Apple Music catalog for tracks."""
        settings = load_config(app.state.config_path)

        try:
            from src.apple_music import AppleMusicAuth, AppleMusicClient

            auth = AppleMusicAuth(
                team_id=settings.apple_music.team_id,
                key_id=settings.apple_music.key_id,
                private_key_path=settings.apple_music.private_key_path_resolved,
            )
            client = AppleMusicClient(
                auth=auth,
                storefront=settings.apple_music.storefront,
            )

            async with client:
                results = await client.search(q, types=["songs"], limit=10)
                tracks = []
                for t in results.songs:
                    if t.attributes:
                        tracks.append({
                            "id": t.id,
                            "name": t.name,
                            "artist": t.artist_name,
                            "album": t.album_name,
                        })
                return {"status": "success", "tracks": tracks}
        except Exception as e:
            logger.error("search_failed", error=str(e))
            return {"status": "error", "message": str(e)}

    @app.post("/playlist/add")
    async def add_track_to_playlist(track_id: str = Form(...)) -> dict[str, Any]:
        """Add a track to the playlist."""
        settings = load_config(app.state.config_path)
        playlist_name = settings.user.playlist_name

        try:
            from src.apple_music import AppleMusicAuth, AppleMusicClient

            auth = AppleMusicAuth(
                team_id=settings.apple_music.team_id,
                key_id=settings.apple_music.key_id,
                private_key_path=settings.apple_music.private_key_path_resolved,
            )
            client = AppleMusicClient(
                auth=auth,
                storefront=settings.apple_music.storefront,
            )

            async with client:
                # Get the playlist
                playlist = await client.get_library_playlist_by_name(playlist_name)
                if playlist:
                    # Add track to playlist
                    await client.add_tracks_to_library_playlist(playlist.id, [track_id])
                    logger.info("track_added_to_playlist", track_id=track_id)
                    return {"status": "success", "message": "Track added to playlist"}
                else:
                    return {"status": "error", "message": "Playlist not found"}
        except Exception as e:
            logger.error("add_track_failed", error=str(e))
            return {"status": "error", "message": str(e)}

    @app.post("/playlist/remove")
    async def remove_track_from_playlist(track_id: str = Form(...)) -> dict[str, Any]:
        """Remove a track from the playlist using DELETE API."""
        settings = load_config(app.state.config_path)
        playlist_name = settings.user.playlist_name

        try:
            from src.apple_music import AppleMusicAuth, AppleMusicClient

            auth = AppleMusicAuth(
                team_id=settings.apple_music.team_id,
                key_id=settings.apple_music.key_id,
                private_key_path=settings.apple_music.private_key_path_resolved,
            )
            client = AppleMusicClient(
                auth=auth,
                storefront=settings.apple_music.storefront,
            )

            async with client:
                # Find the playlist with tracks
                all_playlists = await client.get_library_playlists()
                matching = [p for p in all_playlists if p.name == playlist_name]

                playlist = None
                for p in matching:
                    tracks = await client.get_library_playlist_tracks(p.id)
                    if tracks:
                        playlist = p
                        break

                if not playlist:
                    return {"status": "error", "message": "Playlist not found"}

                # Use DELETE endpoint (discovered from Apple's web interface)
                success = await client.remove_track_from_library_playlist(
                    playlist.id, track_id
                )

                if success:
                    return {"status": "success", "message": "Track removed from playlist"}
                else:
                    return {"status": "error", "message": "Failed to remove track"}

        except Exception as e:
            logger.error("remove_track_failed", error=str(e))
            return {"status": "error", "message": str(e)}

    return app


async def _check_auth_status(settings: Settings) -> dict[str, Any]:
    """Check Apple Music authentication status.

    Args:
        settings: Application settings

    Returns:
        Dict with auth status info
    """
    import keyring

    try:
        user_token = keyring.get_password("headless-curator", "apple_music_user_token")

        if user_token:
            # Try a simple API call to verify token is valid
            from src.apple_music import AppleMusicAuth, AppleMusicClient

            auth = AppleMusicAuth(
                team_id=settings.apple_music.team_id,
                key_id=settings.apple_music.key_id,
                private_key_path=settings.apple_music.private_key_path_resolved,
            )
            client = AppleMusicClient(
                auth=auth,
                storefront=settings.apple_music.storefront,
            )

            try:
                async with client:
                    # Try to get library - this requires user token
                    await client.get_library_playlists(limit=1)
                    return {"valid": True, "message": "Authenticated"}
            except Exception as e:
                return {"valid": False, "message": f"Token expired: {e}"}
            finally:
                await client.close()
        else:
            return {"valid": False, "message": "No user token found"}

    except Exception as e:
        return {"valid": False, "message": f"Auth check failed: {e}"}


# CLI entry point for running the web server
def run_server(host: str = "0.0.0.0", port: int = 8080, config_path: str = "config.yaml") -> None:
    """Run the web server.

    Args:
        host: Host to bind to
        port: Port to listen on
        config_path: Path to configuration file
    """
    import uvicorn

    app = create_app(config_path)
    uvicorn.run(app, host=host, port=port)
