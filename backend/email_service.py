"""
email_service.py — SMTP email delivery for VOLTRIX
Uses Python stdlib smtplib only — no external library.

Two public functions:
  send_nudge_email(to_email, household_name, nudge_message, action_suggested) -> bool
  send_utility_alert(to_email, zone_name, utility_action, reasoning) -> bool

Both return True on success, False on failure. Never raise.
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

DARK_BG = "#0f172a"
CARD_BG = "#1e293b"
BORDER = "#334155"
TEXT_PRIMARY = "#e2e8f0"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#475569"
ACCENT_BLUE = "#2563eb"
ACCENT_BLUE_BG = "#1e3a5f"
ACCENT_BLUE_LT = "#93c5fd"
ACCENT_RED = "#dc2626"
ACCENT_RED_LT = "#fca5a5"


def _send(to_email: str, subject: str, html_body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_FROM, to_email, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.ehlo()
                s.starttls()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_FROM, to_email, msg.as_string())

        logger.info(f"Email sent -> {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email failed -> {to_email}: {e}")
        return False


def send_nudge_email(to_email, household_name, nudge_message, action_suggested) -> bool:
    subject = "\u26a1 Energy alert for your zone tonight \u2014 VOLTRIX"
    html = f"""
    <div style="font-family:Inter,sans-serif;max-width:520px;margin:auto;
                background:{DARK_BG};border-radius:12px;overflow:hidden;
                border:1px solid {BORDER}">
      <div style="background:{CARD_BG};padding:24px 28px;border-bottom:1px solid {BORDER}">
        <h1 style="color:{ACCENT_BLUE};font-size:20px;margin:0;letter-spacing:-0.5px">\u26a1 VOLTRIX</h1>
        <p style="color:{TEXT_SECONDARY};font-size:12px;margin:4px 0 0">AI-Powered Grid Intelligence</p>
      </div>
      <div style="padding:28px">
        <p style="color:{TEXT_PRIMARY};font-size:15px;margin:0 0 12px">
          Hi <strong>{household_name}</strong>,
        </p>
        <p style="color:#cbd5e1;font-size:14px;line-height:1.7;margin:0 0 20px">
          {nudge_message}
        </p>
        <div style="background:{ACCENT_BLUE_BG};border-left:3px solid {ACCENT_BLUE};
                    border-radius:6px;padding:14px 16px;margin:0 0 24px">
          <p style="color:{ACCENT_BLUE_LT};font-size:11px;font-weight:600;margin:0 0 6px;
                    text-transform:uppercase;letter-spacing:0.8px">
            Suggested Action
          </p>
          <p style="color:{TEXT_PRIMARY};font-size:14px;margin:0">
            <strong>{action_suggested}</strong>
          </p>
        </div>
        <p style="color:{TEXT_MUTED};font-size:12px;line-height:1.5;margin:0">
          This alert was generated automatically by VOLTRIX based on AI forecasting
          of your zone\u2019s energy demand. Small actions from each household prevent
          wider grid disruptions.
        </p>
      </div>
      <div style="background:{CARD_BG};padding:14px 28px;border-top:1px solid {BORDER}">
        <p style="color:{TEXT_MUTED};font-size:11px;margin:0">
          VOLTRIX \u2014 AI-Powered Grid Intelligence \u00b7 Automated Citizen Alert
        </p>
      </div>
    </div>
    """
    return _send(to_email, subject, html)


def send_utility_alert(to_email, zone_name, utility_action, reasoning) -> bool:
    subject = f"[VOLTRIX] Grid stress predicted \u2014 {zone_name}"
    html = f"""
    <div style="font-family:Inter,sans-serif;max-width:520px;margin:auto;
                background:{DARK_BG};border-radius:12px;overflow:hidden;
                border:1px solid {BORDER}">
      <div style="background:{CARD_BG};padding:24px 28px;border-bottom:1px solid {BORDER}">
        <h1 style="color:{ACCENT_RED};font-size:20px;margin:0">\u26a0 VOLTRIX \u2014 Grid Alert</h1>
        <p style="color:{TEXT_SECONDARY};font-size:12px;margin:4px 0 0">Utility Operations</p>
      </div>
      <div style="padding:28px">
        <p style="color:{TEXT_SECONDARY};font-size:11px;font-weight:600;margin:0 0 4px;
                  text-transform:uppercase;letter-spacing:0.8px">Affected Zone</p>
        <p style="color:{TEXT_PRIMARY};font-size:17px;font-weight:600;margin:0 0 20px">
          {zone_name}
        </p>
        <p style="color:{TEXT_SECONDARY};font-size:11px;font-weight:600;margin:0 0 4px;
                  text-transform:uppercase;letter-spacing:0.8px">AI Analysis</p>
        <p style="color:#cbd5e1;font-size:14px;line-height:1.7;margin:0 0 20px">
          {reasoning}
        </p>
        <div style="background:#2d1515;border-left:3px solid {ACCENT_RED};
                    border-radius:6px;padding:14px 16px">
          <p style="color:{ACCENT_RED_LT};font-size:11px;font-weight:600;margin:0 0 6px;
                    text-transform:uppercase;letter-spacing:0.8px">Recommended Action</p>
          <p style="color:{TEXT_PRIMARY};font-size:14px;margin:0">{utility_action}</p>
        </div>
      </div>
      <div style="background:{CARD_BG};padding:14px 28px;border-top:1px solid {BORDER}">
        <p style="color:{TEXT_MUTED};font-size:11px;margin:0">
          VOLTRIX \u2014 AI-Powered Grid Intelligence \u00b7 Automated Utility Alert
        </p>
      </div>
    </div>
    """
    return _send(to_email, subject, html)
