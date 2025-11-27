"""Scheduler for nightly playlist refresh."""

import asyncio
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.curator import run_curator
from src.utils.config import Settings, load_config
from src.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


def send_macos_notification(title: str, message: str) -> None:
    """Send a macOS notification using osascript.

    Args:
        title: Notification title
        message: Notification message
    """
    try:
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except Exception as e:
        logger.warning("notification_failed", error=str(e))


async def scheduled_refresh(config_path: Path) -> None:
    """Run a scheduled playlist refresh with error handling.

    Args:
        config_path: Path to configuration file
    """
    logger.info("scheduled_refresh_starting")

    try:
        result = await run_curator(config_path)

        send_macos_notification(
            "Headless Curator",
            f"Playlist refreshed: {result['track_count']} tracks",
        )

    except Exception as e:
        logger.error("scheduled_refresh_failed", error=str(e))

        send_macos_notification(
            "Headless Curator Error",
            f"Playlist refresh failed: {str(e)[:50]}",
        )


class CuratorScheduler:
    """Scheduler for automated playlist updates."""

    def __init__(self, settings: Settings, config_path: Path) -> None:
        self.settings = settings
        self.config_path = config_path
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        """Start the scheduler with configured refresh time."""
        # Parse refresh time (HH:MM format)
        hour, minute = map(int, self.settings.schedule.refresh_time.split(":"))
        timezone = ZoneInfo(self.settings.schedule.timezone)

        # Create cron trigger for daily execution
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            timezone=timezone,
        )

        self.scheduler.add_job(
            scheduled_refresh,
            trigger,
            args=[self.config_path],
            id="nightly_refresh",
            name="Nightly Playlist Refresh",
            replace_existing=True,
        )

        self.scheduler.start()

        logger.info(
            "scheduler_started",
            refresh_time=self.settings.schedule.refresh_time,
            timezone=self.settings.schedule.timezone,
        )

    def stop(self) -> None:
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("scheduler_stopped")

    async def run_forever(self) -> None:
        """Run the scheduler indefinitely."""
        self.start()

        # Check token expiry warning
        from src.apple_music.auth import AppleMusicAuth

        auth = AppleMusicAuth(
            team_id=self.settings.apple_music.team_id,
            key_id=self.settings.apple_music.key_id,
            private_key_path=self.settings.apple_music.private_key_path_resolved,
        )

        # Generate initial token to check expiry
        auth.generate_developer_token()
        should_warn, days = auth.check_token_expiry_warning()
        if should_warn and days:
            logger.warning("developer_token_expiring", days_remaining=days)
            send_macos_notification(
                "Headless Curator Warning",
                f"Developer token expires in {days} days",
            )

        try:
            while True:
                await asyncio.sleep(3600)  # Check every hour
        except asyncio.CancelledError:
            self.stop()


async def run_daemon(config_path: Path | str = "config.yaml") -> None:
    """Run the curator as a daemon.

    Args:
        config_path: Path to configuration file
    """
    path = Path(config_path)
    settings = load_config(path)

    # Setup logging to file for daemon mode
    log_path = Path.home() / "Library" / "Logs" / "HeadlessCurator" / "curator.log"
    setup_logging(log_level="INFO", log_file=log_path, json_format=True)

    logger.info("daemon_starting", config_path=str(path))

    scheduler = CuratorScheduler(settings, path)
    await scheduler.run_forever()


def generate_launchd_plist(
    config_path: Path | str = "config.yaml",
    python_path: Path | str | None = None,
) -> str:
    """Generate a launchd plist file for macOS.

    Args:
        config_path: Path to configuration file
        python_path: Path to Python interpreter (defaults to current)

    Returns:
        Plist XML content
    """
    if python_path is None:
        python_path = Path(sys.executable)

    config_path = Path(config_path).absolute()
    python_path = Path(python_path).absolute()

    # Get the project root (where pyproject.toml is)
    project_root = Path(__file__).parent.parent.absolute()

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dave.headless-curator</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>src.main</string>
        <string>serve</string>
        <string>--config</string>
        <string>{config_path}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{project_root}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>{Path.home()}/Library/Logs/HeadlessCurator/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>{Path.home()}/Library/Logs/HeadlessCurator/stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>PYTHONPATH</key>
        <string>{project_root}</string>
    </dict>

    <key>ProcessType</key>
    <string>Background</string>

    <key>LowPriorityIO</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>60</integer>
</dict>
</plist>
"""
    return plist
