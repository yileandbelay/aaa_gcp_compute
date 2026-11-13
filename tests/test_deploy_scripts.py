"""The shell scripts that run on the server: nightly backup (with an off-server copy via rclone),
restore, and safe HTTPS renewal. rclone and certbot are replaced by small stand-ins."""
import os
import re
import sqlite3
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "deploy" / "backup.sh"
RESTORE = ROOT / "deploy" / "restore.sh"

RCLONE_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for rclone: remote "fake:x" is the folder $FAKE_REMOTE_ROOT/x."""
    import fnmatch, os, shutil, sys, time
    args = sys.argv[1:]
    with open(os.environ["RCLONE_LOG"], "a") as log:
        log.write(" ".join(args) + "\\n")
    if os.environ.get("RCLONE_FAIL"):
        sys.exit(1)
    def real(path):
        return os.path.join(os.environ["FAKE_REMOTE_ROOT"], path[5:]) if path.startswith("fake:") else path
    cmd, rest = args[0], args[1:]
    includes, positional, i = [], [], 0
    flags = {}
    while i < len(rest):
        if rest[i] == "--include": includes.append(rest[i + 1]); i += 2
        elif rest[i] in ("--transfers", "--min-age"): flags[rest[i]] = rest[i + 1]; i += 2
        else: positional.append(rest[i]); i += 1
    if cmd == "copy":
        src, dst = real(positional[0]), real(positional[1])
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            if not includes or any(fnmatch.fnmatch(name, pat) for pat in includes):
                shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
    elif cmd == "delete":
        folder = real(positional[0])
        days = int(flags["--min-age"].rstrip("d"))
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.getmtime(path) < time.time() - days * 86400:
                os.remove(path)
''')


@pytest.fixture
def box(tmp_path):
    """A fake server: database, uploads, settings file, and an rclone stand-in on the PATH."""
    data = tmp_path / "data"
    (data / "uploads" / "receipts").mkdir(parents=True)
    (data / "uploads" / "receipts" / "slip.png").write_bytes(b"PNGDATA")
    db = data / "academy.db"
    con = sqlite3.connect(db)
    con.execute("create table user (id integer primary key, email text)")
    con.execute("insert into user (email) values ('a@example.com'), ('b@example.com')")
    con.commit(); con.close()
    stubs = tmp_path / "bin"
    stubs.mkdir()
    rclone = stubs / "rclone"
    rclone.write_text(RCLONE_STUB)
    rclone.chmod(rclone.stat().st_mode | stat.S_IEXEC)
    remote = tmp_path / "remote"
    env_file = tmp_path / "academy.env"
    env_file.write_text(f"DATABASE_URL=sqlite:///{db}\nUPLOAD_DIR={data / 'uploads'}\nBACKUP_DIR={tmp_path / 'backups'}\n"
                        f"RCLONE_REMOTE=fake:bucket/academy\n")
    return type("Box", (), dict(root=tmp_path, data=data, db=db, stubs=stubs, remote=remote, env_file=env_file,
                                log=tmp_path / "rclone.log", backups=tmp_path / "backups"))


def run(script, box, *args, with_rclone=True, extra_env=None):
    env = {"PATH": f"{box.stubs}:/usr/bin:/bin" if with_rclone else "/usr/bin:/bin", "HOME": str(box.root),
           "FAKE_REMOTE_ROOT": str(box.remote), "RCLONE_LOG": str(box.log), **(extra_env or {})}
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=env, timeout=60)


def remote_files(box):
    folder = box.remote / "bucket" / "academy"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# --- Nightly backup, local and off-server -----------------------------------------------------

def test_backup_without_a_remote_keeps_only_local_copies(box):
    box.env_file.write_text(box.env_file.read_text().replace("RCLONE_REMOTE=fake:bucket/academy\n", ""))
    r = run(BACKUP, box, str(box.env_file))
    assert r.returncode == 0, r.stderr
    names = [p.name for p in box.backups.iterdir()]
    assert any(n.startswith("db-") and n.endswith(".sqlite.gz") for n in names)
    assert any(n.startswith("uploads-") and n.endswith(".tar.gz") for n in names)
    assert not box.log.exists()


def test_backup_copies_todays_files_off_server(box):
    r = run(BACKUP, box, str(box.env_file))
    assert r.returncode == 0, r.stderr
    names = remote_files(box)
    assert len(names) == 2 and any(n.startswith("db-") for n in names) and any(n.startswith("uploads-") for n in names)
    assert "Off-server backup copied" in r.stdout


def test_backup_removes_off_server_copies_older_than_thirty_days_but_keeps_recent_ones(box):
    folder = box.remote / "bucket" / "academy"
    folder.mkdir(parents=True)
    old, recent = folder / "db-20260101-0300.sqlite.gz", folder / "db-20261101-0300.sqlite.gz"
    old.write_bytes(b"x"); recent.write_bytes(b"x")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    os.utime(recent, (time.time() - 10 * 86400,) * 2)
    assert run(BACKUP, box, str(box.env_file)).returncode == 0
    names = remote_files(box)
    assert old.name not in names and recent.name in names
    assert "--min-age 30d" in box.log.read_text()


def test_retention_can_be_changed(box):
    box.env_file.write_text(box.env_file.read_text() + "OFFSITE_RETENTION_DAYS=7\n")
    assert run(BACKUP, box, str(box.env_file)).returncode == 0
    assert "--min-age 7d" in box.log.read_text()


def test_a_failed_off_server_copy_is_reported_and_the_local_backup_is_kept(box):
    r = run(BACKUP, box, str(box.env_file), extra_env={"RCLONE_FAIL": "1"})
    assert r.returncode != 0 and "Off-server backup" in r.stderr and "failed" in r.stderr
    assert any(p.name.startswith("db-") for p in box.backups.iterdir())


def test_remote_set_but_rclone_missing_is_reported(box):
    r = run(BACKUP, box, str(box.env_file), with_rclone=False)
    assert r.returncode != 0 and "rclone is not installed" in r.stderr
    assert any(p.name.startswith("uploads-") for p in box.backups.iterdir())


# --- Restore on a scratch server --------------------------------------------------------------

def scratch(box, name="scratch"):
    folder = box.root / name
    (folder / "data").mkdir(parents=True)
    env = folder / "academy.env"
    env.write_text(f"DATABASE_URL=sqlite:///{folder / 'data' / 'academy.db'}\nUPLOAD_DIR={folder / 'data' / 'uploads'}\n"
                   f"BACKUP_DIR={folder / 'backups'}\n")
    return folder, env


def test_restore_from_the_off_server_copy_brings_back_database_and_files(box):
    assert run(BACKUP, box, str(box.env_file)).returncode == 0
    folder, env = scratch(box)
    r = run(RESTORE, box, "--env", str(env), "--from", "fake:bucket/academy")
    assert r.returncode == 0, r.stderr
    rows = sqlite3.connect(folder / "data" / "academy.db").execute("select email from user order by id").fetchall()
    assert rows == [("a@example.com",), ("b@example.com",)]
    assert (folder / "data" / "uploads" / "receipts" / "slip.png").read_bytes() == b"PNGDATA"


def test_restore_from_a_local_backup_folder(box):
    run(BACKUP, box, str(box.env_file))
    folder, env = scratch(box, "local-scratch")
    r = run(RESTORE, box, "--env", str(env), "--from", str(box.backups))
    assert r.returncode == 0, r.stderr
    assert sqlite3.connect(folder / "data" / "academy.db").execute("select count(*) from user").fetchone() == (2,)


def test_restore_refuses_to_overwrite_a_database_without_force_and_keeps_a_copy_with_it(box):
    run(BACKUP, box, str(box.env_file))
    folder, env = scratch(box)
    db = folder / "data" / "academy.db"
    sqlite3.connect(db).execute("create table keep (x)").connection.commit()
    r = run(RESTORE, box, "--env", str(env), "--from", str(box.backups))
    assert r.returncode != 0 and "--force" in r.stderr
    assert not sqlite3.connect(db).execute("select name from sqlite_master where name='user'").fetchall()
    r = run(RESTORE, box, "--env", str(env), "--from", str(box.backups), "--force")
    assert r.returncode == 0 and (folder / "data" / "academy.db.before-restore").exists()
    assert sqlite3.connect(db).execute("select count(*) from user").fetchone() == (2,)


def test_restore_picks_the_newest_backup_or_the_one_asked_for(box):
    run(BACKUP, box, str(box.env_file))
    for src in list(box.backups.iterdir()):
        stamp = src.name.split("-", 1)[1].split(".")[0]
        (box.backups / src.name.replace(stamp, "20200101-0000")).write_bytes(src.read_bytes())
    folder, env = scratch(box)
    r = run(RESTORE, box, "--env", str(env), "--from", str(box.backups))
    assert r.returncode == 0 and "Restoring backup 2020" not in r.stdout
    r = run(RESTORE, box, "--env", str(env), "--from", str(box.backups), "--stamp", "20200101-0000", "--force")
    assert r.returncode == 0 and "Restoring backup 20200101-0000" in r.stdout


def test_restore_reports_a_missing_backup(box):
    folder, env = scratch(box)
    (box.root / "empty").mkdir()
    r = run(RESTORE, box, "--env", str(env), "--from", str(box.root / "empty"))
    assert r.returncode != 0 and "No db-* backup found" in r.stderr


def test_restore_needs_its_options(box):
    assert run(RESTORE, box).returncode == 2


# --- Safe HTTPS renewal in the installer ------------------------------------------------------

HTTPS = ROOT / "deploy" / "https.sh"
INSTALL = ROOT / "deploy" / "install.sh"

PLAIN_CONF = "server {\n    listen 80;\n    server_name learn.example.org;\n}\n"
HTTPS_BLOCK = "server {\n    listen 443 ssl;\n    ssl_certificate /etc/letsencrypt/live/x/fullchain.pem;\n}\n"

CERTBOT_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for certbot: fails the first CERTBOT_FAILS calls, then adds an HTTPS block like certbot does."""
    import os, sys
    count_file = os.environ["STATE_DIR"] + "/certbot-calls"
    calls = int(open(count_file).read()) if os.path.exists(count_file) else 0
    open(count_file, "w").write(str(calls + 1))
    open(os.environ["STATE_DIR"] + "/certbot-args", "a").write(" ".join(sys.argv[1:]) + "\\n")
    if calls < int(os.environ.get("CERTBOT_FAILS", "0")):
        sys.exit(1)
    conf = os.environ.get("NGINX_CONF")
    if conf and os.path.exists(conf) and "ssl_certificate" not in open(conf).read():
        open(conf, "a").write("server {\\n    listen 443 ssl;\\n    ssl_certificate /etc/letsencrypt/live/x/fullchain.pem;\\n}\\n")
