"""Sports Inbound Backend — Handles Slack button interactions for the partnership pipeline."""
import asyncio
import json
import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    SLACK_SIGNING_SECRET,
    SPORTS_INBOUND_CHANNEL,
    SENDER_EMAIL,
    JUDITH_EMAIL,
    FORM_URL,
    FORM_EDIT_ID,
    UNDO_WINDOW_SECONDS,
    RESPONSE_SHEET_ID,
)
from app.slack_blocks import build_pitch_message, build_submission_message
from app.slack_service import (
    post_message,
    post_thread_confirmation,
    update_message,
    schedule_reminder,
)
from app.sheets_service import update_row_status, get_weekly_stats, append_form_submission
from app.gmail_service import send_email
from app.email_templates import (
    approved_email,
    decline_pitch_email,
    submission_acknowledge_email,
    interested_email,
    decline_submission_email,
    schedule_call_email,
    request_media_kit_email,
    request_rate_card_email,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sports Inbound Backend")

# In-memory store for pending actions (undo feature)
# Key: pending_id, Value: {action, params, task, channel, ts, original_blocks}
_pending_actions: dict[str, dict] = {}

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API: Custom form submission (branded landing page)
# ---------------------------------------------------------------------------
@app.post("/api/form-submission")
async def api_form_submission(request: Request):
    """Receive form data from the branded partnership form and write to Google Sheet.

    Also posts to #sports-inbound with buttons and sends an auto-acknowledge email.
    """
    body = await request.json()

    full_name = body.get("full_name", "")
    email_addr = body.get("email", "")

    # 1. Write to Google Sheet
    sheet_result = append_form_submission(body)
    row_num = sheet_result.get("row", "")

    # 2. Post to #sports-inbound with buttons
    blocks = build_submission_message(
        full_name=full_name,
        email=email_addr,
        organization=body.get("organization", ""),
        sport=body.get("sport", ""),
        athlete_property=body.get("athlete_name", ""),
        market="",
        reach=body.get("followers_range", ""),
        partnership_type=body.get("partnership_type", ""),
        budget=body.get("budget_range", ""),
        socials=body.get("social_media", ""),
        used_before="",
        pitch=body.get("proposal_summary", ""),
        sheet_row=str(row_num),
        sheet_url=f"https://docs.google.com/spreadsheets/d/{RESPONSE_SHEET_ID}",
    )

    slack_result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New partnership form submission from {full_name} <{email_addr}>",
    )

    # 3. Send auto-acknowledge email
    ack = submission_acknowledge_email(full_name)
    email_result = send_email(to=email_addr, subject=ack["subject"], body=ack["body"])

    return {
        "ok": True,
        "sheet": sheet_result.get("ok", False),
        "slack": slack_result.get("ok", False),
        "email_sent": email_result.get("sent", False),
        "row": row_num,
    }


# ---------------------------------------------------------------------------
# Helper: Build pre-filled form URL (V2 Feature 5)
# ---------------------------------------------------------------------------
def _build_prefilled_form_url(name: str, email: str) -> str:
    """Build a Google Forms URL with name and email pre-filled."""
    # Google Forms prefill uses entry IDs from the form
    # For now, use the viewform URL with prefill params
    base = f"https://docs.google.com/forms/d/e/{FORM_EDIT_ID}/viewform"
    params = urlencode({
        "entry.1": name,      # Full Name field
        "entry.2": email,     # Email field
    })
    return f"{base}?{params}"


# ---------------------------------------------------------------------------
# Webhook: New email from Zapier (Zap 1 trigger -> webhook action)
# ---------------------------------------------------------------------------
@app.post("/webhook/new-email")
async def webhook_new_email(request: Request):
    """Receive email data from Zapier Zap 1 and post to #sports-inbound with buttons."""
    body = await request.json()

    sender_name = body.get("sender_name", "Unknown")
    sender_email = body.get("sender_email", "unknown@example.com")
    subject = body.get("subject", "(no subject)")
    date = body.get("date", "")
    route = body.get("route", "Direct to sports@")
    preview = body.get("preview", "")

    blocks = build_pitch_message(
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        date=date,
        route=route,
        preview=preview,
    )

    result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New sponsorship pitch from {sender_name} <{sender_email}>",
    )

    return {
        "ok": result.get("ok", False),
        "ts": result.get("ts"),
        "channel": result.get("channel"),
    }


