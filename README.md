# Monev MagangHub Cron Reminder Bot

Bot Python + Playwright untuk mengecek apakah laporan harian di `monev.maganghub.kemnaker.go.id` sudah diisi atau belum.
Berjalan otomatis di cloud menggunakan **GitHub Actions** dan penjadwalan **`cron-job.org`** (laptop tidak perlu hidup) 3x sehari pada hari kerja (**16:00, 18:00, dan 22:00 WIB**).

## Cara Kerja Bot

1. Dipicu tepat waktu oleh `cron-job.org` via GitHub API (`workflow_dispatch`) setiap Senin–Jumat pukul `16:00`, `18:00`, dan `22:00` WIB.
2. Login ke `monev.maganghub.kemnaker.go.id` melalui portal SSO SIAPkerja (`account.kemnaker.go.id`).
3. Mengecek status hari ini dari endpoint `/api/v1/users/me/home` serta cross-check ke halaman `/dashboard` dan `/dashboard/riwayat?date=YYYY-MM-DD&view=detail`.
4. **Aturan Notifikasi:**
   - **Sudah diisi / Hari libur:** Diam (tidak kirim email).
   - **Belum diisi:** Kirim email pengingat dengan tautan langsung ke halaman pengisian hari itu.
   - **Terjadi Error (gagal login, web down, struktur berubah):** Kirim email alert error + simpan screenshot ke GitHub Actions Artifacts.

## Flow Sistem

```mermaid
flowchart LR
    A["Cron 16:00, 18:00, 22:00 WIB"] --> B{"Sesi Cookie Aktif?"}
    B -->|Ya| C["Dashboard"]
    B -->|Tidak| D["SSO Login"]
    D -->|Cloudflare| E["Email Alert Error"]
    D -->|Sukses| C
    C --> F{"Status Hari Ini"}
    F -->|FILLED atau LIBUR| G["Diam"]
    F -->|EMPTY| H["Email Reminder"]
    F -->|Error| E
```

## Setup di GitHub (Gratis, Tanpa Laptop Hidup)

1. **Buat repository GitHub baru bertipe `Private`** lalu push folder ini ke repo tersebut.
2. **Buat API Key di Resend:**
   - Masuk ke dashboard Resend → **API Keys** → **Create API Key** (permission: `Sending access`).
   - Jika belum memiliki domain terverifikasi di Resend, pengirim default otomatis memakai `Monev Bot <onboarding@resend.dev>` — dengan catatan `NOTIFY_EMAIL` harus sama dengan email yang terdaftar di akun Resend kamu.
   - Jika sudah verifikasi domain sendiri di Resend, kamu bisa menambahkan secret opsional `RESEND_FROM` (misal `Monev Bot <noreply@domainkamu.com>`).
3. **Tambahkan GitHub Repository Secrets** di menu **Settings → Secrets and variables → Actions → New repository secret**:
   - `MONEV_EMAIL`: Email akun SIAPkerja / Kemnaker kamu
   - `MONEV_PASSWORD`: Password akun SIAPkerja / Kemnaker kamu
   - `RESEND_API_KEY`: API key Resend (`re_...`)
   - `NOTIFY_EMAIL`: Email tujuan penerima notifikasi
   - `MONEV_STORAGE_STATE`: *(Disarankan untuk GitHub Actions)* Isi `artifacts/storage_state.json` hasil login lokal agar server GitHub langsung masuk ke `/dashboard` tanpa memicu verifikasi Cloudflare di `account.kemnaker.go.id`. Bisa diunggah cepat lewat:
     ```powershell
     Get-Content -Raw artifacts/storage_state.json | gh secret set MONEV_STORAGE_STATE
     ```
   - `GH_SECRETS_TOKEN`: *(Wajib agar sesi terus aktif otomatis)* GitHub Fine-grained Personal Access Token khusus repo ini dengan izin **Secrets: Read and write**, dipakai workflow untuk menyimpan kembali `MONEV_STORAGE_STATE` baru tiap kali `monev_refresh_token` dirotasi oleh server.
   - *(Opsional)* `RESEND_FROM`: Alamat pengirim jika pakai domain custom terverifikasi di Resend
   - *(Opsional, alternatif tanpa Resend)* `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_HOST`, `SMTP_PORT`
4. **Tes Manual Pertama Kali:**
   - Buka tab **Actions** → pilih workflow **Monev Daily Report Check** → klik **Run workflow**.
   - Cek log eksekusi untuk memastikan login SSO berhasil dan email Resend terkirim.
