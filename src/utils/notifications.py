"""Email notifications for curator events."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import TYPE_CHECKING

from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.utils.config import Settings

logger = get_logger(__name__)


def send_auth_failure_email(settings: "Settings") -> None:
    """Send email notification when Apple Music authentication fails.

    Args:
        settings: Application settings containing email configuration

    Raises:
        Exception: If email sending fails
    """
    email_config = settings.email

    if not email_config.enabled:
        logger.info("email_notifications_disabled")
        return

    if not email_config.recipient:
        logger.warning("no_email_recipient_configured")
        return

    subject = f"Headless Curator: Re-authentication Required"

    body = f"""Hi {settings.user.name or 'there'},

The Headless Curator service needs you to re-authenticate with Apple Music.

The user token has expired and playlist updates for "{settings.user.playlist_name}"
cannot continue until you re-authenticate.

To fix this, run the following command on your server:

    curator auth

This will open a browser for Apple Music authentication. Once complete,
the service will resume automatic playlist updates.

Note: Your existing playlist will continue to work, but no new tracks
will be added until re-authentication is complete.

---
Headless Curator
Automated playlist curation for Apple Music
"""

    msg = MIMEMultipart()
    msg["From"] = email_config.sender or email_config.smtp_username
    msg["To"] = email_config.recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        if email_config.smtp_use_tls:
            server = smtplib.SMTP(email_config.smtp_host, email_config.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(email_config.smtp_host, email_config.smtp_port)

        if email_config.smtp_username and email_config.smtp_password:
            server.login(email_config.smtp_username, email_config.smtp_password)

        server.sendmail(
            msg["From"],
            [email_config.recipient],
            msg.as_string(),
        )
        server.quit()

        logger.info("auth_failure_email_sent", recipient=email_config.recipient)

    except Exception as e:
        logger.error("email_send_failed", error=str(e))
        raise
