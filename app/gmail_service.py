"""Gmail service for sending emails from sports@eightsleep.com.

This service will be fully functional once sports@ credentials are available.
For now, it logs the email that would be sent and returns a placeholder.
"""
import json
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import GMAIL_CREDENTIALS_JSON, SENDER_NAME, SENDER_EMAIL

logger = logging.getLogger(__name__)


def _get_gmail_service():
    """Build Gmail API service. Returns None if credentials not configured."""
    if not GMAIL_CREDENTIALS_JSON:
        logger.warning("Gmail credentials not configured yet — emails will be queued, not sent")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds_data = json.loads(GMAIL_CREDENTIALS_JSON)
        creds = Credentials.from_authorized_user_info(creds_data)
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.error("Failed to build Gmail service: %s", e)
        return None


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
) -> dict:
    """Send an email from sports@eightsleep.com.

    Returns dict with 'sent' (bool) and 'message' (str).
    If Gmail is not configured, returns sent=False with details.
    """
    service = _get_gmail_service()

    if not service:
        log_msg = (
            f"[EMAIL QUEUED - Gmail not connected yet]\n"
            f"  From: {SENDER_NAME} <{SENDER_EMAIL}>\n"
            f"  To: {to}\n"
            f"  CC: {cc or '(none)'}\n"
            f"  Subject: {subject}\n"
            f"  Body preview: {body[:200]}..."
        )
        logger.info(log_msg)
        return {
            "sent": False,
            "message": "Gmail not configured — email logged but not sent. Will send once sports@ OAuth is connected.",
            "to": to,
            "subject": subject,
        }

    try:
        msg = MIMEMultipart()
        msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        msg.attach(MIMEText(body, "plain"))

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