''')

PGREP_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for pgrep: reports a running certbot for the first PGREP_BUSY_CALLS calls."""
    import os, sys
    f = os.environ["STATE_DIR"] + "/pgrep-calls"
    n = int(open(f).read()) if os.path.exists(f) else 0
    open(f, "w").write(str(n + 1))
    sys.exit(0 if n < int(os.environ.get("PGREP_BUSY_CALLS", "0")) else 1)
''')

SLEEP_STUB = "#!/bin/sh\necho \"$1\" >> \"$STATE_DIR/sleeps\"\n"


@pytest.fixture
def https_box(tmp_path):
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir(); state.mkdir()
    for name, body in (("certbot", CERTBOT_STUB), ("pgrep", PGREP_STUB), ("sleep", SLEEP_STUB)):
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    conf = tmp_path / "nginx-site.conf"
    return type("H", (), dict(bin=bindir, state=state, conf=conf, root=tmp_path))


def bash_run(box, snippet, **env):
    full = {"PATH": f"{box.bin}:/usr/bin:/bin", "STATE_DIR": str(box.state), "NGINX_CONF": str(box.conf),
            "HOME": str(box.root), **{k: str(v) for k, v in env.items()}}
    return subprocess.run(["bash", "-c", f'. "{HTTPS}"\n{snippet}'], capture_output=True, text=True, env=full, timeout=60)