# ---------------------------------------------------------------------------
# Webhook: New form submission from Zapier (Zap 3a trigger -> webhook action)
# ---------------------------------------------------------------------------
@app.post("/webhook/new-submission")
async def webhook_new_submission(request: Request):
    """Receive form data from Zapier Zap 3a and post to #sports-inbound with buttons + send ack email."""
    body = await request.json()

    full_name = body.get("full_name", "")
    email_addr = body.get("email", "")
    organization = body.get("organization", "")
    sport = body.get("sport", "")
    athlete_property = body.get("athlete_property", "")
    market = body.get("market", "")
    reach = body.get("reach", "")
    partnership_type = body.get("partnership_type", "")
    budget = body.get("budget", "")
    socials = body.get("socials", "")
    used_before = body.get("used_before", "")
    pitch = body.get("pitch", "")
    sheet_row = body.get("sheet_row", "")
    sheet_url = body.get(
        "sheet_url",
        "https://docs.google.com/spreadsheets/d/16xR18SLY5NmTqQYgttR4k_zq6iFRDuLGhIKslR6ioLQ",
    )

    # 1. Post to #sports-inbound with buttons
    blocks = build_submission_message(
        full_name=full_name,
        email=email_addr,
        organization=organization,
        sport=sport,
        athlete_property=athlete_property,
        market=market,
        reach=reach,
        partnership_type=partnership_type,
        budget=budget,
        socials=socials,
        used_before=used_before,
        pitch=pitch,
        sheet_row=sheet_row,
        sheet_url=sheet_url,
    )

    slack_result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New partnership form submission from {full_name} <{email_addr}>",
    )

    # 2. Send auto-acknowledge email to submitter
    ack = submission_acknowledge_email(full_name)
    email_result = send_email(to=email_addr, subject=ack["subject"], body=ack["body"])

    return {
        "ok": slack_result.get("ok", False),
        "ts": slack_result.get("ts"),
        "email_sent": email_result.get("sent", False),
        "email_message": email_result.get("message", ""),
    }


