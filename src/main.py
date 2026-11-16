"""CLI entrypoint for the Monev daily report reminder bot."""

from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import replace

from src.config import Config, now_wib
from src.monev_checker import ReportStatus, check_today_report
from src.notifier import send_error_alert, send_unfilled_reminder


def is_workday(weekday: int) -> bool:
    """Return True for Monday (0) through Friday (4)."""
    return weekday < 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Monev MagangHub daily report status.")
    parser.add_argument(
        "--date",
        help="Override target date in YYYY-MM-DD (default: today in WIB).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run check even on weekends (Saturday/Sunday).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send real emails; print what would be sent.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    current_wib = now_wib()
    date_str = args.date or current_wib.strftime("%Y-%m-%d")
    checked_at_wib = current_wib.strftime("%H:%M")

    if not args.force and not args.date and not is_workday(current_wib.weekday()):
        print(f"[{date_str} {checked_at_wib} WIB] Hari libur akhir pekan, pengecekan dilewati.")
        return 0

    try:
        config = Config.from_env()
    except Exception as exc:
        print(f"[CONFIG ERROR] {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        config = replace(config, dry_run=True)

    try:
        result = check_today_report(config, date_str)
        print(
            f"[{date_str} {checked_at_wib} WIB] Status={result.status.value} "
            f"({result.source}): {result.reason}"
        )

        if result.status == ReportStatus.EMPTY:
            send_unfilled_reminder(config, date_str, checked_at_wib)
            print(f"[{date_str} {checked_at_wib} WIB] Email pengingat terkirim ke {config.notify_email}.")
        else:
            print(f"[{date_str} {checked_at_wib} WIB] Tidak ada email yang perlu dikirim.")
        return 0
    except Exception as exc:
        error_detail = f"{exc}\n\n{traceback.format_exc(limit=5)}"
        print(f"[ERROR] {error_detail}", file=sys.stderr)
        try:
            send_error_alert(config, date_str, checked_at_wib, str(exc))
            print(f"[{date_str} {checked_at_wib} WIB] Email alert error terkirim ke {config.notify_email}.")
        except Exception as mail_exc:
            print(f"[EMAIL ERROR] Gagal mengirim email alert error: {mail_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
