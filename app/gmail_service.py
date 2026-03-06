"""Gmail service for sending emails as sports@eightsleep.com.

Uses OAuth2 with a refresh token obtained from the sports@ Google account.
The refresh token is long-lived and auto-refreshes the access token as needed.

Env vars needed:
  GMAIL_OAUTH_REFRESH_TOKEN  – permanent refresh token for sports@
  GMAIL_OAUTH_CLIENT_ID      – OAuth client ID from GCP project
  GMAIL_OAUTH_CLIENT_SECRET  – OAuth client secret from GCP project
"""
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import (
    GMAIL_OAUTH_REFRESH_TOKEN,
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    SENDER_NAME,
    SENDER_EMAIL,
)

logger = logging.getLogger(__name__)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _get_gmail_service():
    """Build Gmail API service using OAuth2 refresh token.

    Returns None if OAuth credentials are not configured.
    """
    if not all([GMAIL_OAUTH_REFRESH_TOKEN, GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET]):
        logger.warning("Gmail OAuth credentials not fully configured — emails will be logged, not sent")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(
            token=None,
            refresh_token=GMAIL_OAUTH_REFRESH_TOKEN,
            token_uri=TOKEN_URI,
            client_id=GMAIL_OAUTH_CLIENT_ID,
            client_secret=GMAIL_OAUTH_CLIENT_SECRET,
            scopes=[GMAIL_SEND_SCOPE],
        )

        return build("gmail", "v1", credentials=credentials)
    except Exception as e:
        logger.error("Failed to build Gmail service: %s", e)
        return None


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html_body: str = "",
) -> dict:
    """Send an email from sports@eightsleep.com.

    Uses OAuth2 refresh token for authentication.
    Returns dict with 'sent' (bool) and 'message' (str).
    """
    service = _get_gmail_service()

    if not service:
        log_msg = (
            f"[EMAIL NOT SENT - Gmail OAuth not configured]\n"
            f"  From: {SENDER_NAME} <{SENDER_EMAIL}>\n"
            f"  To: {to}\n"
            f"  CC: {cc or '(none)'}\n"
            f"  Subject: {subject}\n"
            f"  Body preview: {body[:200]}..."
        )
        logger.info(log_msg)
        return {
            "sent": False,
            "message": "Gmail OAuth not configured — email logged but not sent.",
            "to": to,
            "subject": subject,
        }

    try:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
        else:
            msg = MIMEMultipart()
            msg.attach(MIMEText(body, "plain"))

        msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        logger.info("Email sent to %s (subject: %s), message ID: %s", to, subject, sent.get("id"))
        return {
            "sent": True,
            "message": f"Email sent to {to}",
            "message_id": sent.get("id"),
        }
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        return {
            "sent": False,
            "message": f"Failed to send email: {str(e)}",
            "to": to,
            "subject": subject,
        }
