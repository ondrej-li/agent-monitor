"""Email delivery: SMTP or local submission on the mail host over SSH."""

from __future__ import annotations

import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from . import render


class DeliveryError(RuntimeError):
    """Raised when a message cannot be handed off."""


def build_message(cfg, subject: str, text_body: str, html_body: str) -> EmailMessage:
    sender = cfg.require("email.from")
    recipients = cfg.get("email.to") or []
    if not recipients:
        raise DeliveryError("email.to is empty")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="agent-monitor.local")
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _send_sendmail_ssh(cfg, message: EmailMessage) -> None:
    host = cfg.require("email.ssh_host")
    sender = parseaddr(cfg.require("email.from"))[1]
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        host,
        "/usr/sbin/sendmail",
        "-f",
        sender,
        "-t",
    ]
    try:
        proc = subprocess.run(
            command,
            input=message.as_bytes(),
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeliveryError(f"ssh sendmail failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise DeliveryError(f"ssh sendmail exited {proc.returncode}: {detail[:300]}")


def _send_smtp(cfg, message: EmailMessage) -> None:
    host = cfg.require("email.smtp_host")
    port = int(cfg.get("email.smtp_port", 465))
    security = str(cfg.get("email.smtp_security", "ssl")).lower()
    user = cfg.get("email.smtp_user") or ""
    password = cfg.secret(cfg.get("email.smtp_password_secret") or "AGENT_MONITOR_SMTP_PASSWORD")
    if user and not password:
        raise DeliveryError("SMTP password not found (env, env file, or keychain)")
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
        try:
            if user:
                server.login(user, password)
            server.send_message(message)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                server.close()
    except (smtplib.SMTPException, OSError) as exc:
        raise DeliveryError(f"smtp delivery failed: {exc}") from exc


def send(cfg, message: EmailMessage) -> str:
    transport = str(cfg.get("email.transport", "sendmail_ssh")).lower()
    if transport == "sendmail_ssh":
        _send_sendmail_ssh(cfg, message)
    elif transport == "smtp":
        _send_smtp(cfg, message)
    else:
        raise DeliveryError(f"unknown email transport: {transport}")
    return transport


def send_digest(cfg, digest: dict, meta: dict) -> dict:
    subject = render.email_subject(digest, meta)
    message = build_message(
        cfg,
        subject,
        render.email_text(digest, meta),
        render.email_html(digest, meta),
    )
    transport = send(cfg, message)
    return {"subject": subject, "transport": transport, "bytes": len(message.as_bytes())}


def send_test(cfg) -> dict:
    subject = "agent-monitor: delivery test"
    text = (
        "This is a delivery test from agent-monitor.\n\n"
        f"Transport: {cfg.get('email.transport')}\n"
        f"From: {cfg.get('email.from')}\n"
        f"To: {', '.join(cfg.get('email.to') or [])}\n"
    )
    html = (
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        "max-width:600px;margin:0 auto;padding:24px\">"
        "<h1 style=\"font-size:20px;margin:0 0 8px\">Delivery test</h1>"
        "<p style=\"color:#475569\">If you can read this, agent-monitor can reach your inbox.</p>"
        f"<p style=\"color:#64748b;font-size:13px\">Transport: {render.esc(cfg.get('email.transport'))}"
        f"<br>From: {render.esc(cfg.get('email.from'))}"
        f"<br>To: {render.esc(', '.join(cfg.get('email.to') or []))}</p></div>"
    )
    message = build_message(cfg, subject, text, html)
    transport = send(cfg, message)
    return {"subject": subject, "transport": transport}
