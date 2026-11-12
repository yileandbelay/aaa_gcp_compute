# Mek Online Academy

An online learning platform for professional training. Students register, pay for courses, follow video and text lessons, download course materials, take auto-graded assessments and earn verifiable certificates.

It's built with Python / Flask and runs on SQLite by default (PostgreSQL is supported). It's also designed as a **reusable template**: a new academy is a settings file plus, optionally, a theme folder and a JSON file of starter courses. You don't need to change the core code.

## Features

| Module | What you get |
|---|---|
| **Students and staff** | Students register (name, email, phone, city), log in, reset passwords by email. **Staff** (admins and instructors) are kept apart from students: separate lists, never counted as students. Admins add staff by email; the new person sets their own password. Suspended accounts are locked out at once |
| **Roles** | **Admin:** everything. **Instructor (course admin):** manages only the courses assigned to them, including their lessons, quizzes, announcements and students |
| **Enrolling students** | Each course has an "Enroll students" page: tick registered students or paste email addresses. People without an account are invited and get the course when they register. Optional email notice; see who has access and how (paid, assigned, free), remove or restore access |
| **Courses** | Categories, levels, training hours, search and category filter, free or paid, free-preview lessons, instructor profiles |
| **Lessons** | Video, text and external-link lessons. Each lesson can have downloadable files (PDF, slides, spreadsheets…) and links (another site, a free course elsewhere). Videos: upload to your video host, paste a YouTube or Vimeo link, or store on your server. Progress tracking |
| **Contact** | A Contact page with your details (email, phone, WhatsApp, Telegram, address, hours) and a message form. Messages are saved in an admin inbox with an unread badge, and also emailed to you. Spam protection: hidden field and a per-IP hourly limit |
| **Video** | Upload from the lesson editor straight to a video host, and play it in the lesson with a per-student signed link: **Bunny Stream** or **VdoCipher** (DRM). Hosts are pluggable. Videos can also be stored on your own server (expiring per-student links, served by nginx) |
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
| Content protection | `PROTECT_LESSON_TEXT` (block copying lesson text), `VIDEO_WATERMARK`, `ALLOW_VIDEO_DOWNLOAD`, `ALLOW_MATERIAL_DOWNLOAD` |
| Video hosting | `BUNNY_LIBRARY_ID`, `BUNNY_API_KEY`, `BUNNY_TOKEN_KEY`, `VDOCIPHER_API_SECRET` |
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

## Running the academy day to day

**People.** *Admin → Students* lists learners; *Admin → Staff* lists admins and instructors. To add a teacher, open Staff, fill in the "Add a staff member" form and choose **Instructor**. They get an email with a link to choose a password (or, if email is not set up yet, the link appears on screen for you to send them). Then open a course and choose them in the **Instructor** field so they can edit it.

**Courses.** *Admin → Courses → New course*, then follow the checklist on the course page: add lessons (the "Quick add" box creates a lesson and opens it), add a quiz, publish. In the lesson editor choose **Video**, **Text** or **External link**. For videos, upload to your video host or paste a YouTube or Vimeo link. Public YouTube or Vimeo videos are not protected, so use them for free content. Add files and links under "Files and links": one link per line as `Title | https://address`.

**Assigning a course to students.** Open the course, click **Enroll and manage students**, tick the students and/or paste email addresses, and press Enroll. Students you enroll see the course under "My learning". If an address has no account yet, the course is added when that person registers. Students can also pay for themselves, and the list shows how each one got access.

**Messages.** What visitors send from the Contact page appears under *Admin → Messages*.

## Video hosting

Hosting video on a video service keeps the heavy traffic off your server, so a small, cheap server is enough. Set up one or both services; each lesson picks its host in the lesson editor.

**Bunny Stream** ([bunny.net/stream](https://bunny.net/stream/)):
1. Create a Video Library. On its **API** page copy the **Library ID** and the library's **API key**.
2. On its **Security** page turn on **Embed view token authentication** and copy its token key. (Also restrict allowed domains to your own site if the library offers that.)
3. Set `BUNNY_LIBRARY_ID`, `BUNNY_API_KEY` and `BUNNY_TOKEN_KEY` in `/etc/academy.env` and restart the app.

**VdoCipher** ([vdocipher.com](https://www.vdocipher.com)): DRM-encrypted video with the student's name and email burned in. Set `VDOCIPHER_API_SECRET` (dashboard → Config → API keys).

In the lesson editor choose the host, then upload the file there (it goes straight from your browser to the host; the page loads the open-source `tus-js-client` from the jsDelivr CDN for Bunny's resumable uploads) or paste the video ID of a video you uploaded in the host's own dashboard. After saving, students watch it in the host's player with a short-lived signed link, and only enrolled students are given one. Bunny cannot burn a watermark into the video, so the student's email is shown over the player (`VIDEO_WATERMARK`, on by default).

Videos on your own server remain an option, but they use your server's disk and bandwidth.

To add another host (Vimeo, Mux, Cloudflare Stream…), copy `academy/videohosts/bunny.py`, implement the same few methods, and register it in `academy/videohosts/__init__.py`.

No website can make video impossible to copy: someone can always film the screen. Signed links and watermarks stop casual sharing and make leaks traceable.

## Project layout

```
academy/
  __init__.py        app factory, theme loading, CLI (create-admin, seed-demo)
  config.py          every setting, read from environment variables
  models.py          database tables
  routes/            one blueprint per area: main, auth, learn, assess, pay, admin
  payments.py        Chapa integration       videohosts/  Bunny Stream and VdoCipher providers
  mail.py            SMTP email              utils.py   CSRF, access checks, progress
  enrollment.py      assigning courses, invitations
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