# ---------------------------------------------------------------------------
# Slack Interactivity Endpoint — handles all button clicks
# ---------------------------------------------------------------------------
@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    """Handle Slack interactive button clicks.

    Slack sends a POST with Content-Type: application/x-www-form-urlencoded
    containing a 'payload' field with JSON.
    """
    raw_body = await request.body()

    # Verify Slack signature if signing secret is configured
    if SLACK_SIGNING_SECRET:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        slack_sig = request.headers.get("X-Slack-Signature", "")

        # Reject requests older than 5 minutes
        if abs(time.time() - float(timestamp or 0)) > 300:
            return Response(status_code=403, content="Request too old")

        sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
        my_sig = "v0=" + hmac.new(
            SLACK_SIGNING_SECRET.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(my_sig, slack_sig):
            return Response(status_code=403, content="Invalid signature")

    # Parse the payload
    form_data = parse_qs(raw_body.decode("utf-8"))
    payload_str = form_data.get("payload", [""])[0]
    if not payload_str:
        return Response(status_code=400, content="No payload")

    payload = json.loads(payload_str)
    actions = payload.get("actions", [])
    if not actions:
        return Response(status_code=200, content="No actions")

    action = actions[0]
    action_id = action.get("action_id", "")
    value = action.get("value", "")
    channel_id = payload.get("channel", {}).get("id", SPORTS_INBOUND_CHANNEL)
    message_ts = payload.get("message", {}).get("ts", "")
    original_blocks = payload.get("message", {}).get("blocks", [])

    logger.info("Slack interaction: action=%s, value=%s, channel=%s, ts=%s", action_id, value, channel_id, message_ts)

    # Parse value (format: "email|||name|||subject_or_row")
    parts = value.split("|||")
    recipient_email = parts[0] if len(parts) > 0 else ""
    recipient_name = parts[1] if len(parts) > 1 else ""
    third_field = parts[2] if len(parts) > 2 else ""

    # Dispatch based on action_id
    # --- V1: Primary actions (now with undo) ---
    if action_id == "pitch_approved":
        await _start_pending_action(
            "pitch_approved", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field,
        )

    elif action_id == "pitch_decline":
        await _start_pending_action(
            "pitch_decline", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field,
        )

    elif action_id == "submission_interested":
        await _start_pending_action(
            "submission_interested", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field,
        )

    elif action_id == "submission_hold":
        await _handle_submission_hold(
            channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field,
        )

    elif action_id == "submission_decline":
        await _start_pending_action(
            "submission_decline", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field,
        )

    # --- V2: Undo ---
    elif action_id == "undo_action":
        await _handle_undo(value, channel_id, message_ts, original_blocks)

    # --- V2: Hold reminders ---
    elif action_id in ("remind_1week", "remind_1month", "remind_quarter"):
        await _handle_hold_reminder(
            action_id, channel_id, message_ts, original_blocks,
            recipient_email, recipient_name,
        )

    # --- V2: Quick-reply templates ---
    elif action_id in ("followup_schedule_call", "followup_media_kit", "followup_rate_card"):
        await _handle_quick_reply(
            action_id, channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field,
        )

    # Return 200 immediately (Slack requires response within 3 seconds)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# V2 Feature 1: Undo system
# ---------------------------------------------------------------------------

async def _start_pending_action(
    action_type: str,
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, third_field: str,
):
    """Replace buttons with a countdown + UNDO button. Execute after UNDO_WINDOW_SECONDS."""
    pending_id = str(uuid.uuid4())[:8]

    action_labels = {
        "pitch_approved": "APPROVED",
        "pitch_decline": "DECLINE",
        "submission_interested": "INTERESTED",
        "submission_decline": "DECLINE",
    }
    label = action_labels.get(action_type, action_type.upper())

    # Replace buttons with pending state + UNDO button
    pending_blocks = _replace_with_undo(blocks, label, pending_id, email, name, third_field)
    await update_message(channel, ts, pending_blocks)

    # Store pending action
    _pending_actions[pending_id] = {
        "action_type": action_type,
        "channel": channel,
        "ts": ts,
        "original_blocks": blocks,
        "email": email,
        "name": name,
        "third_field": third_field,
        "label": label,
    }

    # Schedule execution after delay
    asyncio.create_task(_execute_after_delay(pending_id))


async def _execute_after_delay(pending_id: str):
    """Wait UNDO_WINDOW_SECONDS, then execute the pending action if not cancelled."""
    await asyncio.sleep(UNDO_WINDOW_SECONDS)

    pending = _pending_actions.pop(pending_id, None)
    if not pending:
        return  # Was undone

    action_type = pending["action_type"]
    channel = pending["channel"]
    ts = pending["ts"]
    blocks = pending["original_blocks"]
    email = pending["email"]
    name = pending["name"]
    third_field = pending["third_field"]

    if action_type == "pitch_approved":
        await _handle_pitch_approved(channel, ts, blocks, email, name, third_field)
    elif action_type == "pitch_decline":
        await _handle_pitch_decline(channel, ts, blocks, email, name, third_field)
    elif action_type == "submission_interested":
        await _handle_submission_interested(channel, ts, blocks, email, name, third_field)
    elif action_type == "submission_decline":
        await _handle_submission_decline(channel, ts, blocks, email, name, third_field)


async def _handle_undo(pending_id: str, channel: str, ts: str, blocks: list[dict]):
    """Cancel a pending action and restore the original buttons."""
    pending = _pending_actions.pop(pending_id, None)
    if not pending:
        await post_thread_confirmation(channel, ts, ":x: Action already completed or expired.")
        return

    # Restore original buttons
    await update_message(channel, ts, pending["original_blocks"])
    await post_thread_confirmation(channel, ts, ":leftwards_arrow_with_hook: Undone! Buttons restored.")


def _replace_with_undo(
    blocks: list[dict], label: str, pending_id: str,
    email: str, name: str, third_field: str,
) -> list[dict]:
    """Replace action buttons with a pending state + UNDO button."""
    new_blocks = []
    for block in blocks:
        if block.get("type") == "actions":
            new_blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f":hourglass_flowing_sand: *{label}* will execute in {UNDO_WINDOW_SECONDS} seconds..."}
                ],
            })
            new_blocks.append({
                "type": "actions",
                "block_id": "undo_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "UNDO", "emoji": True},
                        "style": "danger",
                        "action_id": "undo_action",
                        "value": pending_id,
                    }
                ],
            })
        else:
            new_blocks.append(block)
    return new_blocks


