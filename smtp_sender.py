"""
Send drafted emails via Gmail SMTP (or any SMTP provider).

Gmail requires an *app password* — not your account password.
Create one at https://myaccount.google.com/apppasswords (needs 2FA on).
"""
from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


def send(
    *,
    from_email: str,
    from_name: str,
    app_password: str,
    to: str,
    subject: str,
    body: str,
    attachments: Iterable[str | Path] = (),
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 465,
) -> None:
    """
    Send a plain-text email with optional attachments.
    Raises on failure (smtplib / SSL exceptions bubble up).
    """
    if not from_email or not app_password:
        raise RuntimeError(
            "Missing SMTP credentials. Open Settings and add your Gmail address "
            "and a Google app password."
        )
    if not to:
        raise RuntimeError("No recipient address.")
    if not subject and not body:
        raise RuntimeError("Email is empty.")

    msg = EmailMessage()
    msg["From"]    = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"]      = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")

    for path in attachments:
        p = Path(path)
        if not p.exists() or not p.is_file():
            continue
        ctype, _ = mimetypes.guess_type(str(p))
        ctype = ctype or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            p.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=p.name,
        )

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as s:
        s.login(from_email, app_password)
        s.send_message(msg)
