"""Playwright-based checker for Monev MagangHub daily attendance & learning logs."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page, Response

from src.config import (
    Config,
    MONEV_BASE_URL,
    MONEV_DASHBOARD_URL,
    MONEV_HOME_API_PATH,
    MONEV_LOGIN_URL,
    build_riwayat_url,
)


class ReportStatus(str, Enum):
    """Status of today's Monev daily report."""

    FILLED = "filled"
    EMPTY = "empty"
    OFF_DAY = "off-day"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking a single day's Monev report."""

    status: ReportStatus
    reason: str
    source: str


class MonevCheckError(RuntimeError):
    """Raised when login, navigation, or status parsing fails."""


def classify_home_payload(payload: dict[str, Any], expected_date: str | None = None) -> CheckResult:
    """Classify status from GET /api/v1/users/me/home response payload."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise MonevCheckError("Unexpected /users/me/home payload format.")

    server_date = data.get("date")
    if expected_date and server_date and server_date != expected_date:
        raise MonevCheckError(
            f"Server date mismatch in /users/me/home: got {server_date!r}, expected {expected_date!r}."
        )

    if data.get("is_holiday") or data.get("is_scheduled_off_day"):
        holiday = data.get("holiday")
        holiday_name = holiday.get("name") if isinstance(holiday, dict) else None
        detail = f"Hari libur ({holiday_name})" if holiday_name else "Hari libur / off-day jadwal"
        return CheckResult(status=ReportStatus.OFF_DAY, reason=detail, source="api:/users/me/home")

    has_attendance = data.get("has_attendance")
    if not isinstance(has_attendance, bool):
        raise MonevCheckError(f"Missing boolean 'has_attendance' in /users/me/home: {data!r}")

    if has_attendance:
        return CheckResult(
            status=ReportStatus.FILLED,
            reason="Laporan dan kehadiran hari ini sudah tercatat.",
            source="api:/users/me/home",
        )

    return CheckResult(
        status=ReportStatus.EMPTY,
        reason="Laporan hari ini belum diisi (has_attendance=false).",
        source="api:/users/me/home",
    )


def classify_dashboard_text(page_text: str) -> CheckResult | None:
    """Classify status from rendered /dashboard home text, or None if inconclusive."""
    normalized = " ".join(page_text.lower().split())
    if "hari ini adalah hari libur" in normalized:
        return CheckResult(
            status=ReportStatus.OFF_DAY,
            reason="Terdeteksi status hari libur pada halaman Beranda.",
            source="dom:/dashboard",
        )
    if "program magang belum dimulai" in normalized or "pengisian laporan dinonaktifkan" in normalized:
        return CheckResult(
            status=ReportStatus.OFF_DAY,
            reason="Pengisian laporan tidak aktif / di luar periode magang.",
            source="dom:/dashboard",
        )
    if "laporan hari ini sudah diisi" in normalized:
        return CheckResult(
            status=ReportStatus.FILLED,
            reason="Terdeteksi teks 'Laporan hari ini sudah diisi' pada halaman Beranda.",
            source="dom:/dashboard",
        )
    if "laporan hari ini belum diisi" in normalized:
        return CheckResult(
            status=ReportStatus.EMPTY,
            reason="Terdeteksi teks 'Laporan hari ini belum diisi' pada halaman Beranda.",
            source="dom:/dashboard",
        )
    return None


_INDONESIAN_MONTHS = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}


def format_indonesian_date(date_str: str) -> str:
    """Format YYYY-MM-DD into '5 November 2026' matching Monev detail headings."""
    year, month, day = (int(part) for part in date_str.split("-"))
    return f"{day} {_INDONESIAN_MONTHS[month]} {year}"


def classify_riwayat_detail_text(page_text: str, date_str: str | None = None) -> CheckResult | None:
    """Classify status from rendered /dashboard/riwayat?date=...&view=detail text."""
    normalized = " ".join(page_text.lower().split())

    if date_str:
        date_heading = format_indonesian_date(date_str).lower()
        idx = normalized.find(date_heading)
        if idx == -1:
            return None
        detail_section = normalized[idx:]
    else:
        detail_section = normalized

    if "kehadiran dan laporan harian tidak perlu diisi" in detail_section:
        return CheckResult(
            status=ReportStatus.OFF_DAY,
            reason="Terdeteksi hari libur nasional / jadwal libur posisi pada detail riwayat.",
            source="dom:/dashboard/riwayat",
        )

    # Empty dates auto-open the "Tambah laporan" form which also includes "Uraian aktivitas" labels
    if "tambah laporan" in normalized or "minimal 100 karakter" in detail_section:
        return CheckResult(
            status=ReportStatus.EMPTY,
            reason="Form 'Tambah laporan' terbuka (laporan tanggal ini belum diisi).",
            source="dom:/dashboard/riwayat",
        )

    if "status laporan belum diisi" in detail_section:
        return CheckResult(
            status=ReportStatus.EMPTY,
            reason="Terdeteksi 'Status laporan Belum diisi' pada detail riwayat.",
            source="dom:/dashboard/riwayat",
        )

    if "uraian aktivitas" in detail_section and "pembelajaran yang diperoleh" in detail_section:
        return CheckResult(
            status=ReportStatus.FILLED,
            reason="Detail 'Uraian Aktivitas' & 'Pembelajaran yang Diperoleh' sudah terisi.",
            source="dom:/dashboard/riwayat",
        )

    leave_or_absent_markers = (
        "tidak hadir dengan keterangan",
        "tidak hadir tanpa keterangan",
    )
    if "alasan" in detail_section and any(marker in detail_section for marker in leave_or_absent_markers):
        return CheckResult(
            status=ReportStatus.FILLED,
            reason="Detail ketidakhadiran/izin sudah tercatat pada riwayat.",
            source="dom:/dashboard/riwayat",
        )

    return None


def parse_storage_state(raw: str) -> dict[str, Any]:
    """Parse MONEV_STORAGE_STATE from raw JSON or base64-encoded JSON."""
    text = raw.strip()
    if not text:
        raise MonevCheckError("MONEV_STORAGE_STATE is empty.")

    candidates = [text]
    if not text.startswith("{"):
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
            candidates.insert(0, decoded)
        except Exception:
            pass

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and ("cookies" in parsed or "origins" in parsed):
                return parsed
        except Exception:
            continue

    raise MonevCheckError("Format MONEV_STORAGE_STATE tidak valid (harus JSON Playwright storage_state).")


def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 15000) -> Locator:
    """Return the first visible element matching any selector in order."""
    combined = ", ".join(selectors)
    locator = page.locator(combined).first
    locator.wait_for(state="visible", timeout=timeout_ms)
    return locator


def _try_restore_saved_session(page: Page) -> bool:
    """Try opening /dashboard with saved cookies, refreshing and waiting ~10s if needed."""
    page.goto(MONEV_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45000)

    for attempt in range(2):
        try:
            page.wait_for_function(
                """() => {
                    const href = window.location.href;
                    const text = (document.body?.innerText || '').toLowerCase();
                    if (!href.startsWith('https://monev.maganghub.kemnaker.go.id/dashboard')) {
                        return false;
                    }
                    return !document.querySelector('#__nuxt-loader') && !text.includes('sedang memuat');
                }""",
                timeout=10000,
            )
            if page.url.startswith(MONEV_DASHBOARD_URL):
                return True
        except Exception:
            pass

        if attempt == 0:
            # Monev sometimes needs a reload + ~10s wait to restore session from HttpOnly cookie
            if "account.kemnaker.go.id" in page.url:
                page.goto(MONEV_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45000)
            else:
                page.reload(wait_until="domcontentloaded", timeout=45000)

    return page.url.startswith(MONEV_DASHBOARD_URL)


def _perform_sso_login(page: Page, email: str, password: str) -> None:
    """Complete SIAPkerja SSO login flow starting from Monev /login."""
    page.goto(MONEV_LOGIN_URL, wait_until="domcontentloaded", timeout=45000)

    # Wait until either redirected back to dashboard (if session already active) or SSO host
    page.wait_for_url(
        lambda url: url.startswith(MONEV_DASHBOARD_URL) or url.startswith("https://account.kemnaker.go.id"),
        timeout=45000,
    )

    if page.url.startswith(MONEV_DASHBOARD_URL):
        return

    body_lower = " ".join(page.inner_text("body").lower().split()) if page.locator("body").count() else ""
    if "verifikasi keamanan" in body_lower or "verifikasi bahwa anda adalah manusia" in body_lower:
        raise MonevCheckError(
            "Halaman login account.kemnaker.go.id diblokir oleh verifikasi keamanan Cloudflare. "
            "Perbarui secret MONEV_STORAGE_STATE dari sesi login lokal yang masih aktif."
        )

    email_input = _first_visible(
        page,
        [
            'input[placeholder*="Masukkan email" i]',
            'input[type="email"]',
            'input[name="email"]',
            'input[name="username"]',
            'input[name="identity"]',
            'input[id*="email" i]',
            'input[placeholder*="email" i]',
            'input[type="text"]',
        ],
        timeout_ms=30000,
    )
    password_input = _first_visible(page, ['input[type="password"]'], timeout_ms=15000)

    email_input.fill(email)
    password_input.fill(password)

    submit_btn = _first_visible(
        page,
        [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Masuk")',
            'button:has-text("Login")',
        ],
        timeout_ms=15000,
    )
    submit_btn.click()

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        page.wait_for_url(lambda url: url.startswith(MONEV_DASHBOARD_URL), timeout=45000)
    except PlaywrightTimeoutError as exc:
        current_url = page.url
        snippet = " ".join(page.inner_text("body").split())[:400] if page.locator("body").count() else ""
        raise MonevCheckError(
            f"Login gagal atau tidak redirect ke dashboard Monev. URL terakhir: {current_url}. "
            f"Cuplikan halaman: {snippet}"
        ) from exc


def _wait_dashboard_ready(page: Page) -> None:
    """Wait until the dashboard finishes syncing today's report status."""
    try:
        page.wait_for_function(
            """() => {
                const text = (document.body?.innerText || '').toLowerCase();
                return !text.includes('mengecek laporan hari ini') && !document.querySelector('#__nuxt-loader');
            }""",
            timeout=20000,
        )
    except Exception:
        page.wait_for_load_state("networkidle", timeout=15000)