# ---------------------------------------------------------------------------
# V2 Feature 2: Hold reminder buttons
# ---------------------------------------------------------------------------

async def _handle_hold_reminder(
    action_id: str, channel: str, ts: str, blocks: list[dict],
    email: str, name: str,
):
    """Schedule a Slack reminder for a HOLD item."""
    now = datetime.now(timezone.utc)
    intervals = {
        "remind_1week": (timedelta(weeks=1), "1 week"),
        "remind_1month": (timedelta(days=30), "1 month"),
        "remind_quarter": (timedelta(days=90), "next quarter"),
    }
    delta, label = intervals[action_id]
    remind_at = now + delta

    # Replace reminder buttons with confirmation
    updated = _disable_buttons(blocks, f"Remind in {label}")
    await update_message(channel, ts, updated)

    # Schedule the reminder
    reminder_text = (
        f":bell: *HOLD Reminder* - Time to revisit *{name}* ({email}).\n"
        f"This partnership proposal was put on hold {label} ago. "
        f"Review and decide: Interested, Decline, or extend the hold."
    )
    await schedule_reminder(channel, ts, reminder_text, remind_at)

    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Reminder set for {remind_at.strftime('%B %d, %Y')}.",
    )


# ---------------------------------------------------------------------------
# V2 Feature 3: Quick-reply templates after INTERESTED
# ---------------------------------------------------------------------------

async def _handle_quick_reply(
    action_id: str, channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str,
):
    """Send a follow-up email template after INTERESTED."""
    template_map = {
        "followup_schedule_call": (schedule_call_email, "Schedule call email"),
        "followup_media_kit": (request_media_kit_email, "Media kit request"),
        "followup_rate_card": (request_rate_card_email, "Rate card request"),
    }
    template_fn, label = template_map[action_id]
    template = template_fn(name)

    email_result = send_email(
        to=email, subject=template["subject"], body=template["body"],
        cc=template.get("cc", ""),
    )

    # Replace quick-reply buttons with confirmation
    updated = _disable_buttons(blocks, label)
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (queued - Gmail not connected)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: {label} sent to {email_status}",
    )


# ---------------------------------------------------------------------------
# V2 Feature 4: Weekly dashboard summary
# ---------------------------------------------------------------------------
@app.post("/webhook/weekly-digest")
async def webhook_weekly_digest(request: Request):
    """Post a weekly pipeline summary to #sports-inbound. Called by a cron trigger."""
    stats = get_weekly_stats()
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Weekly Partnership Pipeline Summary", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Total submissions:* {stats['total']}"},
                {"type": "mrkdwn", "text": f"*New (pending review):* {stats['new']}"},
                {"type": "mrkdwn", "text": f"*Interested:* {stats['interested']}"},
                {"type": "mrkdwn", "text": f"*On Hold:* {stats['hold']}"},
                {"type": "mrkdwn", "text": f"*Declined:* {stats['declined']}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f":bar_chart: <https://docs.google.com/spreadsheets/d/{RESPONSE_SHEET_ID}|"
                        f"Open full tracker>"
                    ),
                }
            ],
        },
    ]

    result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"Weekly summary: {stats['total']} total, {stats['new']} pending review",
    )

    return {"ok": result.get("ok", False), "stats": stats}


# ---------------------------------------------------------------------------
# Helper: Replace buttons with quick-reply template buttons (V2 Feature 3)
# ---------------------------------------------------------------------------
def _replace_with_quick_reply_buttons(
    blocks: list[dict], email: str, name: str, row: str,
) -> list[dict]:
    """Replace original action buttons with quick-reply follow-up options."""
    new_blocks = []
    for block in blocks:
        if block.get("type") == "actions":
            new_blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": ":white_check_mark: *INTERESTED* selected - Send a follow-up?"}
                ],
            })
            new_blocks.append({
                "type": "actions",
                "block_id": "quick_reply_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Schedule call", "emoji": True},
                        "action_id": "followup_schedule_call",
                        "value": f"{email}|||{name}|||{row}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Request media kit", "emoji": True},
                        "action_id": "followup_media_kit",
                        "value": f"{email}|||{name}|||{row}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Request rate card", "emoji": True},
                        "action_id": "followup_rate_card",
                        "value": f"{email}|||{name}|||{row}",
                    },
                ],
            })
        else:
            new_blocks.append(block)
    return new_blocks


