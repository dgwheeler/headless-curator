# Headless Curator

A personalized Apple Music playlist generator that runs on your Mac, automatically curating and updating a playlist based on seed artists and learned preferences.

## Overview

Headless Curator creates a "radio station" experience for a specific user by:

1. **Discovering artists** similar to seed artists you specify
2. **Filtering** by criteria (gender, country, release year) via MusicBrainz
3. **Building playlists** with a weighted mix of favorites, hits, discoveries, and new releases
4. **Learning** from listening behavior over time
5. **Refreshing nightly** at a scheduled time

The target user (e.g., "Grace") simply sees a playlist called "Grace's Station" in their Apple Music library that stays fresh and personalized.

## Requirements

- macOS Sonoma or later
- Python 3.11+
- Apple Developer Account with MusicKit enabled
- Mac that stays on (e.g., Mac Studio, Mac mini)

## Quick Start

```bash
# Clone and install
git clone https://github.com/yourusername/headless-curator.git
cd headless-curator
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure (edit config.yaml with your settings)
cp config.yaml.example config.yaml

# Authenticate with Apple Music
curator auth

# Test with a manual refresh
curator refresh

# Install as background service
curator install-service
```

## Managing Curator

### Web Interface

The easiest way to manage Curator is through the web interface:

```bash
curator web --port 3030
```

Then open **http://127.0.0.1:8080** in your browser.

The web interface provides:
- **Dashboard** - View playlist stats, authentication status, recent sync activity, and trigger manual refreshes
- **Artists** - Add or remove seed artists and songs
- **Settings** - Adjust playlist size and category weights
- **Auth** - Check authentication status and see re-auth instructions

### CLI Commands

```bash
# Start web management interface
curator web
curator web --port 8181  # Custom port

# Authenticate with Apple Music
curator auth

# Manually trigger a playlist refresh
curator refresh
curator refresh --verbose  # Debug mode

# Manage seed artists
curator add-seed --artist "Ed Sheeran"
curator remove-seed --artist "Ed Sheeran"

# Show current status
curator status

# Run as a daemon (foreground)
curator serve

# macOS service management
curator install-service
curator uninstall-service
```

## Installation

### 1. Clone and Install

```bash
git clone https://github.com/yourusername/headless-curator.git
cd headless-curator

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

### 2. Create Apple MusicKit Key

1. Go to [Apple Developer Portal](https://developer.apple.com/account)
2. Navigate to **Certificates, Identifiers & Profiles** > **Keys**
3. Click **+** to create a new key
4. Name it "Headless Curator" and enable **MusicKit**
5. Download the `.p8` private key file
6. Note your **Key ID** (displayed after creation)
7. Note your **Team ID** (from top-right of developer portal or Membership page)

Store the private key securely:

```bash
mkdir -p ~/.secrets
mv ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.secrets/apple_music_key.p8
chmod 600 ~/.secrets/apple_music_key.p8
```

### 3. Configure Environment

Add to your `~/.zshrc`:

```bash
# Apple Music API credentials
export APPLE_TEAM_ID="YOUR_TEAM_ID"
export APPLE_KEY_ID="YOUR_KEY_ID"

# Email notifications (optional - for auth failure alerts)
export CURATOR_EMAIL_USER="your_email@icloud.com"
export CURATOR_APP_PASSWORD="your-app-specific-password"
```

For email notifications via iCloud, generate an app-specific password at [appleid.apple.com](https://appleid.apple.com) under Sign-In & Security > App-Specific Passwords.

### 4. Create Configuration

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` to customize:
- User name and playlist name
- Seed artists
- Filter criteria
- Schedule
- Email notification settings

### 5. Authenticate

Run the authentication command:

```bash
curator auth
```

This opens `auth_helper.html` in your browser. The target user (e.g., Grace) must sign in with **their** Apple ID to authorize access to their Apple Music library.

**Important:** The user token will eventually expire. When this happens, Curator will send an email notification (if configured) and you'll need to run `curator auth` again.

## Running as a Service

For 24/7 operation, install as a launchd service:

```bash
curator install-service
```

This creates a plist at `~/Library/LaunchAgents/com.dave.headless-curator.plist` and loads it.

The service will:
- Start automatically on boot
- Restart on failure
- Log to `~/Library/Logs/HeadlessCurator/`
- Run the nightly refresh at the configured time

To check service status:

```bash
launchctl list | grep curator
```

To view logs:

```bash
tail -f ~/Library/Logs/HeadlessCurator/curator.log
```

To uninstall:

```bash
curator uninstall-service
```

## Email Notifications

Curator can send email notifications when authentication expires. Configure in `config.yaml`:

