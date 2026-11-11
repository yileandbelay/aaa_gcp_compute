# Mek Online Academy

An online learning platform for professional training. Students register, pay for courses, follow video and text lessons, download course materials, take auto-graded assessments and earn verifiable certificates.

It's built with Python / Flask and runs on SQLite by default (PostgreSQL is supported). It's also designed as a **reusable template**: a new academy is a settings file plus, optionally, a theme folder and a JSON file of starter courses. You don't need to change the core code.

## Features

| Module | What you get |
|---|---|
| **Students** | Registration (name, email, phone, city), login, password reset by email, profile; admins can suspend accounts |
| **Courses** | Categories, levels, training hours, search and category filter, free or paid, free-preview lessons, instructor profiles |
| **Lessons** | Video and text (Markdown with tables and lists) lessons, downloadable materials (PDF, slides, spreadsheets…), progress tracking |
| **Video** | Videos stored on your server and streamed through expiring per-student links (nginx serves the file after the app checks access), or **VdoCipher DRM** with a per-student watermark |
| **Assessments** | Multiple-choice quizzes and exams with pass mark, attempt limit, server-enforced timer, explanations; answers revealed only after passing or using all attempts |
| **Payments** | **Chapa** (telebirr, CBE Birr, M-Pesa, cards) verified with Chapa's API; **bank transfer / mobile money** with receipt upload and admin approval; emails to the student; printable receipts |
| **Certificates** | Issued automatically on completion, show training hours, public verification link |
| **Engagement** | Per-lesson Q&A and course announcements |
| **Admin** | Dashboard, course/lesson/assessment editors, student progress, assessment results, student search and CSV export, manual access grants, payment review |
| **Security** | Hashed passwords, login throttling, CSRF protection, role-based access (student / instructor / admin), sanitized Markdown, upload type checks, security headers |
| **Operations** | One-command server install with HTTPS, database migrations, daily backups, health check at `/healthz`, Docker option, CI tests |

## Try it locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export FLASK_APP=wsgi.py PAYMENT_DEMO_MODE=true
flask db upgrade          # create the database
flask seed-demo           # sample courses + demo accounts (see below)
flask run                 # http://127.0.0.1:5000
```

The demo accounts are:
- admin: `admin@example.com` / `admin12345`
- instructor: `instructor@example.com` / `instructor123`

Register a new account to see the student side. Run the tests with `pytest`.

## Deploy to a server

You need a VPS (virtual server) running **Ubuntu 22.04+ or Debian 12+**, for example from DigitalOcean, Hetzner, Contabo, Linode or Google Cloud. 2 GB of RAM is enough to start. You also need a domain name whose DNS **A record points to the server's IP address**. Shared web hosting (cPanel) is not suitable.

On the server:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/yileandbelay/aaa_gcp_compute.git
cd aaa_gcp_compute
sudo bash deploy/install.sh --domain learn.example.com --email you@example.com
```

If the repository is private, the server needs permission to clone it. The simplest way is a GitHub [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys) with read-only access; clone with the `git@github.com:...` address. The installer reuses that address for updates.

The installer sets everything up:
- It installs Python, nginx and certbot.
- It creates a system user and the app in `/opt/academy`, with data in `/var/lib/academy`.
- It writes `/etc/academy.env` with a random secret key.
- It runs the database migrations.
- It starts the app as a systemd service behind nginx.
- It gets a free HTTPS certificate from Let's Encrypt.
- It schedules a daily backup.

Then:

1. **Configure the academy.** Edit the settings with `sudo nano /etc/academy.env`. At minimum set:
   - `SITE_NAME`
   - `CONTACT_EMAIL` and `CONTACT_PHONE`
   - `MANUAL_PAYMENT_INSTRUCTIONS` with your real account details
   - `CHAPA_SECRET_KEY` if you use Chapa
   - the `MAIL_*` settings

   Apply the changes with `sudo systemctl restart academy`.
2. **Create your admin account:**
   ```bash
   sudo -u academy bash -c "set -a; . /etc/academy.env; cd /opt/academy && .venv/bin/flask --app wsgi create-admin"
   ```
3. **Review the policy pages.** Check the text of `/policies/terms`, `/policies/privacy` and `/policies/refunds`. To change it, override the pages in a theme folder.
4. **Add content.** Log in and create your courses under **Admin → Courses**.

**Updating** after new code is pushed: `cd ~/aaa_gcp_compute && git pull && sudo bash deploy/install.sh`. The domain and other options are remembered. Your settings and data are kept.

