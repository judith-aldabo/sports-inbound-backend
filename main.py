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
    WEBHOOK_SECRET,
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
from app.linear_service import create_pitch_issue, create_submission_issue, get_issue_details, create_linear_webhook, get_all_team_issues, check_email_sent_marker, add_email_sent_marker, update_issue_status
from app.email_templates import (
    approved_email,
    decline_pitch_email,
    decline_pitch_no_discount_email,
    submission_acknowledge_email,
    interested_email,
    decline_submission_email,
    decline_submission_no_discount_email,
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


def _mark_transition_handled(issue_id: str, new_status: str):
    """Mark a transition as handled by a button click so the poller and webhook skip it.

    Updates both the in-memory poller cache (prevents poller from detecting
    the status change) and the processed-transitions set (prevents both poller
    and webhook from re-sending the email).
    """
    if issue_id:
        _issue_status_cache[issue_id] = new_status
        _processed_transitions.add(f"{issue_id}:{new_status}")

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
    asyncio.create_task(_keep_alive_loop())
    logger.info("Linear poller background task started (every %ds)", POLL_INTERVAL_SECONDS)


KEEP_ALIVE_INTERVAL = 120  # Ping self every 2 minutes to prevent Fly.io auto-suspend


async def _keep_alive_loop():
    """Self-ping to prevent Fly.io from auto-suspending the machine.

    Fly.io suspends idle machines, but Slack button clicks have a 3-second
    timeout. If the machine is suspended, it takes too long to wake up and
    Slack shows an error. This loop keeps the machine alive.
    """
    import httpx
    await asyncio.sleep(10)  # Wait for app to start
    logger.info("Keep-alive loop started (every %ds)", KEEP_ALIVE_INTERVAL)
    while True:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:8000/healthz", timeout=5)
                logger.debug("Keep-alive ping: %s", resp.status_code)
        except Exception as e:
            logger.debug("Keep-alive ping failed (expected during shutdown): %s", e)
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)


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
    if new_status in {"Interested", "Declined", "Declined (No Discount)", "Approved"}:
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
        await add_email_sent_marker(issue_id, new_status)  # Write marker BEFORE sending
        email_result = send_email(
            to=recipient_email,
            subject=template["subject"],
            body=template["body"],
            cc=template.get("cc", ""),
            html_body=template.get("html_body", ""),
        )
        slack_text = f":white_check_mark: *{identifier}* → *Interested*"

    elif new_status == "Declined":
        if is_pitch:
            template = decline_pitch_email(recipient_name, _parse_subject_from_title(title))
        else:
            template = decline_submission_email(recipient_name)
        await add_email_sent_marker(issue_id, new_status)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))
        slack_text = f":no_entry_sign: *{identifier}* → *Declined*"

    elif new_status == "Declined (No Discount)":
        if is_pitch:
            template = decline_pitch_no_discount_email(recipient_name, _parse_subject_from_title(title))
        else:
            template = decline_submission_no_discount_email(recipient_name)
        await add_email_sent_marker(issue_id, new_status)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))
        slack_text = f":no_entry_sign: *{identifier}* → *Declined (No Discount)*"

    elif new_status == "Approved":
        prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
        subject = _parse_subject_from_title(title) if is_pitch else "Your Eight Sleep Partnership"
        template = approved_email(recipient_name, subject, form_url=prefilled_url)
        await add_email_sent_marker(issue_id, new_status)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))
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

    # Post Slack notification for email-triggering status changes
    if email_result.get("sent"):
        email_status = recipient_email
        email_icon = ":envelope:"
    else:
        email_status = f"{recipient_email} (:x: FAILED)"
        email_icon = ":rotating_light:"
        # Alert on failure so Judith knows to act manually
        slack_text = f":rotating_light: *{identifier}* → *{new_status}* — EMAIL FAILED"

    await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=[{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{slack_text}\n"
                        f"_{title}_\n"
                        f"{email_icon} Email to {email_status}\n"
                        f"<{issue_url}|View in Linear>",
            },
        }],
        text=f"{identifier} → {new_status}, email to {recipient_email}",
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/api/health-report")
async def health_report(request: Request):
    """Receive daily health check report from Apps Script and post to Slack."""
    body = await request.json()
    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != WEBHOOK_SECRET:
        return {"ok": False, "error": "unauthorized"}

    report_text = body.get("report", "No report content")
    try:
        post_message(SPORTS_INBOUND_CHANNEL, report_text)
        return {"ok": True, "message": "Health report posted to Slack"}
    except Exception as e:
        logging.error("Failed to post health report: %s", e)
        return {"ok": False, "error": str(e)}


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

    # 3. Post triage notification to #sports-inbound WITH buttons
    linear_issue_id = linear_result.get("id", "")
    blocks = build_submission_message(
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
        linear_issue_id=linear_issue_id,
    )

    slack_result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New partnership form submission from {full_name} <{email_addr}>",
    )

    # 4. Send auto-acknowledge email
    ack = submission_acknowledge_email(full_name)
    email_result = send_email(to=email_addr, subject=ack["subject"], body=ack["body"], html_body=ack.get("html_body", ""))

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
    """Build a branded form URL with name and email pre-filled."""
    base = FORM_URL  # https://judith-aldabo.github.io/sports-partnership-form/
    params = urlencode({
        "name": name,
        "email": email,
    })
    return f"{base}?{params}"


