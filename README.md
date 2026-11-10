# Kasina Cakery Academy

A simple online learning platform for **Kasina Cakery** training: students register, pay for courses, follow protected video and text lectures, take auto-graded assessments and earn verifiable certificates.

Built with Python / Flask + SQLAlchemy. It runs on SQLite out of the box and on PostgreSQL (e.g. Cloud SQL) in production. It's designed to run on a single Google Compute Engine VM.

## Features

| Area | What you get |
|---|---|
| **Student registration** | Sign-up with name, email, phone and city; login with "remember me"; profile and password change; admins can suspend accounts |
| **Protected lectures** | **Video** lectures (uploaded mp4/webm/mov) and **text** lectures (Markdown recipes, tables, notes). Files are stored outside the web root and streamed only to enrolled students through **expiring, per-student signed links** (seeking is supported). The player has download disabled, a moving email watermark, and right-click/copy is blocked on lecture text. Lessons can be marked as **free previews** |
| **Progress tracking** | Mark-complete per lesson, progress bars per course, "continue" navigation |
| **Assessments** | Multiple-choice quizzes and final exams, auto-graded, with a pass mark, attempt limits, an optional timer (enforced on the server) and explanations. Correct answers are revealed only after the student passes or runs out of attempts |
| **Payments** | **Chapa** online checkout (telebirr, CBE Birr, M-Pesa, cards). Each payment is verified server-side with Chapa's API, so forged webhooks are ignored. Also **manual bank transfer / mobile money**: the student uploads a receipt and an admin approves it. Free courses, printable payment receipts, and a demo mode for testing |
| **Certificates** | Issued automatically when all lessons are done and all assessments are passed. Printable, with a public verification URL |
| **Engagement** | Per-lesson Q&A / discussion and course announcements |
| **Admin / instructor back-office** | Dashboard (students, revenue, pending receipts), course/lesson/assessment editors, lesson re-ordering, per-course student progress, assessment results, student search + CSV export, manual access grants (scholarships), payment review |
| **Security** | Hashed passwords, CSRF protection on every form, role-based access (student / instructor / admin), sanitized Markdown, upload type checks, safe redirects, security headers |

Roles: **student** (default), **instructor** (manages course content), **admin** (also manages students and payments).

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export FLASK_APP=wsgi.py PAYMENT_DEMO_MODE=true
flask seed-demo            # sample courses + admin@kasina.local / admin12345
flask run                  # http://127.0.0.1:5000
```

Run the tests with `pytest`.

To create a real admin account, run `flask create-admin`.

## Configuration

Settings come from environment variables. See [`.env.example`](.env.example) for the full list. The important ones:

- `SECRET_KEY`: required in production (it also signs the video links).
- `DATABASE_URL`: defaults to SQLite in `instance/`, e.g. `postgresql+psycopg2://user:pass@host/db`.
- `UPLOAD_DIR`: where videos and receipts are stored (keep it on a persistent disk).
- `CHAPA_SECRET_KEY`: enables Chapa checkout. Get it from the Chapa dashboard and use a test key first.
- `MANUAL_PAYMENT_ENABLED`, `MANUAL_PAYMENT_INSTRUCTIONS`: bank or telebirr transfer details shown to students.
- `PAYMENT_DEMO_MODE`: simulates payments. **Never enable this in production.**
- `BEHIND_PROXY=true` when running behind nginx or a load balancer, and `SESSION_COOKIE_SECURE=true` once you have HTTPS.

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
2. Add `CHAPA_SECRET_KEY` and your bank details to `/etc/kasina.env`, then run `sudo systemctl restart kasina`.
3. Point your domain at the VM and enable HTTPS (`sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx`). Then set `SESSION_COOKIE_SECURE=true`.
4. Back up `/var/lib/kasina` (database and uploads), for example with scheduled disk snapshots. Or move the database to Cloud SQL via `DATABASE_URL`.

There is also a `Dockerfile` if you prefer containers (Cloud Run / GKE). In that case, put uploads on a mounted volume or bucket.

## About content protection

No website can make streamed video impossible to copy; a determined user can always screen-record. This platform takes the practical steps:

- Lectures are never publicly addressable.
- Links expire and work only for the student they were issued to.
- Download controls are hidden.
- Each student's email is watermarked on the video, which discourages leaks and makes them traceable.

For stronger DRM you could later move videos to a provider such as Mux, Cloudflare Stream or Vimeo with signed playback.

## Project layout

```
kasina/
  __init__.py      app factory, CLI commands (create-admin, seed-demo)
  config.py        environment-based settings
  models.py        User, Course, Lesson, Enrollment, Payment, Assessment, Question,
                   Attempt, Certificate, LessonProgress, Comment, Announcement
  payments.py      Chapa integration
  utils.py         CSRF, role guards, markdown, signed video tokens, progress
  routes/          main, auth, learn, assess, pay, admin blueprints
  templates/       Jinja templates
deploy/            GCE startup script, systemd unit, nginx config
tests/             pytest suite
```