def counter(box, name):
    f = box.state / name
    return int(f.read_text()) if f.exists() else 0


def installer_rerun(box, **env):
    """What install.sh does on every run: snapshot HTTPS, rewrite the config as plain HTTP, ask certbot."""
    snippet = textwrap.dedent(f'''\
        https_snapshot "$NGINX_CONF"
        printf '%s' '{PLAIN_CONF}' > "$NGINX_CONF"
        if install_certificate learn.example.org me@example.org; then https_clear "$NGINX_CONF"
        else https_restore "$NGINX_CONF" || true; fi
        ''')
    return bash_run(box, snippet, **env)


def test_certificate_is_requested_with_the_expected_options(https_box):
    r = bash_run(https_box, "install_certificate learn.example.org me@example.org")
    assert r.returncode == 0, r.stderr
    args = (https_box.state / "certbot-args").read_text()
    assert "--nginx" in args and "--keep-until-expiring" in args and "--redirect" in args
    assert "-d learn.example.org" in args and "-m me@example.org" in args and "-n" in args


def test_installer_waits_while_another_certbot_runs_then_continues(https_box):
    r = bash_run(https_box, "install_certificate learn.example.org me@example.org", PGREP_BUSY_CALLS=3)
    assert r.returncode == 0 and "Another certbot run is active" in r.stdout
    assert counter(https_box, "certbot-calls") == 1 and counter(https_box, "pgrep-calls") >= 4
    assert (https_box.state / "sleeps").read_text().split() == ["5", "5", "5"]