# ---------------------------------------------------------------------------
# Helper: Replace buttons with hold reminder buttons (V2 Feature 2)
# ---------------------------------------------------------------------------
def _replace_with_hold_reminder_buttons(
    blocks: list[dict], email: str, name: str,
) -> list[dict]:
    """Replace original action buttons with reminder interval options."""
    new_blocks = []
    for block in blocks:
        if block.get("type") == "actions":
            new_blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": ":white_check_mark: *HOLD* selected - When should I remind you?"}
                ],
            })
            new_blocks.append({
                "type": "actions",
                "block_id": "hold_reminder_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "1 week", "emoji": True},
                        "action_id": "remind_1week",
                        "value": f"{email}|||{name}|||",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "1 month", "emoji": True},
                        "action_id": "remind_1month",
                        "value": f"{email}|||{name}|||",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Next quarter", "emoji": True},
                        "action_id": "remind_quarter",
                        "value": f"{email}|||{name}|||",
                    },
                ],
            })
        else:
            new_blocks.append(block)
    return new_blocks


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _disable_buttons(blocks: list[dict], chosen_label: str) -> list[dict]:
    """Replace action buttons with a context block showing which action was taken."""
    new_blocks = []
    for block in blocks:
        if block.get("type") == "actions":
            new_blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f":white_check_mark: *{chosen_label}* selected"}
                ],
            })
        else:
            new_blocks.append(block)
    return new_blocks


async def _handle_pitch_approved(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, subject: str,
):
    """APPROVED: Send form link email (with pre-filled URL) + post confirmation."""
    # V2 Feature 5: Pre-fill form URL with sender's name and email
    prefilled_url = _build_prefilled_form_url(name, email)
    template = approved_email(name, subject, form_url=prefilled_url)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"])

    updated = _disable_buttons(blocks, "APPROVED")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (queued - Gmail not connected)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done - form link sent to {email_status}",
    )


async def _handle_pitch_decline(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, subject: str,
):
    """DECLINE: Send decline email + post confirmation."""
    template = decline_pitch_email(name, subject)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"])

    updated = _disable_buttons(blocks, "DECLINE")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (queued - Gmail not connected)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done - decline email sent to {email_status}",
    )


async def _handle_submission_interested(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str,
):
    """INTERESTED: Send handoff email + update sheet + show quick-reply buttons."""
    template = interested_email(name)
    email_result = send_email(
        to=email, subject=template["subject"], body=template["body"], cc=template["cc"],
    )

    update_row_status(row, status="Interested", next_action="Judith to follow up")

    # V2 Feature 3: Replace original buttons with quick-reply template buttons
    updated = _replace_with_quick_reply_buttons(blocks, email, name, row)
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (queued - Gmail not connected)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done - handoff email sent to {email_status}, {JUDITH_EMAIL} CCd\n"
        f"_Optional: click a follow-up button above to send an additional email._",
    )


async def _handle_submission_hold(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str,
):
    """HOLD: Update sheet + show reminder interval buttons."""
    update_row_status(row, status="Hold", next_action="Revisit next quarter")

    # V2 Feature 2: Replace buttons with reminder interval options
    updated = _replace_with_hold_reminder_buttons(blocks, email, name)
    await update_message(channel, ts, updated)

    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done - {name} marked as Hold in tracker. No email sent.\n"
        f"_Choose when to be reminded above._",
    )


async def _handle_submission_decline(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str,
):
    """DECLINE (submission): Send decline email + update sheet + confirm."""
    template = decline_submission_email(name)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"])

    update_row_status(row, status="Declined")

    updated = _disable_buttons(blocks, "DECLINE")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (queued - Gmail not connected)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done - decline email sent to {email_status}",
    )