5. **Setup Penjadwalan Otomatis dengan `cron-job.org`:**
   - Workflow `.github/workflows/check_monev.yml` sengaja hanya menggunakan trigger `workflow_dispatch` (tanpa `schedule` bawaan GitHub) agar eksekusi selalu tepat waktu dan tidak terjadi eksekusi dobel akibat antrean cron GitHub.
   - **a. Buat Fine-grained Personal Access Token (PAT) di GitHub:**
     - Buka GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
     - Pada **Repository access**, pilih **Only select repositories** → pilih repository `monev-cron-agent` milikmu.
     - Pada **Permissions** → **Repository permissions**, ubah **Actions** menjadi **Read and write**.
     - Klik **Generate token** dan salin token-nya (`github_pat_...`).
   - **b. Buat Cronjob di `cron-job.org`:**
     - Daftar / login di `https://cron-job.org` → **Cronjobs** → **Create cronjob**.
     - **Title:** `Monev Daily Check`
     - **URL:** `https://api.github.com/repos/<GITHUB_USERNAME>/<REPO_NAME>/actions/workflows/check_monev.yml/dispatches` *(ganti `<GITHUB_USERNAME>/<REPO_NAME>` dengan repo milikmu)*.
     - **Execution schedule (Custom):**
       - **Timezone:** `Asia/Jakarta`
       - **Hours:** `16`, `18`, `22` | **Minutes:** `0`
       - **Days of month:** `*` | **Months:** `*` | **Days of week:** `Mon`, `Tue`, `Wed`, `Thu`, `Fri`
     - **Advanced:**
       - **Request method:** `POST`
       - **Headers:**
         - `Accept`: `application/vnd.github+json`
         - `Authorization`: `Bearer <TOKEN_GITHUB_KAMU>`
         - `X-GitHub-Api-Version`: `2022-11-28`
         - `User-Agent`: `cron-job-org`
       - **Request body:** `{"ref":"main"}`
     - Klik **Create**, lalu klik **Test run** untuk memastikan hasilnya **`204 No Content`** dan workflow langsung berjalan di tab **Actions**.

## Jalankan di Lokal (Opsional untuk Testing)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

copy .env.example .env
# Isi .env dengan kredensial kamu, lalu jalankan test tanpa kirim email:
python -m src.main --dry-run --force
```

## Maintenance

Bot berjalan otomatis tanpa laptop hidup, dengan perawatan ringan berikut:

1. **Perbarui `MONEV_STORAGE_STATE` jika sesi kedaluwarsa:**
   - Jika menerima email `[Monev Bot ERROR]` bertuliskan sesi kedaluwarsa / verifikasi keamanan Cloudflare di `account.kemnaker.go.id`, perbarui cookie sesi dari laptopmu (IP Indonesia) lalu unggah lagi ke GitHub Secret:
     ```powershell
     # Opsional: hapus sesi lama jika ingin memaksa login ulang dari awal
     Remove-Item -ErrorAction SilentlyContinue artifacts\storage_state.json

     .\.venv\Scripts\python.exe -m src.main --dry-run --force
     Get-Content -Raw artifacts\storage_state.json | gh secret set MONEV_STORAGE_STATE
     ```
2. **Alternatif tanpa `cron-job.org` (jika memakai `schedule` bawaan GitHub):**
   - Jika suatu saat kamu menambahkan kembali blok `schedule:` di `.github/workflows/check_monev.yml`, perlu diingat bahwa GitHub otomatis menonaktifkan jadwal `cron` jika repository tidak memiliki commit baru selama 60 hari berturut-turut (aktifkan lagi lewat tab **Actions** → **Enable workflow**).
3. **Diagnosa error:**
   - Setiap kegagalan di GitHub Actions menyimpan screenshot halaman terakhir ke artifact `monev-debug-artifacts` (tersimpan 7 hari) di halaman run yang gagal.

## Catatan Penting

- **Kenapa Memakai `cron-job.org` (`workflow_dispatch`):** GitHub Actions `schedule` gratis sering tertunda dari puluhan menit hingga beberapa jam di jam sibuk. Menggunakan `cron-job.org` untuk memanggil endpoint `workflow_dispatch` memastikan pengecekan berjalan tepat pada pukul `16:00`, `18:00`, dan `22:00` WIB.
- **Geo-blocking:** Jika server luar negeri (GitHub runner) sewaktu-waktu diblokir oleh firewall instansi, bot akan mengirim email alert error dan solusi alternatifnya adalah menjalankan script Python yang sama di VPS lokal Indonesia dengan `crontab`.
