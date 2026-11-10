# Kasina Cakery Academy

A simple online learning platform for **Kasina Cakery** training: students register, pay for courses, follow protected video and text lectures, take auto-graded assessments and earn verifiable certificates.

Built with Python / Flask + SQLAlchemy. It runs on SQLite out of the box and on PostgreSQL (e.g. Cloud SQL) in production. It's designed to run on a single Google Compute Engine VM.

## Features

| Area | What you get |
|---|---|
| **Student registration** | Sign-up with name, email, phone and city; login with "remember me"; password reset by email; profile and password change; admins can suspend accounts |
| **Protected lectures** | **Video** lectures and **text** lectures (Markdown recipes, tables, notes). Videos can be hosted with **VdoCipher DRM** (encrypted, with each student's name and email burned into the stream) or stored on this server and streamed to enrolled students through **expiring, per-student links**. Right-click/copy is blocked on lecture text. Lessons can be marked as **free previews** |
| **Progress tracking** | Mark-complete per lesson, progress bars per course, "continue" navigation |
| **Assessments** | Multiple-choice quizzes and final exams, auto-graded, with a pass mark, attempt limits, an optional timer (enforced on the server) and explanations. Correct answers are revealed only after the student passes or runs out of attempts |
| **Payments** | **Chapa** online checkout (telebirr, CBE Birr, M-Pesa, cards). Each payment is verified server-side with Chapa's API, so forged webhooks are ignored. Also **manual bank transfer / mobile money**: the student uploads a receipt, an admin approves it and the student gets an email. Free courses, printable payment receipts, and a demo mode for testing |
| **Certificates** | Issued automatically when all lessons are done and all assessments are passed. Printable, with a public verification URL |
| **Engagement** | Per-lesson Q&A / discussion and course announcements |
| **Admin / instructor back-office** | Dashboard (students, revenue, pending receipts), course/lesson/assessment editors, lesson re-ordering, per-course student progress, assessment results, student search + CSV export, manual access grants (scholarships), payment review |
| **Security** | Hashed passwords, login throttling after repeated failures, CSRF protection on every form, role-based access (student / instructor / admin), sanitized Markdown, upload type checks, safe redirects, security headers |
| **Policies** | Terms of use, privacy and refund pages (template text to review before launch) |
| **Operations** | Database migrations, daily backups (optionally to Cloud Storage), nginx streaming of locally stored videos |

Roles: **student** (default), **instructor** (manages course content), **admin** (also manages students and payments).

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export FLASK_APP=wsgi.py PAYMENT_DEMO_MODE=true
flask db upgrade           # create / update the database tables
flask seed-demo            # sample courses + admin@kasina.local / admin12345
flask run                  # http://127.0.0.1:5000
```

Run the tests with `pytest`.

To create a real admin account, run `flask create-admin`.

Without email settings, password-reset links are printed in the server log instead of being sent.

If you ran an earlier version locally, delete `instance/kasina.db` once before `flask db upgrade`; that copy was created without migrations.

When you change `models.py`, create a migration with `flask db migrate -m "describe the change"`, review the generated file in `kasina/migrations/versions/`, then run `flask db upgrade`.

## Configuration

Settings come from environment variables. See [`.env.example`](.env.example) for the full list. The important ones:

- `SECRET_KEY`: required in production (it also signs the video links).
- `DATABASE_URL`: defaults to SQLite in `instance/`, e.g. `postgresql+psycopg2://user:pass@host/db`.
- `UPLOAD_DIR`: where videos and receipts are stored (keep it on a persistent disk).
- `CHAPA_SECRET_KEY`: enables Chapa checkout. Get it from the Chapa dashboard and use a test key first.
- `MANUAL_PAYMENT_ENABLED`, `MANUAL_PAYMENT_INSTRUCTIONS`: bank or telebirr transfer details shown to students.
- `PAYMENT_DEMO_MODE`: simulates payments. **Never enable this in production.**
- `BEHIND_PROXY=true` when running behind nginx or a load balancer, and `SESSION_COOKIE_SECURE=true` once you have HTTPS.
- `VDOCIPHER_API_SECRET`: enables DRM-protected video (see below).
- `MAIL_SERVER`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`: SMTP account for password resets and payment emails.
- `CONTACT_EMAIL`, `CONTACT_PHONE`: shown on the policy pages.
- `USE_X_ACCEL_REDIRECT=true`: nginx streams locally stored videos after the app checks access (set automatically by the GCE script).

## Deploying on Google Compute Engine

```bash
gcloud compute instances create kasina-academy \
  --zone=europe-west1-b --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB --tags=http-server,https-server \
  --metadata=kasina-repo=https://github.com/yileandbelay/aaa_gcp_compute.git,kasina-branch=main,kasina-domain=academy.example.com \
  --metadata-from-file=startup-script=deploy/gce-startup.sh

gcloud compute firewall-rules create allow-http --allow=tcp:80,tcp:443 --target-tags=http-server
```

The startup script installs Python and nginx, clones the repo to `/opt/kasina`, writes `/etc/kasina.env` with a random `SECRET_KEY`, and runs gunicorn under systemd behind nginx. After that:

1. SSH in and create an admin:
   `sudo -u kasina bash -c 'set -a; . /etc/kasina.env; cd /opt/kasina && .venv/bin/flask --app wsgi create-admin'`
2. Add `CHAPA_SECRET_KEY`, `VDOCIPHER_API_SECRET`, the mail settings, your bank details and contact details to `/etc/kasina.env`, then run `sudo systemctl restart kasina`.
3. Point your domain at the VM and enable HTTPS (`sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx`). Then set `SESSION_COOKIE_SECURE=true`.
4. Backups run daily from `/etc/cron.daily/kasina-backup` into `/var/lib/kasina/backups` (kept 14 days). To also copy them off the VM, create a bucket and add `kasina-backup-bucket=gs://your-bucket` to the instance metadata (or `BACKUP_BUCKET=` in `/etc/kasina.env`); the VM's service account needs write access to that bucket. Test a restore before launch.
5. Review the text of the terms, privacy and refund pages in `kasina/templates/policies/`.

To update the server after new code is merged, re-run the startup script (`sudo google_metadata_script_runner startup`). It pulls the code, applies database migrations and restarts the app.

There is also a `Dockerfile` if you prefer containers (Cloud Run / GKE). In that case, put uploads on a mounted volume or bucket.

## Protected video with VdoCipher

[VdoCipher](https://www.vdocipher.com) hosts videos encrypted with Widevine and FairPlay DRM. Browsers that support DRM generally show a black frame to screen-recording software, and every playback carries a moving watermark with the student's name and email.

1. Create a VdoCipher account and copy the API secret from the dashboard (Config → API keys).
2. Set `VDOCIPHER_API_SECRET` and restart the app.
3. In the lesson editor choose **Protected (VdoCipher DRM)**. Either upload the file there (it goes straight from your browser to VdoCipher) or upload it in the VdoCipher dashboard and paste its video ID.
4. VdoCipher takes a few minutes to process a new video; the lesson editor shows its status.

The API secret stays on the server. For each viewing the server asks VdoCipher for a five-minute playback code tied to that student, and only enrolled students get one.

Lessons can also keep using videos stored on this server ("This server" option). Those get expiring per-student links and an on-screen email watermark, but no DRM.

No website can make video impossible to copy: someone can always film the screen with another device. The watermark makes such leaks traceable to an account.

## Project layout

```
kasina/
  __init__.py      app factory, CLI commands (create-admin, seed-demo)
  config.py        environment-based settings
  models.py        User, Course, Lesson, Enrollment, Payment, Assessment, Question,
                   Attempt, Certificate, LessonProgress, Comment, Announcement, LoginAttempt
  migrations/      database migrations (Flask-Migrate / Alembic)
  payments.py      Chapa integration
  video.py         VdoCipher DRM integration
  mail.py          SMTP email
  utils.py         CSRF, role guards, markdown, signed video tokens, progress
  routes/          main, auth, learn, assess, pay, admin blueprints
  templates/       Jinja templates
deploy/            GCE startup script, systemd unit, nginx config, daily backup script
tests/             pytest suite
```
