import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def send_invite_email(invite) -> bool:
    """Send an admin/staff invite email via Resend.

    Returns True if sent (or skipped because RESEND_API_KEY isn't set --
    local dev/tests don't need a real key, and the link is logged instead),
    False if Resend returned an error. Never raises: a failed email
    shouldn't block invite creation, since the invite row and its token are
    already valid and the link can be shared manually as a fallback.
    """

    from .models import Membership

    accept_url = f"{settings.ADMIN_DASHBOARD_URL}/accept-invite?token={invite.token}"

    if not settings.RESEND_API_KEY:
        logger.info("RESEND_API_KEY not set; skipping invite email. Link: %s", accept_url)
        return True

    role_label = dict(Membership.ROLE_CHOICES).get(invite.role, invite.role)
    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [invite.email],
        "subject": f"Du er invitert som {role_label} — {invite.event.title}",
        "html": (
            f"<p>Hei!</p>"
            f"<p>{invite.invited_by.email} har invitert deg som <strong>{role_label}</strong> "
            f"for {invite.event.title} på Hennings Alternativ Jul.</p>"
            f'<p><a href="{accept_url}">Sett opp kontoen din</a></p>'
            f"<p>Lenken er gyldig i 7 dager.</p>"
        ),
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except urllib.error.URLError as exc:
        logger.error("Failed to send invite email to %s: %s", invite.email, exc)
        return False


def send_password_setup_email(setup_token) -> bool:
    """Send a volunteer the link to set (or reset) their password so they
    can log in to the mobile app -- sent automatically right after a
    passwordless registration, and on demand via the app's "first time /
    forgot password" request flow (see views.request_password_setup), so
    the wording here deliberately doesn't assume which one this is.

    Same fire-and-forget shape as send_invite_email: returns True if sent
    or skipped (no RESEND_API_KEY, e.g. local dev/tests), False on a
    Resend error. Never raises -- a failed email shouldn't block whatever
    triggered it, since the token row is already valid and the link could
    be resent or shared manually."""

    set_password_url = f"{settings.WEBSITE_URL}/set-password.html?token={setup_token.token}"

    if not settings.RESEND_API_KEY:
        logger.info("RESEND_API_KEY not set; skipping password setup email. Link: %s", set_password_url)
        return True

    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [setup_token.user.email],
        "subject": "Sett passord for Hennings Alternativ Jul-appen",
        "html": (
            f"<p>Hei!</p>"
            f"<p>Bruk lenken under for å sette et passord for kontoen din på Hennings Alternativ Jul "
            f"— så kan du logge inn i appen og se oppgavene og vaktene dine.</p>"
            f'<p><a href="{set_password_url}">Sett passord</a></p>'
            f"<p>Har du ikke bedt om dette selv, kan du trygt ignorere denne e-posten.</p>"
        ),
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except urllib.error.URLError as exc:
        logger.error("Failed to send password setup email to %s: %s", setup_token.user.email, exc)
        return False