```yaml
email:
  enabled: true
  smtp_host: "smtp.mail.me.com"  # iCloud SMTP
  smtp_port: 587
  smtp_use_tls: true
  smtp_username: "${CURATOR_EMAIL_USER}"
  smtp_password: "${CURATOR_APP_PASSWORD}"
  recipient: "your_email@example.com"
```

When the Apple Music user token expires, you'll receive an email with instructions to re-authenticate.

## How It Works

### Playlist Composition

Each refresh builds a playlist with four categories:

| Category | Weight | Description |
|----------|--------|-------------|
| Favorites | 40% | Tracks with high play counts from user's library |
| Hits | 30% | Top tracks from discovered artists (ones she knows) |
| Discovery | 20% | Tracks from similar artists she hasn't heard |
| Wildcard | 10% | Brand new releases (< 30 days old) |

Tracks are shuffled within categories, then interleaved to avoid clustering.

### Learning Algorithm

The curator learns from listening behavior:

**Positive Signals:**
- Play count increased since last sync
- Track was added to personal library

**Negative Signals:**
- Track in "hot zone" (positions 1-10) for 48+ hours with zero plays

**Decay:**
- Tracks not played in 14+ days gradually lose weight

### Artist Discovery

1. Search Apple Music for each seed artist
2. Fetch related artists for each seed
3. Cross-reference with MusicBrainz for metadata
4. Filter by:
   - Gender (e.g., Male only)
   - Country (e.g., UK, US, Ireland, etc.)
   - Recent releases (>= configured year)

## Configuration Reference

```yaml
user:
  name: "Grace"                    # User's name
  playlist_name: "Grace's Station" # Playlist name in Apple Music

seeds:
  artists:                         # Seed artists for discovery
    - "Sam Smith"
    - "Lewis Capaldi"
  songs: []                        # Seed songs (optional)

filters:
  gender: "Male"                   # MusicBrainz gender filter (or omit for any)
  countries: ["GB", "US", "IE"]    # ISO country codes
  min_release_year: 2012           # Only artists with releases since this year

algorithm:
  playlist_size: 50                # Total tracks in playlist
  weights:
    favorites: 0.40
    hits: 0.30
    discovery: 0.20
    wildcard: 0.10
  hot_zone_size: 10                # Top N positions for negative signals
  hot_zone_hours: 48               # Hours before negative signal
  decay_days: 14                   # Days before weight decay starts
  new_release_days: 30             # Max age for wildcard tracks

schedule:
  refresh_time: "03:00"            # 24-hour format
  timezone: "America/Los_Angeles"

apple_music:
  team_id: "${APPLE_TEAM_ID}"      # From env var
  key_id: "${APPLE_KEY_ID}"        # From env var
  private_key_path: "~/.secrets/apple_music_key.p8"
  storefront: "us"

database:
  path: "curator.db"               # SQLite database file

email:
  enabled: false                   # Set true to enable notifications
  smtp_host: "smtp.mail.me.com"
  smtp_port: 587
  smtp_use_tls: true
  smtp_username: "${CURATOR_EMAIL_USER}"
  smtp_password: "${CURATOR_APP_PASSWORD}"
  recipient: ""                    # Email to notify on auth failure
```

## Troubleshooting

### "Private key not found"

Ensure your `.p8` file is at the path specified in `config.yaml` and has correct permissions:

```bash
ls -la ~/.secrets/apple_music_key.p8
# Should show: -rw------- (600 permissions)
```

### "Authentication failed" (401 error)

1. Check that your developer token is valid (regenerate if expired)
2. Ensure the user token hasn't expired (re-run `curator auth`)
3. Verify Team ID and Key ID are correct

### "Rate limited" by MusicBrainz

The MusicBrainz client includes rate limiting (1 request/second) and caching (30 days). If you're still being rate limited, wait a few minutes and try again.

### Playlist not updating

1. Check logs: `tail -f ~/Library/Logs/HeadlessCurator/curator.log`
2. Run manual refresh: `curator refresh --verbose`
3. Verify the playlist exists in Apple Music
4. Check that the user token is still valid

### Re-authentication Required

Apple Music user tokens expire periodically. When this happens:

1. You'll receive an email notification (if configured)
2. The web interface will show "Authentication Required"
3. Run `curator auth` to re-authenticate
4. The playlist will continue working, but won't update until re-authenticated

## Development

### Running Tests

```bash
pytest
pytest --cov=src --cov-report=term-missing
pytest tests/test_curator.py -v
```

### Type Checking

```bash
mypy src
```

### Linting

```bash
ruff check src tests
ruff format src tests
```

## License

MIT License - see LICENSE file for details.

## Credits

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- [Pydantic](https://docs.pydantic.dev/) - Data validation
- [APScheduler](https://apscheduler.readthedocs.io/) - Task scheduling
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [structlog](https://www.structlog.org/) - Structured logging
- [SQLAlchemy](https://www.sqlalchemy.org/) - Database ORM
