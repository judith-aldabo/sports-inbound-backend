"""Slack API service for posting messages and handling interactions."""
import httpx
import logging
from datetime import datetime, timedelta, timezone
from app.config import SLACK_BOT_TOKEN, SPORTS_INBOUND_CHANNEL

logger = logging.getLogger(__name__)


async def post_message(
    channel: str,
    blocks: list[dict],
    text: str = "",
    username: str = "Sports Inbound",
    thread_ts: str | None = None,
) -> dict:
    """Post a message to a Slack channel with Block Kit blocks."""
    payload: dict = {
        "channel": channel,
        "blocks": blocks,
        "text": text or "Sports Inbound notification",
        "username": username,
        "icon_emoji": ":bell:",
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json=payload,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("Slack post_message failed: %s", data.get("error"))
        return data


async def post_thread_confirmation(
    channel: str,
    thread_ts: str,
    text: str,
) -> dict:
    """Post a confirmation message in a thread."""
    from app.slack_blocks import build_confirmation_blocks

    blocks = build_confirmation_blocks(text)
    return await post_message(
        channel=channel,
        blocks=blocks,
        text=text,
        thread_ts=thread_ts,
    )


async def update_message(
    channel: str,
    ts: str,
    blocks: list[dict],
    text: str = "",
) -> dict:
    """Update an existing Slack message (e.g., to disable buttons after click)."""
    payload = {
        "channel": channel,
        "ts": ts,
        "blocks": blocks,
        "text": text or "Sports Inbound notification (updated)",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.update",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json=payload,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("Slack update_message failed: %s", data.get("error"))
        return data


async def schedule_reminder(
    channel: str,
    thread_ts: str,
    text: str,
    remind_at: datetime,
) -> dict:
    """Schedule a Slack reminder by posting a delayed message.

    Since Slack's chat.scheduleMessage doesn't support threads well,
    we use chat.scheduleMessage to the channel with the reminder text.
    """
    post_at = int(remind_at.timestamp())
    payload = {
        "channel": channel,
        "text": text,
        "post_at": post_at,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.scheduleMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json=payload,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("Slack schedule_reminder failed: %s", data.get("error"))
        else:
            logger.info("Reminder scheduled for %s", remind_at.isoformat())
        return data
