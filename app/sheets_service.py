"""Google Sheets service for updating response tracker rows."""
import json
import logging
from datetime import datetime, timezone
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from app.config import GDRIVE_JSON_KEY, RESPONSE_SHEET_ID

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_sheets_service():
    """Build a Google Sheets API service using the service account key."""
    if not GDRIVE_JSON_KEY:
        logger.warning("GDRIVE_JSON_KEY not set, sheets operations will fail")
        return None
    key_data = json.loads(GDRIVE_JSON_KEY)
    creds = Credentials.from_service_account_info(key_data, scopes=SCOPES)
    delegated = creds.with_subject("devin-service@eightsleep.com")
    return build("sheets", "v4", credentials=delegated)


def get_weekly_stats() -> dict:
    """Get weekly pipeline stats from the response sheet for the digest."""
    service = _get_sheets_service()
    if not service:
        return {"total": 0, "interested": 0, "hold": 0, "declined": 0, "new": 0}

    try:
        # Read all rows to count statuses
        result = service.spreadsheets().values().get(
            spreadsheetId=RESPONSE_SHEET_ID,
            range="Form Responses 1!A:T",
        ).execute()
        rows = result.get("values", [])
        if len(rows) <= 1:
            return {"total": 0, "interested": 0, "hold": 0, "declined": 0, "new": 0}

        # Count statuses (column R = index 17 for Status)
        total = len(rows) - 1  # exclude header
        statuses = {"Interested": 0, "Hold": 0, "Declined": 0, "New": 0}
        for row in rows[1:]:
            status_val = row[17] if len(row) > 17 else "New"
            if status_val in statuses:
                statuses[status_val] += 1
            else:
                statuses["New"] += 1

        return {
            "total": total,
            "interested": statuses["Interested"],
            "hold": statuses["Hold"],
            "declined": statuses["Declined"],
            "new": statuses["New"],
        }
    except Exception as e:
        logger.error("Failed to get weekly stats: %s", e)
        return {"total": 0, "interested": 0, "hold": 0, "declined": 0, "new": 0}


def append_form_submission(data: dict) -> dict:
    """Append a new form submission row to the response sheet.

    Returns {"ok": True, "row": row_number} on success.
    """
    service = _get_sheets_service()
    if not service:
        return {"ok": False, "message": "Sheets service not available"}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = [
        now,                               # Timestamp
        data.get("email", ""),             # Email
        data.get("full_name", ""),         # Full Name
        data.get("role", ""),              # Role
        data.get("organization", ""),      # Organization
        data.get("sport", ""),             # Sport
        data.get("athlete_name", ""),      # Athlete/Team Name
        data.get("social_media", ""),      # Social Media
        data.get("followers_range", ""),   # Followers
        data.get("partnership_type", ""),  # Partnership Type
        data.get("proposal_summary", ""),  # Proposal Summary
        data.get("previous_partnerships", ""),  # Previous Partnerships
        data.get("budget_range", ""),      # Budget
        data.get("timeline", ""),          # Timeline
        data.get("additional_notes", ""),  # Additional Notes
        "",                                # (reserved)
        "",                                # (reserved)
        "New",                             # Status
        "",                                # Next Action
        now.split(" ")[0],                 # Date Reviewed
    ]

    try:
        result = service.spreadsheets().values().append(
            spreadsheetId=RESPONSE_SHEET_ID,
            range="Form Responses 1!A:T",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        updated_range = result.get("updates", {}).get("updatedRange", "")
        # Extract row number from range like "Form Responses 1!A42:T42"
        row_num = updated_range.split("!")[-1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRST") if updated_range else ""
        logger.info("Appended form submission for %s at row %s", data.get("email", ""), row_num)
        return {"ok": True, "row": row_num}
    except Exception as e:
        logger.error("Failed to append form submission: %s", e)
        return {"ok": False, "message": str(e)}


def update_row_status(
    row_number: str,
    status: str,
    next_action: str = "",
) -> bool:
    """Update Status, Next Action, and Date Reviewed columns for a given row."""
    service = _get_sheets_service()
    if not service:
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Columns: R=Status, S=Next Action, T=Date Reviewed
    range_name = f"Form Responses 1!R{row_number}:T{row_number}"
    values = [[status, next_action, today]]

    try:
        service.spreadsheets().values().update(
            spreadsheetId=RESPONSE_SHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        logger.info("Updated row %s: status=%s, next_action=%s", row_number, status, next_action)
        return True
    except Exception as e:
        logger.error("Failed to update row %s: %s", row_number, e)
        return False
