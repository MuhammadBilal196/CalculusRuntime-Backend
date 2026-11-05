"""Outgoing email (verification codes).

Configure ONE of these in .env:

  Resend (simplest on hosts that block SMTP ports):
    RESEND_API_KEY=re_...
    EMAIL_FROM=CalcVoyager <no-reply@your-verified-domain.com>

  SMTP (Gmail with an App Password, Outlook, Brevo, Mailgun, ...):
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587            # 465 = SSL, 587 = STARTTLS
    SMTP_USER=you@gmail.com
    SMTP_PASSWORD=your-16-character-app-password
    EMAIL_FROM=CalcVoyager <you@gmail.com>

For local development without a mail account set EMAIL_DEV_MODE=true: the
code and link are printed in the backend console and returned to the
frontend instead of being emailed.
"""

import asyncio
import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

log = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def dev_mode() -> bool:
    return _env("EMAIL_DEV_MODE").lower() in {"1", "true", "yes", "on"}


def provider() -> str:
    """'resend', 'smtp' or '' when nothing is configured."""
    if _env("RESEND_API_KEY"):
        return "resend"
    if _env("SMTP_HOST"):
        return "smtp"
    return ""


def is_configured() -> bool:
    return bool(provider())


def _sender() -> str:
    raw = _env("EMAIL_FROM") or _env("SMTP_USER")
    name, addr = parseaddr(raw)
    if not addr:
        return ""
    return formataddr((name or "CalcVoyager", addr))


def verification_email(code: str, link: str, minutes: int, name: str = "") -> tuple[str, str, str]:
    """(subject, plain text, html) for a verification email."""
    greeting = f"Hi {name}," if name else "Hi,"
    subject = f"{code} is your CalcVoyager verification code"
    text = (
        f"{greeting}\n\n"
        f"Your CalcVoyager verification code is: {code}\n\n"
        f"Or open this link to verify your email:\n{link}\n\n"
        f"The code and link expire in {minutes} minutes. "
        "If you didn't create a CalcVoyager account, you can ignore this email.\n"
    )
    safe_link = html.escape(link, quote=True)
    body = f"""\
<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f6fb;font-family:Segoe UI,Arial,sans-serif;color:#18213d">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
    <table role="presentation" width="100%" style="max-width:480px;background:#ffffff;border:1px solid #dfe5f0;border-radius:16px" cellspacing="0" cellpadding="0">
      <tr><td style="padding:28px 28px 8px">
        <div style="font-size:18px;font-weight:700">
          <span style="display:inline-block;width:30px;height:30px;line-height:30px;text-align:center;border-radius:8px;background:#2b4cc9;color:#fff;font-family:Georgia,serif;font-style:italic">&part;</span>
          &nbsp;CalcVoyager
        </div>
      </td></tr>
      <tr><td style="padding:12px 28px 0;font-size:15px;line-height:1.6">
        <p style="margin:0 0 12px">{html.escape(greeting)}</p>
        <p style="margin:0 0 18px">Enter this code to verify your email address:</p>
        <div style="font-size:34px;font-weight:800;letter-spacing:10px;text-align:center;padding:14px 0;border-radius:12px;background:#f4f6fb;border:1px dashed #c9d3ea">{html.escape(code)}</div>
        <p style="margin:22px 0 14px">Or verify with one click:</p>
        <p style="margin:0 0 22px;text-align:center">
          <a href="{safe_link}" style="display:inline-block;padding:12px 26px;border-radius:10px;background:#2b4cc9;color:#ffffff;text-decoration:none;font-weight:700">Verify my email</a>
        </p>
        <p style="margin:0 0 24px;color:#5b6785;font-size:13px">
          The code and link expire in {minutes} minutes. If you didn't create a CalcVoyager account, you can ignore this email.
        </p>
      </td></tr>
    </table>
  </td></tr></table>
</body></html>"""
    return subject, text, body


def _send_smtp_port(host: str, port: int, user: str, password: str, msg: EmailMessage, timeout: int = 7) -> None:
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if user:
                server.login(user, password)
            server.send_message(msg)


def _send_smtp(to: str, subject: str, text: str, body_html: str) -> None:
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or 587)
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD").replace(" ", "")  # Gmail shows app passwords with spaces

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _sender()
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")

    ports = [port, 465 if port != 465 else 587]
    last_err = None
    for candidate_port in ports:
        try:
            _send_smtp_port(host, candidate_port, user, password, msg, timeout=6)
            return
        except Exception as exc:
            last_err = exc
    raise last_err


async def _send_resend(to: str, subject: str, text: str, body_html: str) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {_env('RESEND_API_KEY')}"},
            json={
                "from": _sender() or "CalcVoyager <onboarding@resend.dev>",
                "to": [to],
                "subject": subject,
                "text": text,
                "html": body_html,
            },
        )
    if response.status_code >= 300:
        raise RuntimeError(f"Resend error {response.status_code}: {response.text[:200]}")


async def send_email(to: str, subject: str, text: str, body_html: str) -> None:
    """Send one email. Raises on failure."""
    kind = provider()
    if kind == "resend":
        await _send_resend(to, subject, text, body_html)
    elif kind == "smtp":
        await asyncio.to_thread(_send_smtp, to, subject, text, body_html)
    else:
        raise RuntimeError("No email provider is configured.")