def test_installer_gives_up_waiting_without_touching_certbot(https_box):
    r = bash_run(https_box, "install_certificate learn.example.org me@example.org", PGREP_BUSY_CALLS=999, CERTBOT_WAIT_SECONDS=20)
    assert r.returncode != 0 and "still active" in r.stderr
    assert counter(https_box, "certbot-calls") == 0


def test_certbot_failures_are_retried_with_growing_pauses(https_box):
    r = bash_run(https_box, "install_certificate learn.example.org me@example.org", CERTBOT_FAILS=2)
    assert r.returncode == 0 and counter(https_box, "certbot-calls") == 3
    assert (https_box.state / "sleeps").read_text().split() == ["10", "20"]


def test_certbot_that_never_works_returns_failure_after_the_set_number_of_tries(https_box):
    r = bash_run(https_box, "install_certificate learn.example.org me@example.org", CERTBOT_FAILS=99, CERTBOT_TRIES=3)
    assert r.returncode != 0 and counter(https_box, "certbot-calls") == 3 and "attempt 3 of 3 failed" in r.stderr


def test_rerunning_the_installer_twice_in_a_row_leaves_https_working(https_box):
    https_box.conf.write_text(PLAIN_CONF)
    for _ in range(2):
        r = installer_rerun(https_box)
        assert r.returncode == 0, r.stderr
        text = https_box.conf.read_text()
        assert "listen 443 ssl" in text and "ssl_certificate" in text
        assert not Path(str(https_box.conf) + ".https-keep").exists()


def test_a_busy_certbot_does_not_leave_the_site_on_plain_http(https_box):
    """The failure that took the site down: certbot busy, config rewritten as HTTP only."""
    https_box.conf.write_text(PLAIN_CONF + HTTPS_BLOCK)
    r = installer_rerun(https_box, PGREP_BUSY_CALLS=999, CERTBOT_WAIT_SECONDS=10)
    assert r.returncode == 0
    text = https_box.conf.read_text()
    assert "listen 443 ssl" in text and "ssl_certificate" in text      # the HTTPS version was put back


def test_a_failing_certbot_also_keeps_the_previous_https_config(https_box):
    https_box.conf.write_text(PLAIN_CONF + HTTPS_BLOCK)
    installer_rerun(https_box, CERTBOT_FAILS=99, CERTBOT_TRIES=2)
    assert "listen 443 ssl" in https_box.conf.read_text()


def test_first_install_without_a_previous_https_config_has_nothing_to_restore(https_box):
    https_box.conf.write_text(PLAIN_CONF)
    installer_rerun(https_box, CERTBOT_FAILS=99, CERTBOT_TRIES=1)
    assert "listen 443" not in https_box.conf.read_text()              # honest: plain HTTP until certbot works
    assert not Path(str(https_box.conf) + ".https-keep").exists()


def test_the_installer_uses_these_helpers_and_stays_valid_bash():
    text = INSTALL.read_text()
    for needle in ("https.sh", "https_snapshot", "install_certificate", "https_restore", "https_clear"):
        assert needle in text, needle
    assert subprocess.run(["bash", "-n", str(INSTALL)]).returncode == 0
    assert subprocess.run(["bash", "-n", str(HTTPS)]).returncode == 0
    assert "certbot --nginx" not in text                               # only the retrying helper calls certbot


# --- Main address with redirects from other names (www as the main site) ----------------------

GETENT_STUB = textwrap.dedent('''\
    #!/bin/sh
    # Stand-in for getent: a name containing "nodns" has no DNS record.
    case "$2" in *nodns*) exit 2 ;; *) echo "203.0.113.5 $2"; exit 0 ;; esac
''')


