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
- Python 3.12+
- Apple Developer Account with MusicKit enabled
- Mac that stays on (e.g., Mac Studio, Mac mini)

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

Set your Apple credentials as environment variables:

```bash
export APPLE_TEAM_ID="YOUR_TEAM_ID"
export APPLE_KEY_ID="YOUR_KEY_ID"
```

Or add them to your shell profile (`~/.zshrc` or `~/.bashrc`).

### 4. Create Configuration

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` to customize:
- User name and playlist name
- Seed artists
- Filter criteria
- Schedule

### 5. Authenticate

Run the authentication command to set up the user token:

```bash
curator auth
```

This will guide you through obtaining a Music User Token via Apple's MusicKit JS OAuth flow.

#### Getting the User Token

Since Apple Music requires user authorization via a web-based flow, you'll need to:

1. Create a simple HTML page with MusicKit JS
2. Host it locally or use a tool like `python -m http.server`
3. Initialize MusicKit with your developer token
4. Call `music.authorize()` to trigger the OAuth popup
5. Copy the returned user token

Example HTML for token retrieval:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Apple Music Auth</title>
    <script src="https://js-cdn.music.apple.com/musickit/v1/musickit.js"></script>
</head>
<body>
    <button id="auth">Authorize Apple Music</button>
    <pre id="token"></pre>
    <script>
        document.addEventListener('musickitloaded', async () => {
            await MusicKit.configure({
                developerToken: 'YOUR_DEVELOPER_TOKEN_HERE',
                app: {
                    name: 'Headless Curator',
                    build: '1.0.0'
                }
            });

            document.getElementById('auth').onclick = async () => {
                const music = MusicKit.getInstance();
                const token = await music.authorize();
                document.getElementById('token').textContent = token;
            };
        });
    </script>
</body>
</html>
```

After getting the token, paste it when prompted by `curator auth`.

## Usage

### CLI Commands

```bash
# Authenticate with Apple Music
curator auth

# Manually trigger a playlist refresh
curator refresh

# Verbose refresh (for debugging)
curator refresh --verbose

# Add a seed artist
curator add-seed --artist "Ed Sheeran"

# Remove a seed artist
curator remove-seed --artist "Ed Sheeran"

# Show current status
curator status

# Run as a daemon (foreground)
curator serve

# Install as a macOS service (launchd)
curator install-service

# Uninstall the macOS service
curator uninstall-service
```

### Running as a Service

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
   - Recent releases (>= 2020)

## Configuration Reference

```yaml
user:
  name: "Grace"                    # User's name
  playlist_name: "Grace's Station" # Playlist name in Apple Music
  playlist_id: null                # Auto-populated after first run

seeds:
  artists:                         # Seed artists for discovery
    - "Sam Smith"
    - "Lewis Capaldi"
  songs: []                        # Seed songs (optional)

filters:
  gender: "Male"                   # MusicBrainz gender filter
  countries: ["GB", "US", "IE"]    # ISO country codes
  min_release_year: 2020           # Only artists with recent releases

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
```

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Run specific test file
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

## License

MIT License - see LICENSE file for details.

## Credits

Built with:
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- [Pydantic](https://docs.pydantic.dev/) - Data validation
- [APScheduler](https://apscheduler.readthedocs.io/) - Task scheduling
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [structlog](https://www.structlog.org/) - Structured logging
- [SQLAlchemy](https://www.sqlalchemy.org/) - Database ORM