# ---------------------------------------------------------------------------
# Webhook authentication helper
# ---------------------------------------------------------------------------
def _verify_webhook_secret(request: Request) -> bool:
    """Verify the webhook request has a valid secret. Returns True if valid or no secret configured."""
    if not WEBHOOK_SECRET:
        return True  # No secret configured — allow all (dev mode)
    # Accept secret via header or query param
    header_secret = request.headers.get("X-Webhook-Secret", "")
    query_secret = request.query_params.get("secret", "")
    return hmac.compare_digest(WEBHOOK_SECRET, header_secret) or hmac.compare_digest(WEBHOOK_SECRET, query_secret)


# ---------------------------------------------------------------------------
# Webhook: New email from Zapier (Zap 1 trigger -> webhook action)
# ---------------------------------------------------------------------------
@app.post("/webhook/new-email")
async def webhook_new_email(request: Request):
    """Receive email data from Zapier Zap 1 and post to #sports-inbound with buttons."""
    if not _verify_webhook_secret(request):
        return Response(status_code=403, content="Invalid webhook secret")

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

    # 2. Post triage notification to #sports-inbound WITH buttons
    linear_issue_id = linear_result.get("id", "")
    blocks = build_pitch_message(
        sender_name=sender_name,
        sender_email=sender_email,
        subject=subject,
        date=date,
        route=route,
        preview=preview,
        linear_url=linear_url,
        linear_id=linear_id,
        linear_issue_id=linear_issue_id,
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
    if not _verify_webhook_secret(request):
        return Response(status_code=403, content="Invalid webhook secret")

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

    # 2. Post triage notification to #sports-inbound WITH buttons
    linear_issue_id = linear_result.get("id", "")
    blocks = build_submission_message(
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
        linear_issue_id=linear_issue_id,
    )

    slack_result = await post_message(
        channel=SPORTS_INBOUND_CHANNEL,
        blocks=blocks,
        text=f"New partnership form submission from {full_name} <{email_addr}>",
    )

    # 3. Send auto-acknowledge email to submitter
    ack = submission_acknowledge_email(full_name)
    email_result = send_email(to=email_addr, subject=ack["subject"], body=ack["body"], html_body=ack.get("html_body", ""))

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

    # SECURITY: Verify Slack signing secret on every request.
    # This is MANDATORY — button clicks trigger emails from sports@eightsleep.com.
    # An unvalidated endpoint would allow anyone to fire emails.
    if not SLACK_SIGNING_SECRET:
        logger.error("SLACK_SIGNING_SECRET is not configured — rejecting all interactions")
        return Response(status_code=500, content="Server misconfigured: signing secret missing")

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
        logger.warning("Invalid Slack signature — possible spoofing attempt")
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

    # Parse value (format: "email|||name|||subject_or_row|||linear_issue_id")
    parts = value.split("|||")
    recipient_email = parts[0] if len(parts) > 0 else ""
    recipient_name = parts[1] if len(parts) > 1 else ""
    third_field = parts[2] if len(parts) > 2 else ""
    linear_issue_id = parts[3] if len(parts) > 3 else ""

    # Dispatch based on action_id
    # --- Primary triage actions (all with undo grace period) ---
    # INVARIANT: No email is sent without explicit human button click (Judith Aldabo).
    if action_id in ("pitch_send_form", "pitch_interested", "pitch_approved"):
        # pitch_interested and pitch_approved are legacy aliases
        await _start_pending_action(
            "pitch_send_form", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
        )

    elif action_id in ("pitch_decline_w_code", "pitch_decline"):
        # pitch_decline is legacy alias
        await _start_pending_action(
            "pitch_decline_w_code", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
        )

    elif action_id == "pitch_decline_no_discount":
        await _start_pending_action(
            "pitch_decline_no_discount", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
        )

    elif action_id == "pitch_hold":
        await _handle_hold(
            channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
            is_pitch=True,
        )

    elif action_id == "pitch_duplicate":
        await _handle_duplicate(
            channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
            is_pitch=True,
        )

    elif action_id in ("submission_loop_in_judith", "submission_interested"):
        # submission_interested is legacy alias
        await _start_pending_action(
            "submission_loop_in_judith", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
        )

    elif action_id == "submission_hold":
        await _handle_hold(
            channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
            is_pitch=False,
        )

    elif action_id in ("submission_decline_w_code", "submission_decline"):
        # submission_decline is legacy alias
        await _start_pending_action(
            "submission_decline_w_code", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
        )

    elif action_id == "submission_decline_no_discount":
        await _start_pending_action(
            "submission_decline_no_discount", channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
        )

    elif action_id == "submission_duplicate":
        await _handle_duplicate(
            channel_id, message_ts, original_blocks,
            recipient_email, recipient_name, third_field, linear_issue_id,
            is_pitch=False,
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

# Map action_type -> Linear status that will be set when the action executes.
# Used to mark transitions as handled IMMEDIATELY (before the undo delay)
# so the poller and webhook never race and duplicate the email.
_ACTION_TARGET_STATUS: dict[str, str] = {
    "pitch_send_form": "Interested",
    "pitch_interested": "Interested",
    "pitch_approved": "Interested",
    "pitch_decline_w_code": "Declined",
    "pitch_decline": "Declined",
    "pitch_decline_no_discount": "Declined (No Discount)",
    "submission_loop_in_judith": "Interested",
    "submission_interested": "Interested",
    "submission_decline_w_code": "Declined",
    "submission_decline": "Declined",
    "submission_decline_no_discount": "Declined (No Discount)",
}


async def _start_pending_action(
    action_type: str,
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, third_field: str, linear_issue_id: str = "",
):
    """Replace buttons with a countdown + UNDO button. Execute after UNDO_WINDOW_SECONDS.

    CRITICAL: We mark the transition as handled IMMEDIATELY — before the undo
    delay — so the Linear poller (every 60 s) and the Linear webhook cannot
    race and fire a duplicate email during the 10-second undo window.
    Two layers:
      1. In-memory (_issue_status_cache + _processed_transitions) — fast,
         prevents the poller from even detecting the change.
      2. Persistent (Linear comment marker) — survives OOM / restart.
    """
    pending_id = str(uuid.uuid4())[:8]

    action_labels = {
        "pitch_send_form": "SEND FORM",
        "pitch_interested": "SEND FORM",  # legacy
        "pitch_approved": "SEND FORM",    # legacy
        "pitch_decline_w_code": "DECLINE W CODE",
        "pitch_decline": "DECLINE W CODE",  # legacy
        "pitch_decline_no_discount": "DECLINE NO DISCOUNT",
        "submission_loop_in_judith": "LOOP IN JUDITH",
        "submission_interested": "LOOP IN JUDITH",  # legacy
        "submission_decline_w_code": "DECLINE W CODE",
        "submission_decline": "DECLINE W CODE",  # legacy
        "submission_decline_no_discount": "DECLINE NO DISCOUNT",
    }
    label = action_labels.get(action_type, action_type.upper())

    # ── DEDUP: mark IMMEDIATELY, BEFORE the undo delay ──────────────────
    target_status = _ACTION_TARGET_STATUS.get(action_type, "")
    if linear_issue_id and target_status:
        # Layer 1 — in-memory (prevents poller from detecting change)
        _mark_transition_handled(linear_issue_id, target_status)
        # Layer 2 — persistent marker in Linear (survives OOM / restart)
        await add_email_sent_marker(linear_issue_id, target_status)
        logger.info(
            "Dedup: pre-marked %s -> %s BEFORE undo delay",
            linear_issue_id[:12], target_status,
        )

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
        "linear_issue_id": linear_issue_id,
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
    linear_id = pending.get("linear_issue_id", "")

    if action_type in ("pitch_send_form", "pitch_interested"):
        await _handle_pitch_send_form(channel, ts, blocks, email, name, third_field, linear_id)
    elif action_type in ("pitch_decline_w_code", "pitch_decline"):
        await _handle_pitch_decline_w_code(channel, ts, blocks, email, name, third_field, linear_id)
    elif action_type == "pitch_decline_no_discount":
        await _handle_pitch_decline_no_discount(channel, ts, blocks, email, name, third_field, linear_id)
    elif action_type in ("submission_loop_in_judith", "submission_interested"):
        await _handle_submission_loop_in_judith(channel, ts, blocks, email, name, third_field, linear_id)
    elif action_type in ("submission_decline_w_code", "submission_decline"):
        await _handle_submission_decline_w_code(channel, ts, blocks, email, name, third_field, linear_id)
    elif action_type == "submission_decline_no_discount":
        await _handle_submission_decline_no_discount(channel, ts, blocks, email, name, third_field, linear_id)


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
        cc=template.get("cc", ""), html_body=template.get("html_body", ""),
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
        return email, _clean_name(name_match.group(1).strip())

    # For pitches: look for From field
    from_match = re.search(r"\*\*From:\*\*\s*(.+?)\s*<", description)
    if from_match:
        return email, _clean_name(from_match.group(1).strip())

    # Fallback: try title parsing
    return email, ""


def _clean_name(name: str) -> str:
    """Strip markdown link artifacts and email fragments from a parsed name.

    Linear auto-formats emails as [email](mailto:email), which can leak into
    name fields when parsed from descriptions. Also strips stray brackets,
    parentheses, and angle brackets.
    """
    # Remove markdown link patterns like [text](url)
    name = re.sub(r'\[([^\]]*)]\([^)]*\)', r'\1', name)
    # Remove any leftover <email> or (email) fragments
    name = re.sub(r'<[^>]*>', '', name)
    name = re.sub(r'\([^)]*@[^)]*\)', '', name)
    # Remove stray brackets and parens
    name = re.sub(r'[\[\]()]', '', name)
    # Remove email addresses that leaked in
    name = re.sub(r'\S+@\S+', '', name)
    # Collapse whitespace
    return re.sub(r'\s+', ' ', name).strip()


def _parse_name_from_title(title: str) -> str:
    """Extract name from Linear issue title like '[Form] Name — Org (Sport)' or '[Pitch] Name — Subject'."""
    match = re.match(r"\[(?:Form|Pitch)\]\s*(.+?)\s*[—\-]", title)
    return _clean_name(match.group(1).strip()) if match else ""


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

    # Dedup layer 1: fast in-memory check (catches button-click-originated transitions instantly)
    dedup_key = f"{issue_id}:{new_status}"
    if dedup_key in _processed_transitions:
        logger.info("Webhook: skipping %s → %s (in-memory dedup)", identifier, new_status)
        return {"ok": True, "status_changed": new_status, "email_sent": False, "reason": "Dedup: already sent (in-memory)"}

    # Dedup layer 2: cross-machine via Linear comments (survives restarts)
    if new_status in {"Interested", "Declined", "Declined (No Discount)", "Approved"}:
        if await check_email_sent_marker(issue_id, new_status):
            logger.info("Webhook: skipping %s → %s (dedup marker found)", identifier, new_status)
            return {"ok": True, "status_changed": new_status, "email_sent": False, "reason": "Dedup: already sent"}

    if new_status == "Interested":
        if is_pitch:
            # Pitch → Interested: send form link
            prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
            template = approved_email(recipient_name, _parse_subject_from_title(title), form_url=prefilled_url)
        else:
            # Submission → Interested: send handoff email CC Judith
            template = interested_email(recipient_name)
        await add_email_sent_marker(issue_id, new_status)  # Write marker BEFORE sending
        email_result = send_email(
            to=recipient_email,
            subject=template["subject"],
            body=template["body"],
            cc=template.get("cc", ""),
            html_body=template.get("html_body", ""),
        )
        slack_text = f":white_check_mark: *{identifier}* → *Interested*"

    elif new_status == "Declined":
        if is_pitch:
            template = decline_pitch_email(recipient_name, _parse_subject_from_title(title))
        else:
            template = decline_submission_email(recipient_name)
        await add_email_sent_marker(issue_id, new_status)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))
        slack_text = f":no_entry_sign: *{identifier}* → *Declined*"

    elif new_status == "Declined (No Discount)":
        if is_pitch:
            template = decline_pitch_no_discount_email(recipient_name, _parse_subject_from_title(title))
        else:
            template = decline_submission_no_discount_email(recipient_name)
        await add_email_sent_marker(issue_id, new_status)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))
        slack_text = f":no_entry_sign: *{identifier}* → *Declined (No Discount)*"

    elif new_status == "Approved":
        prefilled_url = _build_prefilled_form_url(recipient_name, recipient_email)
        subject = _parse_subject_from_title(title) if is_pitch else "Your Eight Sleep Partnership"
        template = approved_email(recipient_name, subject, form_url=prefilled_url)
        await add_email_sent_marker(issue_id, new_status)
        email_result = send_email(to=recipient_email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))
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