**Logs:** `journalctl -u academy -f`. **Health check:** `https://learn.example.com/healthz`.

**Other ways to deploy:**
- **Google Compute Engine:** use `deploy/gce-startup.sh` as the instance startup script. The header of that file explains the metadata keys it reads.
- **Docker:** `cp .env.example .env`, edit it, then run `docker compose up -d --build`. Put an HTTPS reverse proxy in front of port 8000.

### Several academies on one server

Give each its own name, port and domain:

```bash
sudo bash deploy/install.sh --name mek --port 8000 --domain learn.mek.example --email you@example.com
sudo bash deploy/install.sh --name acme --port 8001 --domain academy.acme.example --email you@example.com
```

Each gets its own user, database, uploads, settings file (`/etc/<name>.env`), service and backups.

## Making it your own (reusing the template)

You can set an academy up in three layers, none of which touch the core code.

**1. Settings (`/etc/<name>.env`, or `.env` locally).** Every option is listed with comments in [`.env.example`](.env.example).

| Group | Settings |
|---|---|
| Branding | `SITE_NAME`, `SITE_SHORT_NAME`, `SITE_TAGLINE`, `SITE_HEADLINE`, `SITE_INTRO`, `BRAND_COLOR`, `TX_PREFIX`, `CONTACT_EMAIL`, `CONTACT_PHONE` |
| Modules on/off | `FEATURE_DISCUSSION`, `FEATURE_ANNOUNCEMENTS`, `FEATURE_CERTIFICATES` |
| Content protection | `PROTECT_LESSON_TEXT` (block copying lesson text), `VIDEO_WATERMARK`, `ALLOW_VIDEO_DOWNLOAD`, `ALLOW_MATERIAL_DOWNLOAD`, `VDOCIPHER_API_SECRET` (DRM) |
| Payments | `CURRENCY`, `CHAPA_SECRET_KEY`, `MANUAL_PAYMENT_ENABLED`, `MANUAL_PAYMENT_INSTRUCTIONS` |
| Email | `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` |

**2. Theme folder (`THEME_DIR`).** Point it at a folder like this:

```
my-theme/
  templates/         any file here replaces the built-in template with the same path,
    index.html       e.g. a custom home page
    policies/terms.html
  static/
    theme.css        loaded after the built-in styles (colours, fonts, spacing)
    logo.png         available at /theme/logo.png
```

Copy a template from `academy/templates/` into the theme folder and edit the copy. Keep themes in their own folder or repository so updates to the core code don't overwrite them.

**3. Starter courses (`flask seed-demo --file my_courses.json`).** This loads courses, lessons and quizzes from a JSON file in the format of [`academy/sample_data/demo_courses.json`](academy/sample_data/demo_courses.json). Quiz answers are numbered from 1. The command also creates the demo admin account, so on a live site use it only on a test copy, or change that account's password afterwards.

## Protected video with VdoCipher (optional)

[VdoCipher](https://www.vdocipher.com) hosts videos encrypted with Widevine and FairPlay DRM, with a moving watermark of the student's name and email.

1. Set `VDOCIPHER_API_SECRET`. You get it from the VdoCipher dashboard under Config → API keys.
2. In the lesson editor choose **Protected (VdoCipher DRM)**, then either upload the file there or paste a video ID.

Without it, videos are stored on your server. They are then still only reachable by enrolled students, through links that expire.

No website can make video impossible to copy: someone can always film the screen. Watermarks make leaks traceable.

## Project layout

```
academy/
  __init__.py        app factory, theme loading, CLI (create-admin, seed-demo)
  config.py          every setting, read from environment variables
  models.py          database tables
  routes/            one blueprint per area: main, auth, learn, assess, pay, admin
  payments.py        Chapa integration       video.py   VdoCipher integration
  mail.py            SMTP email              utils.py   CSRF, access checks, progress
  seed.py            JSON course loader      sample_data/demo_courses.json
  migrations/        database migrations (Flask-Migrate / Alembic)
  templates/ static/ built-in pages and styles (override with a theme folder)
deploy/
  install.sh         one-command install / update for Ubuntu & Debian
  gce-startup.sh     Google Compute Engine wrapper for install.sh
  academy.service    systemd unit template      nginx.conf   nginx site template
  backup.sh          daily database + uploads backup
tests/               pytest suite (run by GitHub Actions on every push)
```

When you change `academy/models.py`, create a migration with `flask db migrate -m "what changed"` and review the generated file in `academy/migrations/versions/`. Commit it with your change. `install.sh` applies it on the next update.
