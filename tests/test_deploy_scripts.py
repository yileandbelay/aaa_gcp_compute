"""The shell scripts that run on the server: nightly backup (with an off-server copy via rclone),
restore, and safe HTTPS renewal. rclone and certbot are replaced by small stand-ins."""
import os
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
