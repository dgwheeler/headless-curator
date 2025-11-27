"""CLI entry point for Headless Curator."""

import asyncio
import webbrowser
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.utils.config import load_config, save_config
from src.utils.logging import setup_logging

app = typer.Typer(
    name="curator",
    help="Headless Curator - Personalized Apple Music Playlist Generator",
    no_args_is_help=True,
)
console = Console()


def get_config_path(config: Optional[Path]) -> Path:
    """Get the configuration file path."""
    return config or Path("config.yaml")


@app.command()
def auth(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
) -> None:
    """Authenticate with Apple Music (opens browser for OAuth flow)."""
    config_path = get_config_path(config)
    settings = load_config(config_path)

    rprint("\n[bold blue]Apple Music Authentication[/bold blue]\n")

    # Check if we already have a user token
    from src.apple_music.auth import AppleMusicAuth

    if AppleMusicAuth.has_user_token():
        rprint("[green]✓[/green] User token already exists in keychain")

        if not typer.confirm("Do you want to re-authenticate?"):
            return

    # Generate developer token
    try:
        auth = AppleMusicAuth(
            team_id=settings.apple_music.team_id,
            key_id=settings.apple_music.key_id,
            private_key_path=settings.apple_music.private_key_path_resolved,
        )
        developer_token = auth.generate_developer_token()
    except FileNotFoundError as e:
        rprint(f"[red]Error:[/red] {e}")
        rprint("\nPlease ensure your private key is at the configured path:")
        rprint(f"  {settings.apple_music.private_key_path}")
        raise typer.Exit(1)
    except Exception as e:
        rprint(f"[red]Error generating developer token:[/red] {e}")
        raise typer.Exit(1)

    rprint("[green]✓[/green] Developer token generated successfully")
    rprint()

    # Instructions for getting user token
    rprint("[bold]To complete authentication, you need to obtain a Music User Token.[/bold]")
    rprint()
    rprint("This requires a web-based OAuth flow. You have two options:")
    rprint()
    rprint("1. Use Apple's MusicKit JS in a local web page")
    rprint("2. Use a companion iOS/macOS app with MusicKit")
    rprint()
    rprint("For option 1, create a simple HTML page with MusicKit JS that:")
    rprint("  - Initializes MusicKit with your developer token")
    rprint("  - Calls music.authorize() to get the user token")
    rprint("  - Displays the token for you to copy")
    rprint()

    # Provide a sample HTML file path
    sample_path = Path(__file__).parent.parent / "auth_helper.html"
    rprint(f"A sample auth helper page can be created at: [cyan]{sample_path}[/cyan]")
    rprint()

    token = typer.prompt("Paste your Music User Token here", hide_input=True)

    if token:
        AppleMusicAuth.store_user_token(token.strip())
        rprint("\n[green]✓[/green] User token stored in keychain")
        rprint("[green]✓[/green] Authentication complete!")
    else:
        rprint("[yellow]Warning:[/yellow] No token provided. Authentication incomplete.")


