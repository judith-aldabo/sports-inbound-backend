"""Email templates for all outbound emails from sports@eightsleep.com."""
from app.config import FORM_URL, SENDER_EMAIL, JUDITH_EMAIL, CHECKOUT_URL

SIGNATURE = f"""Best,
Eight Sleep Sports Team
{SENDER_EMAIL}"""

HTML_SIGNATURE = f"Best,<br>Eight Sleep Sports Team<br>{SENDER_EMAIL}"


def approved_email(recipient_name: str, original_subject: str, form_url: str = "") -> dict:
    """Form link email sent after APPROVED/INTERESTED on pitch. Uses pre-filled URL if provided."""
    link = form_url or FORM_URL
    plain_body = f"""Hi {recipient_name},

Thanks for reaching out. We'd like to learn more about this opportunity.

Please take a few minutes to fill out our partnership form so we can properly evaluate the fit: {link}

We'll follow up once we've reviewed your submission.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>Thanks for reaching out. We'd like to learn more about this opportunity.</p>
<p>Please take a few minutes to fill out our <a href="{link}">partnership form</a> so we can properly evaluate the fit.</p>
<p>We'll follow up once we've reviewed your submission.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": f"Re: {original_subject}",
        "body": plain_body,
        "html_body": html_body,
    }


def decline_pitch_email(recipient_name: str, original_subject: str) -> dict:
    """Decline email sent after DECLINE on initial pitch (Zap 2 Path B).

    Includes discount code offer to turn declines into sales opportunities.
    """
    plain_body = f"""Hi {recipient_name},

Thank you for reaching out about a partnership with Eight Sleep.

We\u2019re not able to move forward with a partnership at this time, but we still want you to have what our athletes use every night.

Deep sleep is the one recovery tool that changes everything \u2014 reaction time, endurance, mental sharpness, injury resilience. It\u2019s where real performance is built.

As a thank you for reaching out: visit www.eightsleep.com and use code DEEPSLEEPATHLETE at checkout for an exclusive discount.

If things change down the road, don\u2019t hesitate to reach out again.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>Thank you for reaching out about a partnership with Eight Sleep.</p>
<p>We\u2019re not able to move forward with a partnership at this time, but we still want you to have what our athletes use every night.</p>
<p>Deep sleep is the one recovery tool that changes everything \u2014 reaction time, endurance, mental sharpness, injury resilience. It\u2019s where real performance is built.</p>
<p>As a thank you for reaching out: visit <a href="https://www.eightsleep.com">www.eightsleep.com</a> and use code <strong>DEEPSLEEPATHLETE</strong> at checkout for an exclusive discount.</p>
<p>If things change down the road, don\u2019t hesitate to reach out again.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Your Partnership Inquiry \u2014 Eight Sleep Sports",
        "body": plain_body,
        "html_body": html_body,
    }


def submission_acknowledge_email(recipient_name: str) -> dict:
    """Auto-acknowledge email sent when form is submitted."""
    plain_body = f"""Hi {recipient_name},

Thanks for submitting your partnership proposal. We review every submission and will be in touch if there's a fit.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>Thanks for submitting your partnership proposal. We review every submission and will be in touch if there's a fit.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "We received your Eight Sleep partnership proposal",
        "body": plain_body,
        "html_body": html_body,
    }


def interested_email(recipient_name: str) -> dict:
    """Handoff email sent after INTERESTED — CC's Judith."""
    plain_body = f"""Hi {recipient_name},

We've reviewed your proposal and would love to explore this further.

I'm looping in Judith Aldab\u00f3, our Sports Partnerships Manager, who will be your point of contact from here.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>We've reviewed your proposal and would love to explore this further.</p>
<p>I'm looping in Judith Aldab\u00f3, our Sports Partnerships Manager, who will be your point of contact from here.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal",
        "body": plain_body,
        "html_body": html_body,
        "cc": JUDITH_EMAIL,
    }


def decline_submission_email(recipient_name: str) -> dict:
    """Decline email sent after DECLINE on form submission (Zap 3b Path C).

    Includes discount code offer to turn declines into sales opportunities.
    """
    plain_body = f"""Hi {recipient_name},

We read every proposal we receive, and we appreciate the time you put into yours.

We're not able to move forward right now \u2014 but that's about timing and fit, not a reflection on what you're building.

What we do know: serious athletes deserve serious recovery. Deep sleep is where strength is rebuilt and the body does the work no training session can replicate.

We want you to have that edge \u2014 visit www.eightsleep.com and use code DEEPSLEEPATHLETE at checkout.

