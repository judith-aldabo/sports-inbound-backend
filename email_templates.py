"""Email templates for all outbound emails from sports@eightsleep.com."""
from app.config import FORM_URL, SENDER_EMAIL, JUDITH_EMAIL, CHECKOUT_URL

SIGNATURE = f"""Best,
Eight Sleep Sports Team
{SENDER_EMAIL}"""


def approved_email(recipient_name: str, original_subject: str, form_url: str = "") -> dict:
    """Form link email sent after APPROVED/INTERESTED on pitch. Uses pre-filled URL if provided."""
    link = form_url or FORM_URL
    return {
        "subject": f"Re: {original_subject}",
        "body": f"""Hi {recipient_name},

Thank you for reaching out. We've reviewed your pitch and would like to learn more.

To help us properly assess the opportunity, please take 3 minutes to complete our partnership form: {link}

We'll be in touch once we've reviewed your submission.

{SIGNATURE}""",
    }


def decline_pitch_email(recipient_name: str, original_subject: str) -> dict:
    """Decline email sent after DECLINE on initial pitch (Zap 2 Path B).

    Includes discount code offer to turn declines into sales opportunities.
    """
    return {
        "subject": "Your Partnership Inquiry \u2014 Eight Sleep Sports",
        "body": f"""Hi {recipient_name},

Thank you for reaching out about a partnership with Eight Sleep.

While we\u2019re not moving forward with a partnership at this time, we want you to have the same competitive advantage our athletes already use every night.

Sleep is where performance is built. Deep, quality sleep is the one recovery tool that changes everything \u2014 reaction time, endurance, mental sharpness, injury resilience.

As a thank you for reaching out, here’s an exclusive offer: visit www.eightsleep.com and use code DEEPSLEEPATHLETE at checkout.

This is the same technology trusted by athletes competing at the highest level in Formula 1, cycling, and beyond.

If things change down the road, don\u2019t hesitate to reach out again \u2014 we\u2019d be happy to revisit.

{SIGNATURE}""",
    }


def submission_acknowledge_email(recipient_name: str) -> dict:
    """Auto-acknowledge email sent when form is submitted."""
    return {
        "subject": "We received your Eight Sleep partnership proposal",
        "body": f"""Hi {recipient_name},

Thank you for submitting your partnership proposal to Eight Sleep. We review all submissions on a rolling basis and will be in touch if there's a fit.

{SIGNATURE}""",
    }


def interested_email(recipient_name: str) -> dict:
    """Handoff email sent after INTERESTED — CC's Judith."""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal",
        "body": f"""Hi {recipient_name},

Thank you for submitting your proposal \u2014 we've had a chance to review it and would love to explore this further.

I'm looping in Judith Aldab\u00f3, our Sports Manager, who will be your main point of contact from here.

{SIGNATURE}""",
        "cc": JUDITH_EMAIL,
    }


def decline_submission_email(recipient_name: str) -> dict:
    """Decline email sent after DECLINE on form submission (Zap 3b Path C).

    Includes discount code offer to turn declines into sales opportunities.
    """
    return {
        "subject": "Your Partnership Proposal \u2014 Eight Sleep Sports",
        "body": f"""Hi {recipient_name},

We read every proposal we receive. Yours was no exception.

We\u2019re not in a position to move forward commercially right now \u2014 but we want to be direct with you: that says nothing about what you\u2019re building. Timing and fit are their own thing.

What we do know is that serious athletes deserve serious recovery. Deep sleep isn\u2019t passive \u2014 it\u2019s where strength is rebuilt, decisions sharpen, and the body does the work no training session can replicate.

We want you to have that edge — visit www.eightsleep.com and use code DEEPSLEEPATHLETE at checkout.

This is the technology our partners use every night before they compete. Now it\u2019s available to you.

If things change down the road, don\u2019t hesitate to reach out again.

{SIGNATURE}""",
    }


# ---------------------------------------------------------------------------
# Decline (No Discount) templates — for spam/irrelevant pitches
# ---------------------------------------------------------------------------

def decline_pitch_no_discount_email(recipient_name: str, original_subject: str) -> dict:
    """Clean decline email with no discount code — for spam or irrelevant pitches."""
    return {
        "subject": "Your Partnership Inquiry \u2014 Eight Sleep Sports",
        "body": f"""Hi {recipient_name},

Thank you for reaching out about a partnership with Eight Sleep.

After reviewing your proposal, we\u2019ve decided not to move forward at this time.

We appreciate your interest and wish you all the best.

{SIGNATURE}""",
    }


def decline_submission_no_discount_email(recipient_name: str) -> dict:
    """Clean decline email for form submissions with no discount code."""
    return {
        "subject": "Your Partnership Proposal \u2014 Eight Sleep Sports",
        "body": f"""Hi {recipient_name},

Thank you for submitting your partnership proposal to Eight Sleep.

After reviewing your submission, we\u2019ve decided not to move forward at this time.

We appreciate your interest and wish you all the best.

{SIGNATURE}""",
    }


# ---------------------------------------------------------------------------
# V2: Quick-reply templates (used after INTERESTED)
# ---------------------------------------------------------------------------

def schedule_call_email(recipient_name: str) -> dict:
    """Follow-up email to schedule a call after INTERESTED."""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal - Let's Schedule a Call",
        "body": f"""Hi {recipient_name},

Thank you again for your interest in partnering with Eight Sleep. We'd love to learn more about your proposal.

Would you be available for a brief call this week or next? Please share a few times that work for you and we'll get something on the calendar.

{SIGNATURE}""",
        "cc": JUDITH_EMAIL,
    }


def request_media_kit_email(recipient_name: str) -> dict:
    """Follow-up email requesting a media kit after INTERESTED."""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal - Media Kit Request",
        "body": f"""Hi {recipient_name},

Thank you for your interest in partnering with Eight Sleep. We'd like to learn more about your reach and audience.

Could you please send over your media kit or any relevant materials (audience demographics, engagement metrics, past brand partnerships, etc.)? This will help us evaluate alignment and next steps.

{SIGNATURE}""",
        "cc": JUDITH_EMAIL,
    }


def request_rate_card_email(recipient_name: str) -> dict:
    """Follow-up email requesting a rate card after INTERESTED."""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal - Rate Card Request",
        "body": f"""Hi {recipient_name},

Thank you for your interest in partnering with Eight Sleep. We'd love to understand your pricing structure.

Could you share your rate card or a breakdown of partnership tiers and pricing? This will help us assess the opportunity and determine next steps.

{SIGNATURE}""",
        "cc": JUDITH_EMAIL,
    }
