"""Sports Inbound Backend — Handles Slack button interactions for the partnership pipeline."""
import asyncio
import json
import hashlib
import hmac
import logging
import re
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
from app.slack_blocks import build_pitch_message, build_submission_message, build_pitch_message_readonly, build_submission_message_readonly
from app.slack_service import (
    post_message,
    post_thread_confirmation,
    update_message,
    schedule_reminder,
)
from app.sheets_service import update_row_status, get_weekly_stats, append_form_submission
from app.gmail_service import send_email
from app.linear_service import create_pitch_issue, create_submission_issue, get_issue_details, create_linear_webhook, get_all_team_issues, check_email_sent_marker, add_email_sent_marker
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

# In-memory store for Linear issue statuses (polling system)
# Key: issue_id, Value: status_name
_issue_status_cache: dict[str, str] = {}
_poller_initialized = False
POLL_INTERVAL_SECONDS = 60

# Dedup: track which issue+status emails we've already sent this session
# Prevents duplicate emails during blue-green deployments or retries
_processed_transitions: set[str] = set()

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def start_linear_poller():
    """Start the background Linear poller on app startup."""
    asyncio.create_task(_linear_poll_loop())
    logger.info("Linear poller background task started (every %ds)", POLL_INTERVAL_SECONDS)


async def _linear_poll_loop():
    """Background loop: poll Linear for status changes every POLL_INTERVAL_SECONDS."""
    global _poller_initialized
    # Wait a few seconds for app to fully start
    await asyncio.sleep(5)

    while True:
        try:
            await _poll_linear_status_changes()
        except Exception as e:
            logger.error("Linear poller error: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _poll_linear_status_changes():
    """Fetch all SPO123 issues, detect status changes, and trigger email logic.

    On first run (cold start / after Fly.io suspension), we populate the cache
    AND check for recently-updated issues in actionable statuses. This catches
    changes that happened while the machine was suspended.
    """
    global _poller_initialized

    issues = await get_all_team_issues()
    if not issues:
        return

    EMAIL_TRIGGER_STATUSES = {"Interested", "Declined", "Approved"}
    # Track which issues we process on cold start to avoid duplicates
    processed_on_init: set[str] = set()

    if not _poller_initialized:
        # Cold start: populate cache and check for recent actionable changes
        now = datetime.now(timezone.utc)
        for issue in issues:
            issue_id = issue["id"]
            current_status = issue["state_name"]
            _issue_status_cache[issue_id] = current_status

            # If issue is in an email-triggering status and was updated recently
            # (within 5 min), process it — it likely changed while we were suspended
            if current_status in EMAIL_TRIGGER_STATUSES and issue.get("updated_at"):
                try:
                    updated_at = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
                    age_seconds = (now - updated_at).total_seconds()
                    if age_seconds < 300:  # Updated within last 5 minutes
                        logger.info(
                            "Cold start: processing recently-updated issue %s (%s) in %s (updated %ds ago)",
                            issue["identifier"], issue["title"], current_status, int(age_seconds),
                        )
                        await _handle_status_change(issue_id, "unknown", current_status)
                        processed_on_init.add(issue_id)
                except (ValueError, TypeError) as e:
                    logger.warning("Could not parse updatedAt for %s: %s", issue["identifier"], e)

        _poller_initialized = True
        logger.info(
            "Linear poller initialized with %d issues in cache (%d processed on cold start)",
            len(_issue_status_cache), len(processed_on_init),
        )
        return

    # Normal polling: detect status changes since last poll
    for issue in issues:
        issue_id = issue["id"]
        current_status = issue["state_name"]
        previous_status = _issue_status_cache.get(issue_id)

        # Update cache
        _issue_status_cache[issue_id] = current_status

        # If status changed, process it
        if previous_status and previous_status != current_status:
            logger.info(
                "Poller detected status change: %s (%s) %s -> %s",
                issue["identifier"], issue["title"], previous_status, current_status,
            )
            await _handle_status_change(issue_id, previous_status, current_status)


async def _handle_status_change(issue_id: str, old_status: str, new_status: str):
    """Process a detected status change — same logic as webhook_linear."""
    # Dedup layer 1: in-memory (same machine, same session)
    dedup_key = f"{issue_id}:{new_status}"
    if dedup_key in _processed_transitions:
        logger.info("Skipping duplicate transition for %s -> %s (already processed in-memory)", issue_id, new_status)
        return

    # Dedup layer 2: cross-machine via Linear comments (survives restarts & multi-machine)
    if new_status in {"Interested", "Declined", "Approved"}:
        if await check_email_sent_marker(issue_id, new_status):
            logger.info("Skipping duplicate transition for %s -> %s (marker found in Linear)", issue_id, new_status)
            _processed_transitions.add(dedup_key)
            return

    _processed_transitions.add(dedup_key)

    issue = await get_issue_details(issue_id)
    if not issue.get("ok"):
        logger.error("Poller: failed to fetch issue details for %s", issue_id)
        return

    identifier = issue.get("identifier", "")
    title = issue.get("title", "")
    description = issue.get("description", "")
    issue_url = issue.get("url", "")
    labels = issue.get("labels", [])

    recipient_email, recipient_name = _parse_email_from_description(description)
    if not recipient_name:
        recipient_name = _parse_name_from_title(title)

    if not recipient_email:
        logger.warning("Poller: no email found in issue %s — skipping email", identifier)
        await post_message(
            channel=SPORTS_INBOUND_CHANNEL,
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":arrows_counterclockwise: *{identifier}* moved to *{new_status}*\n"
                            f"_{title}_\n"
                            f":warning: No email address found — manual action needed.\n"
                            f"<{issue_url}|View in Linear>",
                },
            }],
            text=f"{identifier} moved to {new_status} (no email found)",
        )
        return

    email_result = {"sent": False}
    slack_text = ""
    is_pitch = "Email Pitch" in labels or title.startswith("[Pitch]")

    if new_status == "Interested":
        if is_pitch:
            prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
            template = approved_email(recipient_name, _parse_subject_from_title(title), form_url=prefilled_url)
        else:
            template = interested_email(recipient_name)
        email_result = send_email(
            to=recipient_email,
            subject=template["subject"],
            body=template["body"],
            cc=template.get("cc", ""),
        )
        slack_text = f":white_check_mark: *{identifier}* → *Interested*"

    elif new_status == "Declined":
        if is_pitch:
            template = decline_pitch_email(recipient_name, _parse_subject_from_title(title))
        else:
            template = decline_submission_email(recipient_name)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"])
        slack_text = f":no_entry_sign: *{identifier}* → *Declined*"

    elif new_status == "Approved":
        prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
        subject = _parse_subject_from_title(title) if is_pitch else "Your Eight Sleep Partnership"
        template = approved_email(recipient_name, subject, form_url=prefilled_url)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"])
        slack_text = f":tada: *{identifier}* → *Approved*"

    else:
        # Other status changes — just notify Slack
        await post_message(
            channel=SPORTS_INBOUND_CHANNEL,
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":arrows_counterclockwise: *{identifier}* moved to *{new_status}*\n"
                            f"_{title}_\n"
                            f"<{issue_url}|View in Linear>",
                },
            }],
            text=f"{identifier} moved to {new_status}",
        )
        return

    # Mark as sent in Linear for cross-machine dedup
    if email_result.get("sent"):
        await add_email_sent_marker(issue_id, new_status)

    # Post Slack notification for email-triggering status changes
    email_status = recipient_email if email_result.get("sent") else f"{recipient_email} (failed)"
    await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=[{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{slack_text}\n"
                        f"_{title}_\n"
                        f":envelope: Email sent to {email_status}\n"
                        f"<{issue_url}|View in Linear>",
            },
        }],
        text=f"{identifier} → {new_status}, email to {recipient_email}",
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

    # 2. Create Linear issue
    organization = body.get("organization", "")
    sport = body.get("sport", "")
    athlete_property = body.get("athlete_name", "")
    market = body.get("market", "")
    reach = body.get("followers_range", "")
    partnership_type = body.get("partnership_type", "")
    socials = body.get("social_media", "")
    used_before = body.get("slept_on_pod", "")
    pitch = body.get("proposal_summary", "")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{RESPONSE_SHEET_ID}"

    linear_result = await create_submission_issue(
        full_name=full_name,
        email=email_addr,
        organization=organization,
        sport=sport,
        athlete_property=athlete_property,
        market=market,
        reach=reach,
        partnership_type=partnership_type,
        socials=socials,
        used_before=used_before,
        pitch=pitch,
        sheet_row=str(row_num),
        sheet_url=sheet_url,
    )
    linear_url = linear_result.get("url", "")
    linear_id = linear_result.get("identifier", "")

    # 3. Post read-only notification to #sports-inbound (with Linear link)
    blocks = build_submission_message_readonly(
        full_name=full_name,
        email=email_addr,
        organization=organization,
        sport=sport,
        athlete_property=athlete_property,
        market=market,
        reach=reach,
        partnership_type=partnership_type,
        socials=socials,
        used_before=used_before,
        pitch=pitch,
        sheet_row=str(row_num),
        sheet_url=sheet_url,
        linear_url=linear_url,
        linear_id=linear_id,
    )

    slack_result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New partnership form submission from {full_name} <{email_addr}>",
    )

    # 4. Send auto-acknowledge email
    ack = submission_acknowledge_email(full_name)
    email_result = send_email(to=email_addr, subject=ack["subject"], body=ack["body"])

    return {
        "ok": True,
        "sheet": sheet_result.get("ok", False),
        "slack": slack_result.get("ok", False),
        "linear": linear_result.get("ok", False),
        "linear_url": linear_url,
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

    # 1. Create Linear issue
    linear_result = await create_pitch_issue(
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        route=route,
        preview=preview,
    )
    linear_url = linear_result.get("url", "")
    linear_id = linear_result.get("identifier", "")

    # 2. Post read-only notification to #sports-inbound (with Linear link)
    blocks = build_pitch_message_readonly(
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        date=date,
        route=route,
        preview=preview,
        linear_url=linear_url,
        linear_id=linear_id,
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
        "linear": linear_result.get("ok", False),
        "linear_url": linear_url,
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

    # 1. Create Linear issue
    linear_result = await create_submission_issue(
        full_name=full_name,
        email=email_addr,
        organization=organization,
        sport=sport,
        athlete_property=athlete_property,
        market=market,
        reach=reach,
        partnership_type=partnership_type,
        socials=socials,
        used_before=used_before,
        pitch=pitch,
        sheet_row=sheet_row,
        sheet_url=sheet_url,
    )
    linear_url = linear_result.get("url", "")
    linear_id = linear_result.get("identifier", "")

    # 2. Post read-only notification to #sports-inbound (with Linear link)
    blocks = build_submission_message_readonly(
        full_name=full_name,
        email=email_addr,
        organization=organization,
        sport=sport,
        athlete_property=athlete_property,
        market=market,
        reach=reach,
        partnership_type=partnership_type,
        socials=socials,
        used_before=used_before,
        pitch=pitch,
        sheet_row=sheet_row,
        sheet_url=sheet_url,
        linear_url=linear_url,
        linear_id=linear_id,
    )

    slack_result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New partnership form submission from {full_name} <{email_addr}>",
    )

    # 3. Send auto-acknowledge email to submitter
    ack = submission_acknowledge_email(full_name)
    email_result = send_email(to=email_addr, subject=ack["subject"], body=ack["body"])

    return {
        "ok": slack_result.get("ok", False),
        "ts": slack_result.get("ts"),
        "linear": linear_result.get("ok", False),
        "linear_url": linear_url,
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
# Linear webhook: status changes trigger automated emails
# ---------------------------------------------------------------------------

def _parse_email_from_description(description: str) -> tuple[str, str]:
    """Extract reply-to email and recipient name from a Linear issue description.

    Returns (email, name). Falls back to empty strings if not found.

    Linear auto-formats emails as markdown links, so we need to handle:
      - Plain: Reply to: user@example.com
      - Markdown: *Reply to: *[*user@example.com*](<mailto:user@example.com>)
      - Table: | **Email** | [user@example.com](<mailto:user@example.com>) |
    """
    email = ""

    # Strategy 1: Look for mailto: links (most reliable — Linear always adds these)
    mailto_match = re.search(r"mailto:([^)\s>]+)", description)
    if mailto_match:
        email = mailto_match.group(1).strip()

    # Strategy 2: Fallback to plain Reply to: pattern
    if not email:
        email_match = re.search(r"Reply to:\s*\*?\[?\*?(\S+@[\w.\-]+)", description)
        email = email_match.group(1).rstrip("*]") if email_match else ""

    # For form submissions: look for Name field in table
    name_match = re.search(r"\*\*Name\*\*\s*\|\s*(.+?)(?:\s*\||\n)", description)
    if name_match:
        return email, name_match.group(1).strip()

    # For pitches: look for From field
    from_match = re.search(r"\*\*From:\*\*\s*(.+?)\s*<", description)
    if from_match:
        return email, from_match.group(1).strip()

    # Fallback: try title parsing
    return email, ""


def _parse_name_from_title(title: str) -> str:
    """Extract name from Linear issue title like '[Form] Name — Org (Sport)' or '[Pitch] Name — Subject'."""
    match = re.match(r"\[(?:Form|Pitch)\]\s*(.+?)\s*[—\-]", title)
    return match.group(1).strip() if match else ""


def _parse_subject_from_title(title: str) -> str:
    """Extract subject from pitch title like '[Pitch] Name — Subject'."""
    match = re.match(r"\[Pitch\]\s*.+?\s*[—\-]\s*(.+)", title)
    return match.group(1).strip() if match else ""


@app.post("/webhook/linear")
async def webhook_linear(request: Request):
    """Handle Linear webhook events for issue status changes.

    When an issue in SPO123 changes status:
    - Interested → send interested/form-link email + notify Slack
    - Declined → send decline email with discount code + notify Slack
    - Approved → send form link email + notify Slack
    """
    body = await request.json()

    action = body.get("action")
    event_type = body.get("type")

    # Only handle issue update events
    if event_type != "Issue" or action != "update":
        return {"ok": True, "skipped": True, "reason": "Not an issue update"}

    issue_data = body.get("data", {})
    updated_from = body.get("updatedFrom", {})

    # Only process if the status (stateId) changed
    if "stateId" not in updated_from:
        return {"ok": True, "skipped": True, "reason": "No status change"}

    issue_id = issue_data.get("id", "")
    if not issue_id:
        return {"ok": False, "error": "No issue ID"}

    # Fetch full issue details from Linear
    issue = await get_issue_details(issue_id)
    if not issue.get("ok"):
        logger.error("Failed to fetch issue details for %s: %s", issue_id, issue.get("error"))
        return {"ok": False, "error": issue.get("error")}

    new_status = issue.get("state_name", "")
    identifier = issue.get("identifier", "")
    title = issue.get("title", "")
    description = issue.get("description", "")
    issue_url = issue.get("url", "")
    labels = issue.get("labels", [])

    # Extract contact info from the issue description
    recipient_email, recipient_name = _parse_email_from_description(description)
    if not recipient_name:
        recipient_name = _parse_name_from_title(title)

    if not recipient_email:
        logger.warning("No email found in issue %s description — skipping email", identifier)
        # Still notify Slack about the status change
        await post_message(
            channel=SPORTS_INBOUND_CHANNEL,
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":arrows_counterclockwise: *{identifier}* moved to *{new_status}*\n"
                            f"_{title}_\n"
                            f":warning: No email address found — manual action needed.\n"
                            f"<{issue_url}|View in Linear>",
                },
            }],
            text=f"{identifier} moved to {new_status} (no email found)",
        )
        return {"ok": True, "status_changed": new_status, "email_sent": False, "reason": "No email in description"}

    email_result = {"sent": False}
    slack_text = ""

    is_pitch = "Email Pitch" in labels or title.startswith("[Pitch]")

    if new_status == "Interested":
        if is_pitch:
            # Pitch → Interested: send form link
            prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
            template = approved_email(recipient_name, _parse_subject_from_title(title), form_url=prefilled_url)
        else:
            # Submission → Interested: send handoff email CC Judith
            template = interested_email(recipient_name)
        email_result = send_email(
            to=recipient_email,
            subject=template["subject"],
            body=template["body"],
            cc=template.get("cc", ""),
        )
        slack_text = f":white_check_mark: *{identifier}* → *Interested*"

    elif new_status == "Declined":
        if is_pitch:
            template = decline_pitch_email(recipient_name, _parse_subject_from_title(title))
        else:
            template = decline_submission_email(recipient_name)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"])
        slack_text = f":no_entry_sign: *{identifier}* → *Declined*"

    elif new_status == "Approved":
        prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
        subject = _parse_subject_from_title(title) if is_pitch else "Your Eight Sleep Partnership"
        template = approved_email(recipient_name, subject, form_url=prefilled_url)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"])
        slack_text = f":tada: *{identifier}* → *Approved*"

    else:
        # Other status changes (Hold, Triage, Done) — just notify Slack, no email
        await post_message(
            channel=SPORTS_INBOUND_CHANNEL,
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":arrows_counterclockwise: *{identifier}* moved to *{new_status}*\n"
                            f"_{title}_\n"
                            f"<{issue_url}|View in Linear>",
                },
            }],
            text=f"{identifier} moved to {new_status}",
        )
        return {"ok": True, "status_changed": new_status, "email_sent": False}

    # Post Slack notification for email-triggering status changes
    email_status = recipient_email if email_result.get("sent") else f"{recipient_email} (queued — delegation pending)"
    await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=[{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{slack_text}\n"
                        f"_{title}_\n"
                        f":envelope: Email sent to {email_status}\n"
                        f"<{issue_url}|View in Linear>",
            },
        }],
        text=f"{identifier} → {new_status}, email to {recipient_email}",
    )

    return {
        "ok": True,
        "status_changed": new_status,
        "email_sent": email_result.get("sent", False),
        "email_to": recipient_email,
        "identifier": identifier,
    }


# ---------------------------------------------------------------------------
# Setup: Register Linear webhook (one-time call)
# ---------------------------------------------------------------------------
@app.post("/admin/setup-linear-webhook")
async def setup_linear_webhook(request: Request):
    """One-time setup: creates a Linear webhook pointing to this backend.

    Call this once after deploying to register the webhook.
    POST /admin/setup-linear-webhook with optional {"webhook_url": "..."}.
    """
    body = await request.json() if await request.body() else {}
    webhook_url = body.get("webhook_url", "https://app-fsoqjwmi.fly.dev/webhook/linear")

    result = await create_linear_webhook(webhook_url)
    return result


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