If things change down the road, don't hesitate to reach out again.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>We read every proposal we receive, and we appreciate the time you put into yours.</p>
<p>We're not able to move forward right now \u2014 but that's about timing and fit, not a reflection on what you're building.</p>
<p>What we do know: serious athletes deserve serious recovery. Deep sleep is where strength is rebuilt and the body does the work no training session can replicate.</p>
<p>We want you to have that edge \u2014 visit <a href="https://www.eightsleep.com">www.eightsleep.com</a> and use code <strong>DEEPSLEEPATHLETE</strong> at checkout.</p>
<p>If things change down the road, don't hesitate to reach out again.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Your Partnership Proposal — Eight Sleep Sports",
        "body": plain_body,
        "html_body": html_body,
    }


# ---------------------------------------------------------------------------
# Decline (No Discount) templates — for spam/irrelevant pitches
# ---------------------------------------------------------------------------

def decline_pitch_no_discount_email(recipient_name: str, original_subject: str) -> dict:
    """Clean decline email with no discount code — for spam or irrelevant pitches."""
    plain_body = f"""Hi {recipient_name},

Thank you for reaching out about a partnership with Eight Sleep.

After reviewing your proposal, we’ve decided not to move forward at this time.

We appreciate your interest and wish you all the best.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>Thank you for reaching out about a partnership with Eight Sleep.</p>
<p>After reviewing your proposal, we’ve decided not to move forward at this time.</p>
<p>We appreciate your interest and wish you all the best.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Your Partnership Inquiry — Eight Sleep Sports",
        "body": plain_body,
        "html_body": html_body,
    }

def decline_submission_no_discount_email(recipient_name: str) -> dict:
    """Clean decline email for form submissions with no discount code."""
    plain_body = f"""Hi {recipient_name},

Thank you for submitting your partnership proposal to Eight Sleep.

After reviewing your submission, we\u2019ve decided not to move forward at this time.

We appreciate your interest and wish you all the best.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>Thank you for submitting your partnership proposal to Eight Sleep.</p>
<p>After reviewing your submission, we\u2019ve decided not to move forward at this time.</p>
<p>We appreciate your interest and wish you all the best.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Your Partnership Proposal \u2014 Eight Sleep Sports",
        "body": plain_body,
        "html_body": html_body,
    }


# ---------------------------------------------------------------------------
# V2: Quick-reply templates (used after INTERESTED)
# ---------------------------------------------------------------------------

def schedule_call_email(recipient_name: str) -> dict:
    """Follow-up email to schedule a call after INTERESTED."""
    plain_body = f"""Hi {recipient_name},

We'd love to learn more about this opportunity. Would you be available for a brief call this week or next?

Please share a few times that work and we'll get something on the calendar.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>We'd love to learn more about this opportunity. Would you be available for a brief call this week or next?</p>
<p>Please share a few times that work and we'll get something on the calendar.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal - Let's Schedule a Call",
        "body": plain_body,
        "html_body": html_body,
        "cc": JUDITH_EMAIL,
    }


def request_media_kit_email(recipient_name: str) -> dict:
    """Follow-up email requesting a media kit after INTERESTED."""
    plain_body = f"""Hi {recipient_name},

We'd like to learn more about your reach and audience. Could you send over a media kit or any relevant materials (audience demographics, engagement metrics, past brand partnerships)?

This will help us evaluate the fit and determine next steps.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>We'd like to learn more about your reach and audience. Could you send over a media kit or any relevant materials (audience demographics, engagement metrics, past brand partnerships)?</p>
<p>This will help us evaluate the fit and determine next steps.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal - Media Kit Request",
        "body": plain_body,
        "html_body": html_body,
        "cc": JUDITH_EMAIL,
    }


def request_rate_card_email(recipient_name: str) -> dict:
    """Follow-up email requesting a rate card after INTERESTED."""
    plain_body = f"""Hi {recipient_name},

We'd love to understand your pricing structure. Could you share a rate card or breakdown of partnership tiers and pricing?

This will help us assess the opportunity and move things forward.

{SIGNATURE}"""
    html_body = f"""<p>Hi {recipient_name},</p>
<p>We'd love to understand your pricing structure. Could you share a rate card or breakdown of partnership tiers and pricing?</p>
<p>This will help us assess the opportunity and move things forward.</p>
<p>{HTML_SIGNATURE}</p>"""
    return {
        "subject": "Re: Your Eight Sleep Partnership Proposal - Rate Card Request",
        "body": plain_body,
        "html_body": html_body,
        "cc": JUDITH_EMAIL,
    }
