"""Gmail service for sending emails as sports@eightsleep.com.

Uses Google Workspace domain-wide delegation with a service account.
No user password needed — the service account impersonates sports@eightsleep.com
after a Workspace admin grants it the Gmail send scope.

Fallback: If delegation is not yet configured, emails are logged but not sent.
"""
import json
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import GDRIVE_JSON_KEY, SENDER_NAME, SENDER_EMAIL

logger = logging.getLogger(__name__)

# Gmail scope needed for domain-wide delegation
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def _get_gmail_service():
    """Build Gmail API service using domain-wide delegation.

    The service account impersonates sports@eightsleep.com, which requires
    a Workspace admin to grant domain-wide delegation with the gmail.send scope.
    Returns None if credentials are not configured or delegation fails.
    """
    if not GDRIVE_JSON_KEY:
        logger.warning("GDRIVE_JSON_KEY not configured — emails will be logged, not sent")
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_data = json.loads(GDRIVE_JSON_KEY)

        credentials = service_account.Credentials.from_service_account_info(
            creds_data,
            scopes=[GMAIL_SEND_SCOPE],
        )
        # Impersonate sports@eightsleep.com via domain-wide delegation
        delegated_credentials = credentials.with_subject(SENDER_EMAIL)

        return build("gmail", "v1", credentials=delegated_credentials)
    except Exception as e:
        logger.warning(
            "Gmail delegation not available yet (expected until admin grants access): %s",
            e,
        )
        return None


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html_body: str = "",
) -> dict:
    """Send an email from sports@eightsleep.com.

    Uses domain-wide delegation (no password needed).
    Returns dict with 'sent' (bool) and 'message' (str).
    If delegation is not configured yet, returns sent=False with details.
    """
    service = _get_gmail_service()

    if not service:
        log_msg = (
            f"[EMAIL QUEUED - Gmail delegation not configured yet]\n"
            f"  From: {SENDER_NAME} <{SENDER_EMAIL}>\n"
            f"  To: {to}\n"
            f"  CC: {cc or '(none)'}\n"
            f"  Subject: {subject}\n"
            f"  Body preview: {body[:200]}..."
        )
        logger.info(log_msg)
        return {
            "sent": False,
            "message": "Gmail delegation not configured — email logged but not sent. "
                       "Waiting for Workspace admin to grant domain-wide delegation.",
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
