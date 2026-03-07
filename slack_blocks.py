"""Slack Block Kit message builders for Sports Inbound."""
from typing import Optional


def build_pitch_message(
    sender_name: str,
    sender_email: str,
    subject: str,
    date: str,
    route: str,
    preview: str,
    linear_url: str = "",
    linear_id: str = "",
    linear_issue_id: str = "",
) -> list[dict]:
    """Build Block Kit message for new pitch with full triage buttons.

    INVARIANT: No email is sent without explicit human button click.
    Buttons: INTERESTED, HOLD, DECLINE, NO DISCOUNT, DUPLICATE.
    """
    # Encode linear_issue_id as 4th field in button value
    val = f"{sender_email}|||{sender_name}|||{subject}|||{linear_issue_id}"
    linear_text = f"<{linear_url}|{linear_id}>" if linear_url else ""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":incoming_envelope: New sponsorship pitch", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*From:* {sender_name} <{sender_email}>"},
                {"type": "mrkdwn", "text": f"*Route:* {route}"},
                {"type": "mrkdwn", "text": f"*Subject:* {subject}"},
                {"type": "mrkdwn", "text": f"*Received:* {date}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Preview:*\n{preview[:500]}"},
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f":warning: Nothing sends until you click a button.{('  ' + linear_text) if linear_text else ''}",
                }
            ],
        },
        {
            "type": "actions",
            "block_id": "pitch_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "INTERESTED", "emoji": True},
                    "style": "primary",
                    "action_id": "pitch_interested",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "HOLD", "emoji": True},
                    "action_id": "pitch_hold",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "DECLINE", "emoji": True},
                    "style": "danger",
                    "action_id": "pitch_decline",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "NO DISCOUNT", "emoji": True},
                    "action_id": "pitch_decline_no_discount",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "DUPLICATE", "emoji": True},
                    "action_id": "pitch_duplicate",
                    "value": val,
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"_Internal ref \u2014 do not edit:_\n"
                        f"_REPLY_TO_EMAIL: {sender_email}_\n"
                        f"_REPLY_TO_NAME: {sender_name}_\n"
                        f"_ORIGINAL_SUBJECT: {subject}_\n"
                        f"_LINEAR_ID: {linear_issue_id}_"
                    ),
                }
            ],
        },
    ]


def build_submission_message(
    full_name: str,
    email: str,
    organization: str,
    sport: str,
    athlete_property: str,
    market: str,
    reach: str,
    partnership_type: str,
    budget: str = "",
    socials: str = "",
    used_before: str = "",
    pitch: str = "",
    sheet_row: str = "",
    sheet_url: str = "",
    linear_url: str = "",
    linear_id: str = "",
    linear_issue_id: str = "",
) -> list[dict]:
    """Build Block Kit message for form submission with full triage buttons.

    INVARIANT: No email is sent without explicit human button click.
    Buttons: INTERESTED, HOLD, DECLINE, NO DISCOUNT, DUPLICATE.
    """
    # Encode linear_issue_id as 4th field in button value
    val = f"{email}|||{full_name}|||{sheet_row}|||{linear_issue_id}"
    linear_text = f"<{linear_url}|{linear_id}>" if linear_url else ""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":clipboard: New partnership form submission", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Name:* {full_name}"},
                {"type": "mrkdwn", "text": f"*Email:* {email}"},
                {"type": "mrkdwn", "text": f"*Organization:* {organization}"},
                {"type": "mrkdwn", "text": f"*Sport:* {sport}"},
                {"type": "mrkdwn", "text": f"*Athlete / Property:* {athlete_property}"},
                {"type": "mrkdwn", "text": f"*Market:* {market}"},
                {"type": "mrkdwn", "text": f"*Reach:* {reach}"},
                {"type": "mrkdwn", "text": f"*Type:* {partnership_type}"},
                {"type": "mrkdwn", "text": f"*Slept on Pod before?:* {used_before}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Socials:* {socials}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Their pitch:*\n{pitch}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"> <{sheet_url}|Review full sheet>",
            },
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f":warning: Nothing sends until you click a button.{('  ' + linear_text) if linear_text else ''}",
                }
            ],
        },
        {
            "type": "actions",
            "block_id": "submission_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "INTERESTED", "emoji": True},
                    "style": "primary",
                    "action_id": "submission_interested",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "HOLD", "emoji": True},
                    "action_id": "submission_hold",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "DECLINE", "emoji": True},
                    "style": "danger",
                    "action_id": "submission_decline",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "NO DISCOUNT", "emoji": True},
                    "action_id": "submission_decline_no_discount",
                    "value": val,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "DUPLICATE", "emoji": True},
                    "action_id": "submission_duplicate",
                    "value": val,
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"_Internal ref \u2014 do not edit:_\n"
                        f"_SUBMITTER_EMAIL: {email}_\n"
                        f"_SUBMITTER_NAME: {full_name}_\n"
                        f"_SHEET_ROW: {sheet_row}_\n"
                        f"_LINEAR_ID: {linear_issue_id}_"
                    ),
                }
            ],
        },
    ]


def build_pitch_message_readonly(
    sender_name: str,
    sender_email: str,
    subject: str,
    date: str,
    route: str,
    preview: str,
    linear_url: str = "",
    linear_id: str = "",
) -> list[dict]:
    """Build read-only Block Kit message for new pitch notification (no buttons, with Linear link)."""
    linear_text = f"<{linear_url}|View in Linear ({linear_id})>" if linear_url else "_Linear issue pending..._"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "New sponsorship pitch", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*From:* {sender_name} <{sender_email}>"},
                {"type": "mrkdwn", "text": f"*Route:* {route}"},
                {"type": "mrkdwn", "text": f"*Subject:* {subject}"},
                {"type": "mrkdwn", "text": f"*Received:* {date}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Preview:*\n{preview[:500]}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":point_right: {linear_text}"},
        },
    ]


def build_submission_message_readonly(
    full_name: str,
    email: str,
    organization: str,
    sport: str,
    athlete_property: str,
    market: str,
    reach: str,
    partnership_type: str,
    socials: str,
    used_before: str,
    pitch: str,
    sheet_row: str,
    sheet_url: str,
    linear_url: str = "",
    linear_id: str = "",
) -> list[dict]:
    """Build read-only Block Kit message for form submission (no buttons, with Linear link)."""
    linear_text = f"<{linear_url}|View in Linear ({linear_id})>" if linear_url else "_Linear issue pending..._"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "New partnership form submission", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Name:* {full_name}"},
                {"type": "mrkdwn", "text": f"*Email:* {email}"},
                {"type": "mrkdwn", "text": f"*Organization:* {organization}"},
                {"type": "mrkdwn", "text": f"*Sport:* {sport}"},
                {"type": "mrkdwn", "text": f"*Athlete / Property:* {athlete_property}"},
                {"type": "mrkdwn", "text": f"*Market:* {market}"},
                {"type": "mrkdwn", "text": f"*Reach:* {reach}"},
                {"type": "mrkdwn", "text": f"*Type:* {partnership_type}"},
                {"type": "mrkdwn", "text": f"*Slept on Pod before?:* {used_before}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Socials:* {socials}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Their pitch:*\n{pitch}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":point_right: {linear_text}  |  <{sheet_url}|View in Google Sheet>",
            },
        },
    ]


def build_confirmation_blocks(text: str) -> list[dict]:
    """Build a simple confirmation message block."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
    ]