# ---------------------------------------------------------------------------
# Action handlers — all update Linear, send email (if applicable), confirm in Slack
# INVARIANT: No email fires without explicit human button click.
# ---------------------------------------------------------------------------

async def _handle_pitch_send_form(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, subject: str, linear_issue_id: str = "",
):
    """SEND FORM (pitch): Send form link email + update Linear + confirm.

    NOTE: Dedup marking already happened in _start_pending_action() BEFORE
    the undo delay. We do NOT re-mark here to avoid wasted API calls.
    """

    prefilled_url = _build_prefilled_form_url(name, email)
    template = approved_email(name, subject, form_url=prefilled_url)
    email_result = send_email(
        to=email, subject=template["subject"], body=template["body"],
        html_body=template.get("html_body", ""),
    )

    # Update Linear status
    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Interested")

    # For pitches, just disable buttons (no quick-reply — form link is the next step)
    updated = _disable_buttons(blocks, "SEND FORM")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (:rotating_light: EMAIL FAILED)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — form link sent to {email_status}",
    )


async def _handle_pitch_decline_w_code(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, subject: str, linear_issue_id: str = "",
):
    """DECLINE W CODE (pitch): Send decline email with discount + update Linear + confirm."""
    # Dedup marking already done in _start_pending_action()

    template = decline_pitch_email(name, subject)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))

    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Declined")

    updated = _disable_buttons(blocks, "DECLINE W CODE")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (:rotating_light: EMAIL FAILED)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — decline email (with discount code) sent to {email_status}",
    )


