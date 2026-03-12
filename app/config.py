"""Configuration for Sports Inbound backend."""
import os
from dotenv import load_dotenv

load_dotenv()

# Slack
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SPORTS_INBOUND_CHANNEL = os.getenv("SPORTS_INBOUND_CHANNEL", "C0AKR508SDN")

# Google Sheets
GDRIVE_JSON_KEY = os.getenv("GDRIVE_JSON_KEY", "")
RESPONSE_SHEET_ID = "16xR18SLY5NmTqQYgttR4k_zq6iFRDuLGhIKslR6ioLQ"

# Linear
LINEAR_API_KEY = os.getenv("LINEAR_API_KEY", "")
LINEAR_TEAM_ID = os.getenv("LINEAR_TEAM_ID", "ab0cda08-cb13-4958-8e6d-75eccb3ad639")
LINEAR_LABEL_EMAIL_PITCH = os.getenv("LINEAR_LABEL_EMAIL_PITCH", "f693709d-ff63-4114-bcec-36d247c0765b")
LINEAR_LABEL_FORM_SUBMISSION = os.getenv("LINEAR_LABEL_FORM_SUBMISSION", "df69e701-250a-499a-bd92-033ecdf91cca")

# Linear status IDs (for updating issue status from Slack buttons)
LINEAR_STATUS_IDS: dict[str, str] = {
    "Triage": "514f63f5-1579-4e43-864b-7a84f050bff2",
    "Interested": "826eb8f0-571f-474b-9eeb-ebb2c15a439b",
    "Approved": "3298ba2e-e148-42c4-979b-3635154255cc",
    "Hold": "b56cfb1f-4958-4d22-b21a-878f1ffa6cb8",
    "Declined": "cd224b21-368f-4e38-9a12-ffc76986a87f",
    "Declined (No Discount)": "e5d4a6d0-4835-4b30-b167-5973944fb77d",
    "Duplicate": "18a2325f-d2ab-4ca5-a7e4-400b92fd1c31",
    "Done": "884a952d-d2a6-4298-80c7-a1ad4c518afa",
}

# Gmail OAuth (sports@eightsleep.com)
GMAIL_OAUTH_REFRESH_TOKEN = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN", "")
GMAIL_OAUTH_CLIENT_ID = os.getenv("GMAIL_OAUTH_CLIENT_ID", "")
GMAIL_OAUTH_CLIENT_SECRET = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "")

# Form link (branded form hosted on GitHub Pages — always on)
FORM_URL = "https://judith-aldabo.github.io/sports-partnership-form/"
FORM_EDIT_ID = "1tvLLDMOTRMwBW0kT16bXvtchO8jU8eIIOsi5FAkr87Q"  # legacy Google Form ID

# Undo window (seconds)
UNDO_WINDOW_SECONDS = 10

# Judith's info
JUDITH_EMAIL = "judith@eightsleep.com"
JUDITH_SLACK_ID = "U096422D10X"

# Sender identity
SENDER_NAME = "Eight Sleep Sports Team"
SENDER_EMAIL = "sports@eightsleep.com"

# Webhook authentication (shared secret for Zapier → backend)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Discount code for decline emails
DISCOUNT_CODE = "DEEPSLEEPATHLETE"
CHECKOUT_URL = "https://www.eightsleep.com"
