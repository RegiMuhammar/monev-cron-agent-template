"""Email notification helpers using Resend API (preferred) or SMTP fallback."""

from __future__ import annotations

import json
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from src.config import Config, build_riwayat_url

RESEND_EMAILS_URL = "https://api.resend.com/emails"


def _send_via_resend(config: Config, subject: str, body: str) -> None:
    payload = json.dumps(
        {
            "from": config.resend_from,
            "to": [config.notify_email],
            "subject": subject,
            "text": body,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        RESEND_EMAILS_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "monev-cron-agent/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 400:
                detail = response.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Resend API error {response.status}: {detail}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Resend API HTTP {exc.code}: {detail}") from exc


def _send_via_smtp(config: Config, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.smtp_username
    msg["To"] = config.notify_email
    msg.set_content(body)

    if config.smtp_port == 465:
        with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30) as server:
            server.login(config.smtp_username, config.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.smtp_username, config.smtp_password)
            server.send_message(msg)


def send_email(config: Config, subject: str, body: str) -> None:
    """Send a plain-text email via Resend API (if configured) or SMTP."""
    if config.dry_run:
        provider = "Resend" if config.use_resend else "SMTP"
        print(
            f"[DRY_RUN] Email ({provider}) suppressed.\n"
            f"To: {config.notify_email}\nSubject: {subject}\n{body}"
        )
        return

    if config.use_resend:
        _send_via_resend(config, subject, body)
    else:
        _send_via_smtp(config, subject, body)


def send_unfilled_reminder(config: Config, date_str: str, checked_at_wib: str) -> None:
    """Send reminder email when today's Monev daily report is still empty."""
    riwayat_url = build_riwayat_url(date_str)
    subject = f"[Monev Reminder] Laporan Harian {date_str} Belum Diisi (Cek {checked_at_wib} WIB)"
    body = (
        f"Halo,\n\n"
        f"Bot pengecekan Monev MagangHub mendeteksi laporan harian kamu untuk tanggal "
        f"{date_str} BELUM DIISI (dicek pada {checked_at_wib} WIB).\n\n"
        f"Segera isi Kehadiran, Uraian Aktivitas, dan Pembelajaran di tautan berikut:\n"
        f"{riwayat_url}\n\n"
        f"Catatan: Jika kamu sudah mengisinya setelah email ini terkirim, "
        f"pengingat berikutnya tidak akan dikirim setelah status terisi.\n"
    )
    send_email(config, subject, body)


def send_error_alert(config: Config, date_str: str, checked_at_wib: str, error_detail: str) -> None:
    """Send alert email when the bot encounters an error while checking Monev."""
    riwayat_url = build_riwayat_url(date_str)
    subject = f"[Monev Bot ERROR] Gagal Mengecek Laporan {date_str} ({checked_at_wib} WIB)"
    body = (
        f"Halo,\n\n"
        f"Bot Monev MagangHub mengalami error saat mencoba mengecek laporan tanggal "
        f"{date_str} pada {checked_at_wib} WIB.\n\n"
        f"Detail error:\n{error_detail}\n\n"
        f"Silakan cek manual halaman Monev kamu agar tidak terlewat:\n"
        f"{riwayat_url}\n\n"
        f"Jika berjalan di GitHub Actions, periksa log workflow dan artifact screenshot "
        f"untuk diagnosa lebih lanjut.\n"
    )
    send_email(config, subject, body)
