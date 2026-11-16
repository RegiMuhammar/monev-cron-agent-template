"""Configuration and timezone helpers for the Monev reminder bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # Optional when env vars are injected directly (e.g. GitHub Actions)

    def load_dotenv() -> bool:
        return False

WIB = timezone(timedelta(hours=7), name="WIB")

MONEV_BASE_URL = "https://monev.maganghub.kemnaker.go.id"
MONEV_LOGIN_URL = f"{MONEV_BASE_URL}/login"
MONEV_DASHBOARD_URL = f"{MONEV_BASE_URL}/dashboard"
MONEV_HOME_API_PATH = "/api/v1/users/me/home"
DEFAULT_RESEND_FROM = "Monev Bot <onboarding@resend.dev>"


def build_riwayat_url(date_str: str) -> str:
    """Return the daily history detail URL for a YYYY-MM-DD date."""
    return f"{MONEV_BASE_URL}/dashboard/riwayat?date={date_str}&view=detail"


def now_wib() -> datetime:
    """Return current datetime in Asia/Jakarta (UTC+7)."""
    return datetime.now(WIB)


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Runtime settings loaded from environment variables."""

    monev_email: str
    monev_password: str
    notify_email: str
    monev_storage_state: str = ""
    resend_api_key: str = ""
    resend_from: str = DEFAULT_RESEND_FROM
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    headless: bool = True
    dry_run: bool = False
    artifacts_dir: Path = Path("artifacts")

    @property
    def use_resend(self) -> bool:
        """Return True when Resend API key is configured."""
        return bool(self.resend_api_key)

    @classmethod
    def from_env(cls) -> Config:
        """Load and validate required environment variables."""
        load_dotenv()

        required_base = ["MONEV_EMAIL", "MONEV_PASSWORD", "NOTIFY_EMAIL"]
        missing = [key for key in required_base if not os.getenv(key, "").strip()]

        resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
        smtp_username = os.getenv("SMTP_USERNAME", "").strip()
        smtp_password = os.getenv("SMTP_PASSWORD", "")

        if not resend_api_key and (not smtp_username or not smtp_password.strip()):
            missing.append("RESEND_API_KEY (or SMTP_USERNAME + SMTP_PASSWORD)")

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        port_raw = os.getenv("SMTP_PORT", "465").strip() or "465"
        try:
            smtp_port = int(port_raw)
        except ValueError as exc:
            raise ValueError(f"Invalid SMTP_PORT value: {port_raw!r}") from exc

        return cls(
            monev_email=os.environ["MONEV_EMAIL"].strip(),
            monev_password=os.environ["MONEV_PASSWORD"],
            notify_email=os.environ["NOTIFY_EMAIL"].strip(),
            monev_storage_state=os.getenv("MONEV_STORAGE_STATE", "").strip(),
            resend_api_key=resend_api_key,
            resend_from=os.getenv("RESEND_FROM", DEFAULT_RESEND_FROM).strip() or DEFAULT_RESEND_FROM,
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com",
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            headless=_parse_bool(os.getenv("HEADLESS"), default=True),
            dry_run=_parse_bool(os.getenv("DRY_RUN"), default=False),
        )
