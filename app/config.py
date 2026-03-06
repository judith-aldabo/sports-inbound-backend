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

# Gmail OAuth (sports@eightsleep.com)
GMAIL_OAUTH_REFRESH_TOKEN = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN", "")
GMAIL_OAUTH_CLIENT_ID = os.getenv("GMAIL_OAUTH_CLIENT_ID", "")
GMAIL_OAUTH_CLIENT_SECRET = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "")

# Form link
FORM_URL = "https://forms.gle/vCeH3KPpEuXR5m5E9"
FORM_EDIT_ID = "1tvLLDMOTRMwBW0kT16bXvtchO8jU8eIIOsi5FAkr87Q"

# Undo window (seconds)
UNDO_WINDOW_SECONDS = 10

# Judith's info
JUDITH_EMAIL = "judith@eightsleep.com"
JUDITH_SLACK_ID = "U096422D10X"

# Sender identity
SENDER_NAME = "Eight Sleep Sports Team"
SENDER_EMAIL = "sports@eightsleep.com"

# Discount code for decline emails
DISCOUNT_CODE = "DEEPSLEEPATHLETE"
CHECKOUT_URL_CORE = f"https://www.eightsleep.com/product/pod-cover/?code={DISCOUNT_CODE}"
CHECKOUT_URL_ULTRA = f"https://www.eightsleep.com/product/pod-cover/?code={DISCOUNT_CODE}"
