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

# Gmail (sports@eightsleep.com) - will be configured when credentials are available
GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON", "")

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