@pytest.fixture
def names_box(https_box):
    stub = https_box.bin / "getent"
    stub.write_text(GETENT_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return https_box


def test_one_certificate_covers_the_main_domain_and_every_alias(https_box):
    r = bash_run(https_box, "install_certificate www.example.org me@example.org example.org learn.example.org")
    assert r.returncode == 0, r.stderr
    args = (https_box.state / "certbot-args").read_text()
    assert "-d www.example.org -d example.org -d learn.example.org" in args
    assert "--cert-name www.example.org" in args and "--expand" in args and "--redirect" in args


def test_the_certificate_request_without_aliases_still_names_the_certificate(https_box):
    bash_run(https_box, "install_certificate learn.example.org me@example.org")
    args = (https_box.state / "certbot-args").read_text()
    assert "-d learn.example.org" in args and "--cert-name learn.example.org" in args and args.count("-d ") == 1


@pytest.mark.parametrize("raw,expected", [
    ("example.org learn.example.org", ["example.org", "learn.example.org"]),
    ("Example.ORG,learn.example.org", ["example.org", "learn.example.org"]),
    ("example.org, example.org  www.example.org", ["example.org"]),            # duplicates and the main domain dropped
    ("", []),
])
def test_alias_list_is_cleaned_up(https_box, raw, expected):
    r = bash_run(https_box, f'normalize_names www.example.org "{raw}"')
    assert r.returncode == 0 and r.stdout.split() == expected


@pytest.mark.parametrize("bad", ["not a domain", "-bad.example.org", "example", "exa_mple.org", "http://example.org", "a..b.org"])
def test_invalid_alias_names_are_rejected(https_box, bad):
    r = bash_run(https_box, f'normalize_names www.example.org "good.example.org {bad.split()[0]}"')
    assert r.returncode != 0 and "Not a valid domain name" in r.stderr


def test_names_without_dns_are_skipped_with_a_clear_message(names_box):
    r = bash_run(names_box, "resolvable_names example.org nodns.example.org learn.example.org")
    assert r.stdout.split() == ["example.org", "learn.example.org"]
    assert "nodns.example.org has no DNS record yet" in r.stderr and "re-run the installer" in r.stderr


def test_redirect_block_sends_the_aliases_to_the_main_domain_and_leaves_room_for_certbot(https_box):
    r = bash_run(https_box, "redirect_block www.example.org example.org learn.example.org")
    block = r.stdout
    assert "server_name example.org learn.example.org;" in block and "listen 80;" in block
    assert "return 301 https://www.example.org$request_uri;" in block
    # the redirect is inside "location /", so certbot's challenge location can still answer first
    assert re.search(r"location / \{\s*return 301", block) and not re.search(r"server_name[^\n]*\n\s*return", block)
    assert block.count("{") == block.count("}") == 2


def test_installer_offers_the_option_remembers_it_and_uses_the_helpers():
    text = INSTALL.read_text()
    for needle in ("--redirect-from", "SAVED_REDIRECTS", "normalize_names", "resolvable_names", "redirect_block",
                   "install_certificate \"$DOMAIN\" \"$EMAIL\" $LIVE_ALIASES"):
        assert needle in text, needle
    assert text.index("redirect_block \"$DOMAIN\"") < text.index("install_certificate \"$DOMAIN\"")   # redirect exists before certbot runs
    assert subprocess.run(["bash", "-n", str(INSTALL)]).returncode == 0


def test_installer_help_documents_the_main_address_and_redirects():
    r = subprocess.run(["bash", str(INSTALL), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--redirect-from" in r.stdout and "main address" in r.stdout


def test_two_installer_runs_with_aliases_keep_https_on_every_block(names_box):
    """Simulates install.sh: plain config plus redirect block, certbot adds HTTPS to each block, twice."""
    snippet = textwrap.dedent(f'''\
        for run in 1 2; do
          https_snapshot "$NGINX_CONF"
          printf '%s' '{PLAIN_CONF}' > "$NGINX_CONF"
          redirect_block www.example.org $(resolvable_names example.org learn.example.org) >> "$NGINX_CONF"
          if install_certificate www.example.org me@example.org example.org learn.example.org; then https_clear "$NGINX_CONF"
          else https_restore "$NGINX_CONF" || true; fi
        done
        ''')
    r = bash_run(names_box, snippet)
    assert r.returncode == 0, r.stderr
    text = names_box.conf.read_text()
    assert "listen 443 ssl" in text and "server_name example.org learn.example.org;" in text
    assert not Path(str(names_box.conf) + ".https-keep").exists()
