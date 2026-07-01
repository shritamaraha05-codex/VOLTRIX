"""
email_service.py — SMTP email delivery module
Owner: Mrinmoy

Replaces Resend entirely. Uses Python stdlib smtplib + email.mime.
No external dependencies.

Env required:
    SMTP_HOST     e.g. smtp.gmail.com
    SMTP_PORT     587 (STARTTLS) or 465 (SSL)
    SMTP_USER     your@gmail.com
    SMTP_PASSWORD Gmail App Password (16 chars), not account password
    SMTP_FROM     VOLTRIX Alerts <your@gmail.com>
"""

import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("voltrix.email")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", f"VOLTRIX Alerts <{SMTP_USER}>")


def _send(to_email: str, subject: str, html_body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, to_email, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, to_email, msg.as_string())

        logger.info(f"Email sent → {to_email}")
        return True

    except Exception as e:
        logger.error(f"Email failed → {to_email}: {e}")
        return False


def send_nudge_email(
    to_email: str,
    household_name: str,
    nudge_message: str,
    action_suggested: str,
) -> bool:
    subject = "⚡ Energy alert for your zone tonight — VOLTRIX"
    html = f"""
    <div style="font-family:Inter,sans-serif;max-width:520px;margin:auto;background:#0f172a;border-radius:12px;overflow:hidden">
      <div style="background:#1e293b;padding:24px 28px;border-bottom:1px solid #334155">
        <h1 style="color:#2563eb;font-size:20px;margin:0">⚡ VOLTRIX</h1>
        <p style="color:#94a3b8;font-size:12px;margin:4px 0 0">AI-Powered Grid Intelligence</p>
      </div>
      <div style="padding:28px">
        <p style="color:#e2e8f0;font-size:15px;margin:0 0 12px">Hi <strong>{household_name}</strong>,</p>
        <p style="color:#cbd5e1;font-size:14px;line-height:1.6;margin:0 0 20px">{nudge_message}</p>
        <div style="background:#1e3a5f;border-left:3px solid #2563eb;border-radius:6px;padding:14px 16px;margin:0 0 24px">
          <p style="color:#93c5fd;font-size:12px;font-weight:600;margin:0 0 4px;text-transform:uppercase;letter-spacing:0.5px">Suggested Action</p>
          <p style="color:#e2e8f0;font-size:14px;margin:0"><strong>{action_suggested}</strong></p>
        </div>
        <p style="color:#475569;font-size:12px;margin:0">This alert was generated automatically by VOLTRIX based on AI forecasting of your zone's energy demand.</p>
      </div>
      <div style="background:#1e293b;padding:14px 28px;border-top:1px solid #334155">
        <p style="color:#475569;font-size:11px;margin:0">VOLTRIX — AI-Powered Grid Intelligence</p>
      </div>
    </div>
    """
    return _send(to_email, subject, html)


def send_utility_alert(
    to_email: str,
    zone_name: str,
    utility_action: str,
    reasoning: str,
) -> bool:
    subject = f"[VOLTRIX] Grid stress predicted — {zone_name}"
    html = f"""
    <div style="font-family:Inter,sans-serif;max-width:520px;margin:auto;background:#0f172a;border-radius:12px;overflow:hidden">
      <div style="background:#1e293b;padding:24px 28px;border-bottom:1px solid #334155">
        <h1 style="color:#dc2626;font-size:20px;margin:0">⚠ VOLTRIX — Grid Alert</h1>
        <p style="color:#94a3b8;font-size:12px;margin:4px 0 0">Utility Operations Dashboard</p>
      </div>
      <div style="padding:28px">
        <p style="color:#94a3b8;font-size:12px;font-weight:600;margin:0 0 4px;text-transform:uppercase">Zone</p>
        <p style="color:#e2e8f0;font-size:16px;font-weight:600;margin:0 0 20px">{zone_name}</p>
        <p style="color:#94a3b8;font-size:12px;font-weight:600;margin:0 0 4px;text-transform:uppercase">AI Analysis</p>
        <p style="color:#cbd5e1;font-size:14px;line-height:1.6;margin:0 0 20px">{reasoning}</p>
        <div style="background:#1e3a5f;border-left:3px solid #dc2626;border-radius:6px;padding:14px 16px">
          <p style="color:#fca5a5;font-size:12px;font-weight:600;margin:0 0 4px;text-transform:uppercase">Recommended Action</p>
          <p style="color:#e2e8f0;font-size:14px;margin:0">{utility_action}</p>
        </div>
      </div>
      <div style="background:#1e293b;padding:14px 28px;border-top:1px solid #334155">
        <p style="color:#475569;font-size:11px;margin:0">VOLTRIX — AI-Powered Grid Intelligence · Automated Alert</p>
      </div>
    </div>
    """
    return _send(to_email, subject, html)