def _save_debug_screenshot(page: Page, artifacts_dir: Path, date_str: str, tag: str) -> Path | None:
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = artifacts_dir / f"{tag}-{date_str}.png"
        page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:
        return None


def check_today_report(config: Config, date_str: str) -> CheckResult:
    """Log into Monev MagangHub via Playwright and check today's report status."""
    from playwright.sync_api import sync_playwright

    home_payload: dict[str, Any] | None = None
    storage_state_path = config.artifacts_dir / "storage_state.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.headless)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 900},
            "locale": "id-ID",
            "timezone_id": "Asia/Jakarta",
        }
        has_saved_session = False
        if config.monev_storage_state:
            context_kwargs["storage_state"] = parse_storage_state(config.monev_storage_state)
            has_saved_session = True
        elif storage_state_path.is_file():
            context_kwargs["storage_state"] = str(storage_state_path)
            has_saved_session = True

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        def handle_response(response: Response) -> None:
            nonlocal home_payload
            if MONEV_HOME_API_PATH in response.url and response.request.method == "GET":
                if response.status == 200:
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            home_payload = data
                    except Exception:
                        pass

        page.on("response", handle_response)

        try:
            session_restored = _try_restore_saved_session(page) if has_saved_session else False
            if not session_restored:
                _perform_sso_login(page, config.monev_email, config.monev_password)
            _wait_dashboard_ready(page)

            config.artifacts_dir.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_state_path))

            is_different_target_date = False
            if home_payload is not None:
                data = home_payload.get("data") if isinstance(home_payload.get("data"), dict) else home_payload
                server_date = data.get("date") if isinstance(data, dict) else None
                if server_date and server_date != date_str:
                    is_different_target_date = True
                else:
                    try:
                        return classify_home_payload(home_payload, expected_date=date_str)
                    except MonevCheckError:
                        pass

            # Only read /dashboard DOM when checking the server's current date (not a past --date override)
            if not is_different_target_date:
                dashboard_text = page.inner_text("body")
                dom_result = classify_dashboard_text(dashboard_text)
                if dom_result is not None:
                    return dom_result

            # Secondary verification directly on the requested date's riwayat detail URL
            riwayat_url = build_riwayat_url(date_str)
            page.goto(riwayat_url, wait_until="domcontentloaded", timeout=45000)

            expected_heading = format_indonesian_date(date_str)
            try:
                page.wait_for_function(
                    """() => {
                        const text = (document.body?.innerText || '').toLowerCase();
                        return text.includes('riwayat kehadiran') && !text.includes('memuat kalender');
                    }""",
                    timeout=20000,
                )
            except Exception:
                page.wait_for_load_state("networkidle", timeout=15000)

            if not page.url.startswith(MONEV_BASE_URL + "/dashboard"):
                raise MonevCheckError(f"Sesi tidak valid saat membuka riwayat. URL: {page.url}")

            # Some dates (such as holidays/weekends) only render the detail panel after clicking the day button
            if expected_heading not in page.inner_text("body"):
                day_btn = page.locator(f'button.calendar-day-button[aria-label*="{expected_heading}"]').first
                if day_btn.count() > 0:
                    aria_label = (day_btn.get_attribute("aria-label") or "").lower()
                    if not day_btn.is_disabled():
                        day_btn.click()
                        try:
                            page.wait_for_function(
                                """(heading) => (document.body?.innerText || '').includes(heading)""",
                                arg=expected_heading,
                                timeout=5000,
                            )
                        except Exception:
                            pass
                    elif "belum diisi" in aria_label:
                        return CheckResult(
                            status=ReportStatus.EMPTY,
                            reason=f"Status kalender untuk {expected_heading} adalah Belum Diisi.",
                            source="dom:/dashboard/riwayat",
                        )

            riwayat_text = page.inner_text("body")
            riwayat_result = classify_riwayat_detail_text(riwayat_text, date_str=date_str)
            if riwayat_result is not None:
                return riwayat_result

            raise MonevCheckError(
                f"Tidak dapat menentukan status laporan untuk {date_str} dari API maupun detail riwayat."
            )
        except MonevCheckError:
            _save_debug_screenshot(page, config.artifacts_dir, date_str, "error")
            raise
        except Exception as exc:
            _save_debug_screenshot(page, config.artifacts_dir, date_str, "error")
            raise MonevCheckError(f"Unexpected error: {exc}") from exc
        finally:
            context.close()
            browser.close()
