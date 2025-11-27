"""Apple Music authentication with JWT generation and token management."""

import time
from datetime import datetime, timedelta
from pathlib import Path

import jwt
import keyring

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Keyring service name
KEYRING_SERVICE = "headless-curator"
KEYRING_USER_TOKEN_KEY = "apple_music_user_token"
KEYRING_TOKEN_EXPIRY_KEY = "apple_music_token_expiry"

# Apple Music constants
APPLE_MUSIC_ISSUER = "https://appleid.apple.com"
TOKEN_MAX_AGE_DAYS = 180  # Apple allows up to 6 months
TOKEN_WARNING_DAYS = 30  # Warn before expiry


class AppleMusicAuth:
    """Handles Apple Music API authentication."""

    def __init__(
        self,
        team_id: str,
        key_id: str,
        private_key_path: Path,
    ) -> None:
        """Initialize authentication handler.

        Args:
            team_id: Apple Developer Team ID
            key_id: MusicKit Key ID
            private_key_path: Path to the .p8 private key file
        """
        self.team_id = team_id
        self.key_id = key_id
        self.private_key_path = private_key_path
        self._private_key: str | None = None
        self._developer_token: str | None = None
        self._developer_token_expiry: datetime | None = None

    def _load_private_key(self) -> str:
        """Load the private key from disk."""
        if self._private_key is None:
            key_path = self.private_key_path.expanduser()
            if not key_path.exists():
                raise FileNotFoundError(f"Private key not found: {key_path}")

            # Verify permissions (should be 600)
            mode = key_path.stat().st_mode & 0o777
            if mode != 0o600:
                logger.warning(
                    "private_key_permissions_warning",
                    path=str(key_path),
                    mode=oct(mode),
                    expected="0o600",
                )

            self._private_key = key_path.read_text()

        return self._private_key

    def generate_developer_token(self, expires_in_days: int = TOKEN_MAX_AGE_DAYS) -> str:
        """Generate a new Apple Music Developer Token (JWT).

        Args:
            expires_in_days: Token validity period in days (max 180)

        Returns:
            JWT developer token string
        """
        if expires_in_days > TOKEN_MAX_AGE_DAYS:
            expires_in_days = TOKEN_MAX_AGE_DAYS

        private_key = self._load_private_key()
        now = int(time.time())
        expiry = now + (expires_in_days * 24 * 60 * 60)

        headers = {
            "alg": "ES256",
            "kid": self.key_id,
        }

        payload = {
            "iss": self.team_id,
            "iat": now,
            "exp": expiry,
        }

        token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

        self._developer_token = token
        self._developer_token_expiry = datetime.fromtimestamp(expiry)

        logger.info(
            "developer_token_generated",
            expires_at=self._developer_token_expiry.isoformat(),
            expires_in_days=expires_in_days,
        )

        return token

    @property
    def developer_token(self) -> str:
        """Get the current developer token, generating a new one if needed."""
        if self._developer_token is None or self._is_token_expired():
            return self.generate_developer_token()
        return self._developer_token

    def _is_token_expired(self) -> bool:
        """Check if the current developer token is expired or expiring soon."""
        if self._developer_token_expiry is None:
            return True
        # Refresh if less than 1 day remaining
        return datetime.now() >= self._developer_token_expiry - timedelta(days=1)

    def check_token_expiry_warning(self) -> tuple[bool, int | None]:
        """Check if the developer token is expiring soon.

        Returns:
            Tuple of (should_warn, days_remaining)
        """
        if self._developer_token_expiry is None:
            return False, None

        days_remaining = (self._developer_token_expiry - datetime.now()).days

        if days_remaining <= TOKEN_WARNING_DAYS:
            return True, days_remaining

        return False, days_remaining

    # User Music Token management (stored in Keychain)

    @staticmethod
    def get_user_token() -> str | None:
        """Retrieve the User Music Token from the system keychain."""
        try:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_TOKEN_KEY)
            if token:
                logger.debug("user_token_retrieved_from_keychain")
            return token
        except Exception as e:
            logger.error("keychain_access_error", error=str(e))
            return None

    @staticmethod
    def store_user_token(token: str) -> None:
        """Store the User Music Token in the system keychain.

        Args:
            token: The user music token from OAuth flow
        """
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER_TOKEN_KEY, token)
            logger.info("user_token_stored_in_keychain")
        except Exception as e:
            logger.error("keychain_store_error", error=str(e))
            raise

    @staticmethod
    def delete_user_token() -> None:
        """Remove the User Music Token from the system keychain."""
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER_TOKEN_KEY)
            logger.info("user_token_deleted_from_keychain")
        except keyring.errors.PasswordDeleteError:
            pass  # Token didn't exist
        except Exception as e:
            logger.error("keychain_delete_error", error=str(e))

    @staticmethod
    def has_user_token() -> bool:
        """Check if a User Music Token exists in the keychain."""
        return AppleMusicAuth.get_user_token() is not None

    def get_auth_headers(self) -> dict[str, str]:
        """Get headers for authenticated API requests.

        Returns:
            Dictionary with Authorization and Music-User-Token headers
        """
        headers = {
            "Authorization": f"Bearer {self.developer_token}",
        }

        user_token = self.get_user_token()
        if user_token:
            headers["Music-User-Token"] = user_token

        return headers
