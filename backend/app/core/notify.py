"""
Notification channel — scientist self-serve outreach
=====================================================
Mints magic-link tokens and delivers them to scientists whose wells were flagged.
Pluggable and **zero-infra by default**: `console` and `link` need no setup, no
keys, and no cost. Real email (`smtp` / `sendgrid`) is opt-in.

Select with NOTIFY_PROVIDER:
  console   — (default) log the magic link to the server log; nothing to set up
  link      — send nothing; the API returns the link for the reviewer to copy/send
  smtp      — send a real email via an SMTP server (opt-in)
  sendgrid  — send via the SendGrid API (opt-in)

Env vars:
  NOTIFY_PROVIDER       default: console
  RESPOND_BASE_URL      default: http://localhost:3000   (frontend origin for the link)
  INVITE_TTL_DAYS       default: 7

  smtp:     NOTIFY_SMTP_HOST, NOTIFY_SMTP_PORT (default 587), NOTIFY_SMTP_USER,
            NOTIFY_SMTP_PASSWORD, NOTIFY_SMTP_FROM
  sendgrid: NOTIFY_SENDGRID_API_KEY, NOTIFY_SENDGRID_FROM
"""

import os
import secrets
import logging

logger = logging.getLogger(__name__)

PROVIDER      = os.getenv("NOTIFY_PROVIDER", "console")
RESPOND_BASE  = os.getenv("RESPOND_BASE_URL", "http://localhost:3000").rstrip("/")
INVITE_TTL    = int(os.getenv("INVITE_TTL_DAYS", "7"))


def mint_token() -> str:
    """A 256-bit opaque, URL-safe token. Stored server-side; not guessable."""
    return secrets.token_urlsafe(32)


def build_link(token: str) -> str:
    return f"{RESPOND_BASE}/respond/{token}"


def _subject(pending: int) -> str:
    n = pending or "your"
    return f"Action needed: {n} flagged well(s) await your decision"


def _body(scientist_name: str, link: str, pending: int) -> str:
    return (
        f"Hi {scientist_name},\n\n"
        f"{pending} of your experiment well(s) were flagged as anomalous during a data "
        f"migration and need your decision before they move to production.\n\n"
        f"Open this secure link to review them and tell us, in your own words, which "
        f"readings are valid and which should be dropped:\n\n"
        f"  {link}\n\n"
        f"The link is personal to you and expires in {INVITE_TTL} days.\n"
    )


# ── Channel implementations ───────────────────────────────────────────────────

async def send_invitation(scientist_name: str, link: str, pending: int) -> dict:
    """
    Deliver one invitation. Returns {channel, status, link}. Never raises on a
    delivery problem — falls back to returning the link so the loop is never blocked.
    """
    if PROVIDER == "console":
        logger.info("NOTIFY[console] %s → %s (%d pending)", scientist_name, link, pending)
        return {"channel": "console", "status": "logged", "link": link}

    if PROVIDER == "link":
        # Reviewer copies/sends the link manually — nothing is dispatched here.
        return {"channel": "link", "status": "returned", "link": link}

    if PROVIDER == "smtp":
        return await _send_smtp(scientist_name, link, pending)

    if PROVIDER == "sendgrid":
        return await _send_sendgrid(scientist_name, link, pending)

    logger.warning("Unknown NOTIFY_PROVIDER=%s — falling back to returning the link", PROVIDER)
    return {"channel": "link", "status": "returned", "link": link}


async def _send_smtp(scientist_name: str, link: str, pending: int) -> dict:
    host = os.getenv("NOTIFY_SMTP_HOST")
    to   = os.getenv("NOTIFY_SMTP_TO_OVERRIDE")  # optional: route all mail to one inbox in dev
    sender = os.getenv("NOTIFY_SMTP_FROM", "noreply@migration.local")
    if not host:
        logger.warning("smtp provider selected but NOTIFY_SMTP_HOST unset — returning link instead")
        return {"channel": "link", "status": "returned", "link": link}
    try:
        import asyncio, smtplib
        from email.message import EmailMessage

        def _send():
            msg = EmailMessage()
            msg["Subject"] = _subject(pending)
            msg["From"]    = sender
            msg["To"]      = to or f"{scientist_name}@example.com"
            msg.set_content(_body(scientist_name, link, pending))
            port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                user, pw = os.getenv("NOTIFY_SMTP_USER"), os.getenv("NOTIFY_SMTP_PASSWORD")
                if user and pw:
                    s.login(user, pw)
                s.send_message(msg)

        await asyncio.to_thread(_send)
        return {"channel": "smtp", "status": "sent", "link": link}
    except Exception as e:
        logger.warning("smtp send failed (%s) — returning link instead", e)
        return {"channel": "link", "status": "returned", "link": link}


async def _send_sendgrid(scientist_name: str, link: str, pending: int) -> dict:
    api_key = os.getenv("NOTIFY_SENDGRID_API_KEY")
    sender  = os.getenv("NOTIFY_SENDGRID_FROM")
    if not api_key or not sender:
        logger.warning("sendgrid selected but key/sender unset — returning link instead")
        return {"channel": "link", "status": "returned", "link": link}
    try:
        import httpx
        payload = {
            "personalizations": [{"to": [{"email": f"{scientist_name}@example.com"}]}],
            "from": {"email": sender},
            "subject": _subject(pending),
            "content": [{"type": "text/plain", "value": _body(scientist_name, link, pending)}],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        if r.status_code >= 300:
            logger.warning("sendgrid HTTP %s — returning link instead", r.status_code)
            return {"channel": "link", "status": "returned", "link": link}
        return {"channel": "sendgrid", "status": "sent", "link": link}
    except Exception as e:
        logger.warning("sendgrid send failed (%s) — returning link instead", e)
        return {"channel": "link", "status": "returned", "link": link}
