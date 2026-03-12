"""Gmail service for sending emails as sports@eightsleep.com.

Primary method: Google Apps Script web app (permanent, no token expiry).
Fallback: OAuth2 with a refresh token (may expire if password changes).

The Apps Script runs as sports@eightsleep.com and has native GmailApp access,
so it never needs token refreshes or OAuth reauthorization.

Env vars needed:
  WEBHOOK_SECRET             – shared secret for authenticating Apps Script requests
  GMAIL_OAUTH_REFRESH_TOKEN  – (fallback) refresh token for sports@
  GMAIL_OAUTH_CLIENT_ID      – (fallback) OAuth client ID
  GMAIL_OAUTH_CLIENT_SECRET  – (fallback) OAuth client secret
"""
import base64
import json
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import httpx

from app.config import (
    GMAIL_OAUTH_REFRESH_TOKEN,
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    SENDER_NAME,
    SENDER_EMAIL,
    WEBHOOK_SECRET,
)

logger = logging.getLogger(__name__)

# Apps Script web app URL — permanent email sending endpoint
APPS_SCRIPT_EMAIL_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbyb1Eh0mFtSkfiWdrecWEiIfzxU067faUcatbylo0bKuUAtY5cm7jmjammC9U26EGPRSg"
    "/exec"
)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _send_via_apps_script(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html_body: str = "",
) -> dict:
    """Send email via Google Apps Script web app (primary, never expires)."""
    payload = {
        "secret": WEBHOOK_SECRET,
        "to": to,
        "subject": subject,
        "body": body,
        "cc": cc,
        "html_body": html_body,
    }

    try:
        response = httpx.post(
            APPS_SCRIPT_EMAIL_URL,
            json=payload,
            timeout=30.0,
            follow_redirects=True,
        )
        result = response.json()

        if result.get("ok"):
            logger.info("Email sent via Apps Script to %s (subject: %s)", to, subject)
            return {
                "sent": True,
                "message": result.get("message", f"Email sent to {to}"),
                "method": "apps_script",
            }
        else:
            logger.warning("Apps Script email failed: %s", result.get("error", "unknown"))
            return {
                "sent": False,
                "message": f"Apps Script error: {result.get('error', 'unknown')}",
                "method": "apps_script",
            }
    except Exception as e:
        logger.warning("Apps Script request failed: %s — falling back to OAuth", e)
        return {
            "sent": False,
            "message": f"Apps Script request failed: {str(e)}",
            "method": "apps_script",
        }


def _get_gmail_service():
    """Build Gmail API service using OAuth2 refresh token (fallback).

    Returns None if OAuth credentials are not configured.
    """
    if not all([GMAIL_OAUTH_REFRESH_TOKEN, GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET]):
        logger.warning("Gmail OAuth credentials not fully configured — OAuth fallback unavailable")
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


def _send_via_oauth(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html_body: str = "",
) -> dict:
    """Send email via Gmail OAuth2 API (fallback)."""
    service = _get_gmail_service()

    if not service:
        return {
            "sent": False,
            "message": "Gmail OAuth not configured — email not sent.",
            "method": "oauth_fallback",
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

        logger.info("Email sent via OAuth to %s (subject: %s), message ID: %s", to, subject, sent.get("id"))
        return {
            "sent": True,
            "message": f"Email sent to {to}",
            "message_id": sent.get("id"),
            "method": "oauth_fallback",
        }
    except Exception as e:
        logger.error("OAuth email send failed to %s: %s", to, e)
        return {
            "sent": False,
            "message": f"OAuth email send failed: {str(e)}",
            "method": "oauth_fallback",
        }


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html_body: str = "",
) -> dict:
    """Send an email from sports@eightsleep.com.

    Strategy: Try Apps Script first (permanent, no expiry).
    If that fails, fall back to OAuth2 (may expire).
    Returns dict with 'sent' (bool) and 'message' (str).
    """
    # Primary: Apps Script (never expires)
    result = _send_via_apps_script(to, subject, body, cc, html_body)
    if result["sent"]:
        return result

    logger.warning("Apps Script failed, trying OAuth fallback for %s", to)

    # Fallback: OAuth2 (may expire)
    oauth_result = _send_via_oauth(to, subject, body, cc, html_body)
    if oauth_result["sent"]:
        return oauth_result

    # Both failed — log for visibility
    log_msg = (
        f"[EMAIL NOT SENT - both methods failed]\n"
        f"  From: {SENDER_NAME} <{SENDER_EMAIL}>\n"
        f"  To: {to}\n"
        f"  CC: {cc or '(none)'}\n"
        f"  Subject: {subject}\n"
        f"  Apps Script error: {result.get('message')}\n"
        f"  OAuth error: {oauth_result.get('message')}\n"
        f"  Body preview: {body[:200]}..."
    )
    logger.error(log_msg)
    return {
        "sent": False,
        "message": f"Email send failed (both methods): {oauth_result.get('message')}",
        "to": to,
        "subject": subject,
    }
