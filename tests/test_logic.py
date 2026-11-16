"""Unit tests for Monev checker classification and CLI logic."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src.config import Config, DEFAULT_RESEND_FROM, build_riwayat_url
from src.main import is_workday, main
from src.monev_checker import (
    MonevCheckError,
    ReportStatus,
    classify_dashboard_text,
    classify_home_payload,
    classify_riwayat_detail_text,
)
from src.notifier import send_email


class TestMonevLogic(unittest.TestCase):
    def test_build_riwayat_url(self) -> None:
        self.assertEqual(
            build_riwayat_url("2026-11-16"),
            "https://monev.maganghub.kemnaker.go.id/dashboard/riwayat?date=2026-11-16&view=detail",
        )

    def test_is_workday(self) -> None:
        for day in range(5):
            self.assertTrue(is_workday(day))
        self.assertFalse(is_workday(5))
        self.assertFalse(is_workday(6))

    def test_classify_home_payload_filled(self) -> None:
        payload = {"data": {"date": "2026-11-16", "has_attendance": True, "is_holiday": False}}
        res = classify_home_payload(payload, expected_date="2026-11-16")
        self.assertEqual(res.status, ReportStatus.FILLED)

    def test_classify_home_payload_empty(self) -> None:
        payload = {"data": {"date": "2026-11-16", "has_attendance": False, "is_holiday": False}}
        res = classify_home_payload(payload, expected_date="2026-11-16")
        self.assertEqual(res.status, ReportStatus.EMPTY)

    def test_classify_home_payload_holiday_or_off_day(self) -> None:
        payload = {
            "data": {
                "date": "2026-11-16",
                "has_attendance": False,
                "is_holiday": True,
                "holiday": {"name": "Libur Nasional"},
            }
        }
        res = classify_home_payload(payload, expected_date="2026-11-16")
        self.assertEqual(res.status, ReportStatus.OFF_DAY)
        self.assertIn("Libur Nasional", res.reason)

        off_day = {"data": {"date": "2026-11-16", "has_attendance": False, "is_scheduled_off_day": True}}
        self.assertEqual(classify_home_payload(off_day).status, ReportStatus.OFF_DAY)

    def test_classify_home_payload_date_mismatch_raises(self) -> None:
        payload = {"data": {"date": "2026-11-15", "has_attendance": True}}
        with self.assertRaises(MonevCheckError):
            classify_home_payload(payload, expected_date="2026-11-16")

    def test_classify_dashboard_text(self) -> None:
        self.assertEqual(classify_dashboard_text("Laporan hari ini sudah diisi").status, ReportStatus.FILLED)
        self.assertEqual(classify_dashboard_text("Laporan hari ini belum diisi").status, ReportStatus.EMPTY)
        self.assertEqual(classify_dashboard_text("Hari ini adalah hari libur").status, ReportStatus.OFF_DAY)
        self.assertEqual(classify_dashboard_text("Program magang belum dimulai").status, ReportStatus.OFF_DAY)
        self.assertEqual(classify_dashboard_text("Pengisian laporan dinonaktifkan").status, ReportStatus.OFF_DAY)
        self.assertIsNone(classify_dashboard_text("Sedang memuat..."))

    def test_classify_riwayat_detail_text_ignores_legend_only(self) -> None:
        legend_only = (
            "Riwayat Kehadiran Hadir disetujui Izin disetujui Tidak Hadir "
            "Menunggu Tindakan Mentor Belum Diisi Hari Libur"
        )
        self.assertIsNone(classify_riwayat_detail_text(legend_only))

        filled = (
            f"{legend_only} 16 November 2026 Menunggu Persetujuan "
            "Kehadiran Hadir Uraian Aktivitas rerun job Pembelajaran yang Diperoleh debug mysql"
        )
        self.assertEqual(classify_riwayat_detail_text(filled).status, ReportStatus.FILLED)

    def test_classify_riwayat_detail_text_holiday_and_leave(self) -> None:
        holiday = (
            "15 November 2026 Hari Libur Hari libur Jadwal libur posisi (Minggu) "
            "Sumber Jadwal libur posisi Pengisian Kehadiran dan laporan harian tidak perlu diisi."
        )
        self.assertEqual(classify_riwayat_detail_text(holiday).status, ReportStatus.OFF_DAY)

        leave = (
            "12 November 2026 Izin disetujui Kehadiran Tidak hadir dengan keterangan "
            "Alasan Sakit demam"
        )
        self.assertEqual(classify_riwayat_detail_text(leave).status, ReportStatus.FILLED)

        empty_form = (
            "Tambah laporan · 5 November 2026 Kehadiran Uraian aktivitas Minimal 100 karakter "
            "Pembelajaran yang diperoleh Minimal 100 karakter"
        )
        self.assertEqual(classify_riwayat_detail_text(empty_form, date_str="2026-11-05").status, ReportStatus.EMPTY)

    def test_config_from_env_missing_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.config.load_dotenv"):
                with self.assertRaises(ValueError):
                    Config.from_env()

    def test_config_from_env_with_resend(self) -> None:
        env = {
            "MONEV_EMAIL": "a@b.com",
            "MONEV_PASSWORD": "secret",
            "RESEND_API_KEY": "re_test_123",
            "NOTIFY_EMAIL": "dest@gmail.com",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("src.config.load_dotenv"):
                cfg = Config.from_env()
                self.assertTrue(cfg.use_resend)
                self.assertEqual(cfg.resend_from, DEFAULT_RESEND_FROM)

    def test_send_email_via_resend_posts_json(self) -> None:
        cfg = Config(
            monev_email="a@b.com",
            monev_password="secret",
            notify_email="dest@gmail.com",
            resend_api_key="re_test_123",
        )
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("src.notifier.urllib.request.urlopen", return_value=mock_resp) as mock_open:
            send_email(cfg, "Subjek Tes", "Isi pesan")
            req = mock_open.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.resend.com/emails")
            self.assertEqual(req.get_header("Authorization"), "Bearer re_test_123")
            payload = json.loads(req.data.decode("utf-8"))
            self.assertEqual(payload["to"], ["dest@gmail.com"])
            self.assertEqual(payload["subject"], "Subjek Tes")
            self.assertEqual(payload["from"], DEFAULT_RESEND_FROM)

    def test_main_sends_email_only_when_empty(self) -> None:
        env = {
            "MONEV_EMAIL": "a@b.com",
            "MONEV_PASSWORD": "secret",
            "SMTP_USERNAME": "sender@gmail.com",
            "SMTP_PASSWORD": "app-password",
            "NOTIFY_EMAIL": "dest@gmail.com",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("src.config.load_dotenv"):
                with patch("src.main.check_today_report") as mock_check, patch(
                    "src.main.send_unfilled_reminder"
                ) as mock_remind:
                    mock_check.return_value = classify_home_payload(
                        {"data": {"date": "2026-11-16", "has_attendance": True}}
                    )
                    code = main(["--date", "2026-11-16", "--force"])
                    self.assertEqual(code, 0)
                    mock_remind.assert_not_called()

                    mock_check.return_value = classify_home_payload(
                        {"data": {"date": "2026-11-16", "has_attendance": False}}
                    )
                    code = main(["--date", "2026-11-16", "--force"])
                    self.assertEqual(code, 0)
                    mock_remind.assert_called_once()

    def test_main_sends_error_alert_on_exception(self) -> None:
        env = {
            "MONEV_EMAIL": "a@b.com",
            "MONEV_PASSWORD": "secret",
            "SMTP_USERNAME": "sender@gmail.com",
            "SMTP_PASSWORD": "app-password",
            "NOTIFY_EMAIL": "dest@gmail.com",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("src.config.load_dotenv"):
                with patch("src.main.check_today_report", side_effect=MonevCheckError("Login gagal")), patch(
                    "src.main.send_error_alert"
                ) as mock_err:
                    code = main(["--date", "2026-11-16", "--force"])
                    self.assertEqual(code, 1)
                    mock_err.assert_called_once()


if __name__ == "__main__":
    unittest.main()
