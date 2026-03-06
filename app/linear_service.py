"""Linear API service for creating and managing Sports Inbound issues."""
import httpx
import logging
from app.config import LINEAR_API_KEY, LINEAR_TEAM_ID, LINEAR_LABEL_EMAIL_PITCH, LINEAR_LABEL_FORM_SUBMISSION

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.linear.app/graphql"


async def create_pitch_issue(
    sender_name: str,
    sender_email: str,
    subject: str,
    route: str,
    preview: str,
) -> dict:
    """Create a Linear issue for an inbound email pitch. Lands in Triage status."""
    title = f"[Pitch] {sender_name} — {subject}"
    description = (
        f"## Inbound Sponsorship Pitch\n\n"
        f"**From:** {sender_name} <{sender_email}>\n"
        f"**Subject:** {subject}\n"
        f"**Route:** {route}\n\n"
        f"---\n\n"
        f"### Preview\n\n"
        f"{preview}\n\n"
        f"---\n\n"
        f"*Reply to: {sender_email}*"
    )

    return await _create_issue(
        title=title,
        description=description,
        label_id=LINEAR_LABEL_EMAIL_PITCH,
    )


async def create_submission_issue(
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
) -> dict:
    """Create a Linear issue for a form submission. Lands in Triage status."""
    title = f"[Form] {full_name} — {organization} ({sport})"
    description = (
        f"## Partnership Form Submission\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Name** | {full_name} |\n"
        f"| **Email** | {email} |\n"
        f"| **Organization** | {organization} |\n"
        f"| **Sport** | {sport} |\n"
        f"| **Athlete / Property** | {athlete_property} |\n"
        f"| **Market** | {market} |\n"
        f"| **Reach** | {reach} |\n"
        f"| **Partnership Type** | {partnership_type} |\n"
        f"| **Slept on Pod before?** | {used_before} |\n"
        f"| **Social Media** | {socials} |\n\n"
        f"---\n\n"
        f"### Proposal\n\n"
        f"{pitch}\n\n"
        f"---\n\n"
        f"[View in Google Sheet]({sheet_url}) (Row {sheet_row})\n\n"
        f"*Reply to: {email}*"
    )

    return await _create_issue(
        title=title,
        description=description,
        label_id=LINEAR_LABEL_FORM_SUBMISSION,
    )


async def get_issue_details(issue_id: str) -> dict:
    """Fetch full issue details from Linear by ID. Used by webhook handler."""
    query = """
    query IssueDetails($id: String!) {
        issue(id: $id) {
            id
            identifier
            title
            description
            url
            state { name type }
            labels { nodes { name } }
        }
    }
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers={
                "Authorization": LINEAR_API_KEY,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": {"id": issue_id}},
        )
        data = resp.json()

    if "errors" in data:
        logger.error("Linear get_issue_details failed: %s", data["errors"])
        return {"ok": False, "error": str(data["errors"])}

    issue = data.get("data", {}).get("issue")
    if not issue:
        return {"ok": False, "error": "Issue not found"}

    return {
        "ok": True,
        "id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "description": issue.get("description", ""),
        "url": issue.get("url"),
        "state_name": issue.get("state", {}).get("name", ""),
        "state_type": issue.get("state", {}).get("type", ""),
        "labels": [l["name"] for l in issue.get("labels", {}).get("nodes", [])],
    }


async def create_linear_webhook(webhook_url: str) -> dict:
    """Create a Linear webhook to receive issue status change events for the Sports Inbound team."""
    query = """
    mutation WebhookCreate($input: WebhookCreateInput!) {
        webhookCreate(input: $input) {
            success
            webhook {
                id
                enabled
            }
        }
    }
    """
    variables = {
        "input": {
            "url": webhook_url,
            "teamId": LINEAR_TEAM_ID,
            "resourceTypes": ["Issue"],
            "allPublicTeams": False,
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers={
                "Authorization": LINEAR_API_KEY,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
        )
        data = resp.json()

    if "errors" in data:
        logger.error("Linear webhook creation failed: %s", data["errors"])
        return {"ok": False, "error": str(data["errors"])}

    result = data.get("data", {}).get("webhookCreate", {})
    if result.get("success"):
        webhook = result.get("webhook", {})
        logger.info("Linear webhook created: id=%s", webhook.get("id"))
        return {"ok": True, "id": webhook.get("id")}

    return {"ok": False, "error": "Webhook creation returned success=false"}


async def _create_issue(
    title: str,
    description: str,
    label_id: str,
) -> dict:
    """Create a Linear issue in the Sports Inbound team."""
    query = """
    mutation IssueCreate($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
                id
                identifier
                title
                url
                state { name }
            }
        }
    }
    """
    variables = {
        "input": {
            "title": title,
            "description": description,
            "teamId": LINEAR_TEAM_ID,
            "labelIds": [label_id] if label_id else [],
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers={
                "Authorization": LINEAR_API_KEY,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
        )
        data = resp.json()

    if "errors" in data:
        logger.error("Linear create_issue failed: %s", data["errors"])
        return {"ok": False, "error": str(data["errors"])}

    issue_data = data.get("data", {}).get("issueCreate", {})
    if issue_data.get("success"):
        issue = issue_data.get("issue", {})
        logger.info(
            "Linear issue created: %s — %s",
            issue.get("identifier"),
            issue.get("url"),
        )
        return {
            "ok": True,
            "id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "url": issue.get("url"),
            "title": issue.get("title"),
        }

    logger.error("Linear create_issue returned success=false: %s", data)
    return {"ok": False, "error": "Issue creation returned success=false"}