async def _handle_pitch_decline_no_discount(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, subject: str, linear_issue_id: str = "",
):
    """NO DISCOUNT (pitch): Send clean decline email + update Linear + confirm."""
    # Dedup marking already done in _start_pending_action()

    template = decline_pitch_no_discount_email(name, subject)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))

    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Declined (No Discount)")

    updated = _disable_buttons(blocks, "DECLINE NO DISCOUNT")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (:rotating_light: EMAIL FAILED)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — decline email (no discount) sent to {email_status}",
    )


async def _handle_hold(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, third_field: str, linear_issue_id: str = "",
    is_pitch: bool = False,
):
    """HOLD: No email sent. Update Linear + sheet + show reminder buttons."""
    _mark_transition_handled(linear_issue_id, "Hold")
    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Hold")

    if not is_pitch and third_field:
        update_row_status(third_field, status="Hold", next_action="Revisit next quarter")

    # Replace buttons with reminder interval options
    updated = _replace_with_hold_reminder_buttons(blocks, email, name)
    await update_message(channel, ts, updated)

    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — {name} marked as Hold. No email sent.\n"
        f"_Choose when to be reminded above._",
    )


async def _handle_duplicate(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, third_field: str, linear_issue_id: str = "",
    is_pitch: bool = False,
):
    """DUPLICATE: No email sent. Update Linear + confirm."""
    _mark_transition_handled(linear_issue_id, "Duplicate")
    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Duplicate")

    updated = _disable_buttons(blocks, "DUPLICATE")
    await update_message(channel, ts, updated)

    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — {name} marked as Duplicate. No email sent.",
    )