@app.command()
def refresh(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose output"),
    ] = False,
) -> None:
    """Manually trigger a playlist refresh."""
    config_path = get_config_path(config)

    setup_logging(
        log_level="DEBUG" if verbose else "INFO",
        json_format=False,
    )

    rprint("\n[bold blue]Refreshing Playlist[/bold blue]\n")

    from src.curator import run_curator

    try:
        result = asyncio.run(run_curator(config_path))

        rprint()
        rprint(Panel(
            f"[green]✓ Playlist refreshed successfully![/green]\n\n"
            f"  Tracks: {result['track_count']}\n"
            f"  Artists discovered: {result['artists_discovered']}\n"
            f"  Duration: {result['duration_seconds']}s",
            title="Refresh Complete",
        ))

    except Exception as e:
        rprint(f"\n[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command("add-seed")
def add_seed(
    artist: Annotated[
        Optional[str],
        typer.Option("--artist", "-a", help="Artist name to add as seed"),
    ] = None,
    song: Annotated[
        Optional[str],
        typer.Option("--song", "-s", help="Song name to add as seed"),
    ] = None,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
) -> None:
    """Add a new seed artist or song."""
    if not artist and not song:
        rprint("[red]Error:[/red] Please specify --artist or --song")
        raise typer.Exit(1)

    config_path = get_config_path(config)
    settings = load_config(config_path)

    if artist:
        if artist not in settings.seeds.artists:
            settings.seeds.artists.append(artist)
            save_config(settings, config_path)
            rprint(f"[green]✓[/green] Added seed artist: [cyan]{artist}[/cyan]")
        else:
            rprint(f"[yellow]![/yellow] Artist already in seeds: [cyan]{artist}[/cyan]")

    if song:
        if song not in settings.seeds.songs:
            settings.seeds.songs.append(song)
            save_config(settings, config_path)
            rprint(f"[green]✓[/green] Added seed song: [cyan]{song}[/cyan]")
        else:
            rprint(f"[yellow]![/yellow] Song already in seeds: [cyan]{song}[/cyan]")


@app.command("remove-seed")
def remove_seed(
    artist: Annotated[
        Optional[str],
        typer.Option("--artist", "-a", help="Artist name to remove from seeds"),
    ] = None,
    song: Annotated[
        Optional[str],
        typer.Option("--song", "-s", help="Song name to remove from seeds"),
    ] = None,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
) -> None:
    """Remove a seed artist or song."""
    if not artist and not song:
        rprint("[red]Error:[/red] Please specify --artist or --song")
        raise typer.Exit(1)

    config_path = get_config_path(config)
    settings = load_config(config_path)

    if artist:
        if artist in settings.seeds.artists:
            settings.seeds.artists.remove(artist)
            save_config(settings, config_path)
            rprint(f"[green]✓[/green] Removed seed artist: [cyan]{artist}[/cyan]")
        else:
            rprint(f"[yellow]![/yellow] Artist not in seeds: [cyan]{artist}[/cyan]")

    if song:
        if song in settings.seeds.songs:
            settings.seeds.songs.remove(song)
            save_config(settings, config_path)
            rprint(f"[green]✓[/green] Removed seed song: [cyan]{song}[/cyan]")
        else:
            rprint(f"[yellow]![/yellow] Song not in seeds: [cyan]{song}[/cyan]")


@app.command()
def status(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
) -> None:
    """Show current playlist and curator status."""
    config_path = get_config_path(config)

    try:
        settings = load_config(config_path)
    except Exception as e:
        rprint(f"[red]Error loading config:[/red] {e}")
        raise typer.Exit(1)

    rprint(f"\n[bold blue]Headless Curator Status[/bold blue]\n")

    # User info
    rprint(f"[bold]User:[/bold] {settings.user.name}")
    rprint(f"[bold]Playlist:[/bold] {settings.user.playlist_name}")
    rprint()

    # Seed artists
    table = Table(title="Seed Artists")
    table.add_column("Artist", style="cyan")
    for artist in settings.seeds.artists:
        table.add_row(artist)
    console.print(table)
    rprint()

    # Filters
    rprint("[bold]Filters:[/bold]")
    rprint(f"  Gender: {settings.filters.gender}")
    rprint(f"  Countries: {', '.join(settings.filters.countries)}")
    rprint(f"  Min Release Year: {settings.filters.min_release_year}")
    rprint()

    # Schedule
    rprint("[bold]Schedule:[/bold]")
    rprint(f"  Refresh Time: {settings.schedule.refresh_time}")
    rprint(f"  Timezone: {settings.schedule.timezone}")
    rprint()

    # Auth status
    from src.apple_music.auth import AppleMusicAuth

    rprint("[bold]Authentication:[/bold]")
    if AppleMusicAuth.has_user_token():
        rprint("  [green]✓[/green] User token present in keychain")
    else:
        rprint("  [red]✗[/red] User token not found (run 'curator auth')")

    try:
        auth = AppleMusicAuth(
            team_id=settings.apple_music.team_id,
            key_id=settings.apple_music.key_id,
            private_key_path=settings.apple_music.private_key_path_resolved,
        )
        auth.generate_developer_token()
        should_warn, days = auth.check_token_expiry_warning()

        if should_warn and days:
            rprint(f"  [yellow]![/yellow] Developer token expires in {days} days")
        elif days:
            rprint(f"  [green]✓[/green] Developer token valid ({days} days remaining)")

    except FileNotFoundError:
        rprint("  [red]✗[/red] Private key not found")
    except Exception as e:
        rprint(f"  [red]✗[/red] Developer token error: {e}")

    # Database stats
    rprint()
    rprint("[bold]Database:[/bold]")

    async def get_stats():
        from src.database import Repository
        repo = Repository(settings.database.url)
        try:
            await repo.init_db()
            return await repo.get_stats()
        finally:
            await repo.close()

    try:
        stats = asyncio.run(get_stats())
        rprint(f"  Artists: {stats['artists']} ({stats['seed_artists']} seeds)")
        rprint(f"  Tracks: {stats['tracks']}")
        rprint(f"  Preferences: {stats['preferences']}")
    except Exception as e:
        rprint(f"  [yellow]Not initialized or error:[/yellow] {e}")


@app.command()
def serve(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
) -> None:
    """Run the curator daemon (for launchd or manual background run)."""
    config_path = get_config_path(config)

    rprint("\n[bold blue]Starting Headless Curator Daemon[/bold blue]\n")
    rprint(f"Config: {config_path}")
    rprint("Press Ctrl+C to stop\n")

    from src.scheduler import run_daemon

    try:
        asyncio.run(run_daemon(config_path))
    except KeyboardInterrupt:
        rprint("\n[yellow]Daemon stopped[/yellow]")


@app.command("install-service")
def install_service(
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to configuration file"),
    ] = None,
) -> None:
    """Generate and optionally install the launchd plist for macOS."""
    import sys

    config_path = get_config_path(config).absolute()

    from src.scheduler import generate_launchd_plist

    plist_content = generate_launchd_plist(config_path)
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.dave.headless-curator.plist"

    rprint(f"\n[bold blue]launchd Service Installation[/bold blue]\n")
    rprint(f"Plist path: [cyan]{plist_path}[/cyan]\n")
    rprint("[dim]" + "-" * 60 + "[/dim]")
    rprint(plist_content)
    rprint("[dim]" + "-" * 60 + "[/dim]\n")

    if typer.confirm("Install this plist and load the service?"):
        # Create log directory
        log_dir = Path.home() / "Library" / "Logs" / "HeadlessCurator"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Write plist
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)

        rprint(f"[green]✓[/green] Plist written to {plist_path}")

        # Load the service
        import subprocess

        result = subprocess.run(
            ["launchctl", "load", str(plist_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            rprint("[green]✓[/green] Service loaded successfully")
            rprint()
            rprint("The service will start automatically on boot.")
            rprint("To check status: [cyan]launchctl list | grep curator[/cyan]")
            rprint("To stop: [cyan]launchctl unload ~/Library/LaunchAgents/com.dave.headless-curator.plist[/cyan]")
        else:
            rprint(f"[red]Error loading service:[/red] {result.stderr}")
    else:
        rprint("\nTo install manually:")
        rprint(f"  1. Save the plist to: {plist_path}")
        rprint(f"  2. Run: launchctl load {plist_path}")


@app.command("uninstall-service")
def uninstall_service() -> None:
    """Unload and remove the launchd service."""
    import subprocess

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.dave.headless-curator.plist"

    if not plist_path.exists():
        rprint("[yellow]Service not installed[/yellow]")
        return

    if typer.confirm("Unload and remove the launchd service?"):
        # Unload the service
        result = subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            rprint("[green]✓[/green] Service unloaded")
        else:
            rprint(f"[yellow]Warning:[/yellow] {result.stderr}")

        # Remove the plist
        plist_path.unlink()
        rprint(f"[green]✓[/green] Plist removed from {plist_path}")


if __name__ == "__main__":
    app()