async def _handle_submission_loop_in_judith(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str, linear_issue_id: str = "",
):
    """LOOP IN JUDITH (submission): Send handoff email CC Judith + update Linear + sheet + quick-reply buttons."""
    # Dedup marking already done in _start_pending_action()

    template = interested_email(name)
    email_result = send_email(
        to=email, subject=template["subject"], body=template["body"],
        cc=template["cc"], html_body=template.get("html_body", ""),
    )

    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Interested")

    update_row_status(row, status="Interested", next_action="Judith to follow up")

    # Replace original buttons with quick-reply template buttons
    updated = _replace_with_quick_reply_buttons(blocks, email, name, row)
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (:rotating_light: EMAIL FAILED)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — handoff email sent to {email_status}, {JUDITH_EMAIL} CCd\n"
        f"_Optional: click a follow-up button above to send an additional email._",
    )


async def _handle_submission_decline_w_code(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str, linear_issue_id: str = "",
):
    """DECLINE W CODE (submission): Send decline email with discount + update Linear + sheet + confirm."""
    # Dedup marking already done in _start_pending_action()

    template = decline_submission_email(name)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))

    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Declined")

    update_row_status(row, status="Declined")

    updated = _disable_buttons(blocks, "DECLINE W CODE")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (:rotating_light: EMAIL FAILED)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — decline email (with discount code) sent to {email_status}",
    )


async def _handle_submission_decline_no_discount(
    channel: str, ts: str, blocks: list[dict],
    email: str, name: str, row: str, linear_issue_id: str = "",
):
    """NO DISCOUNT (submission): Send clean decline email + update Linear + sheet + confirm."""
    # Dedup marking already done in _start_pending_action()

    template = decline_submission_no_discount_email(name)
    email_result = send_email(to=email, subject=template["subject"], body=template["body"], html_body=template.get("html_body", ""))

    if linear_issue_id:
        await update_issue_status(linear_issue_id, "Declined (No Discount)")

    update_row_status(row, status="Declined (No Discount)")

    updated = _disable_buttons(blocks, "DECLINE NO DISCOUNT")
    await update_message(channel, ts, updated)

    email_status = email if email_result.get("sent") else f"{email} (:rotating_light: EMAIL FAILED)"
    await post_thread_confirmation(
        channel, ts,
        f":white_check_mark: Done — decline email (no discount) sent to {email_status}",
    )
