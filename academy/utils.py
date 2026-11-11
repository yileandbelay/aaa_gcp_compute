"""Shared helpers: CSRF, role guards, markdown, signed video tokens, progress."""
import os
import re
import secrets
import uuid
from functools import wraps

import markdown as md
import nh3
from flask import abort, current_app, request, session
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import Markup

from .models import Assessment, Attempt, Certificate, LessonProgress, db


# --- CSRF -------------------------------------------------------------------

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.endpoint == "pay.chapa_webhook":
            return  # server-to-server; verified against the Chapa API instead
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Invalid or missing CSRF token. Reload the page and try again.")


# --- Access control ---------------------------------------------------------

def staff_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not current_user.is_staff:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


# --- Content ----------------------------------------------------------------

def render_markdown(text):
    html = md.markdown(text or "", extensions=["extra", "sane_lists", "nl2br"])
    return Markup(nh3.clean(html))


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or uuid.uuid4().hex[:8]


def save_upload(file_storage, subdir, allowed_ext):
    """Save an uploaded file under UPLOAD_DIR/subdir with a random name."""
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in allowed_ext:
        abort(400, f"File type .{ext} is not allowed. Allowed: {', '.join(sorted(allowed_ext))}")
    name = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(current_app.config["UPLOAD_DIR"], subdir)
    os.makedirs(folder, exist_ok=True)
    file_storage.save(os.path.join(folder, name))
    return name


# --- Signed, user-bound video tokens -----------------------------------------

def _video_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="lesson-video")


def make_video_token(user_id, lesson_id):
    return _video_serializer().dumps({"u": user_id, "l": lesson_id})


def verify_video_token(token, user_id, lesson_id):
    try:
        data = _video_serializer().loads(token, max_age=current_app.config["VIDEO_TOKEN_TTL"])
    except (BadSignature, SignatureExpired):
        return False
    return data.get("u") == user_id and data.get("l") == lesson_id


# --- Progress & certificates --------------------------------------------------

def course_progress(user, course):
    lesson_ids = [l.id for l in course.lessons]
    done = set()
    if lesson_ids:
        done = {
            p.lesson_id
            for p in LessonProgress.query.filter(
                LessonProgress.user_id == user.id, LessonProgress.lesson_id.in_(lesson_ids)
            )
        }
    passed = set()
    for a in course.assessments:
        if Attempt.query.filter_by(user_id=user.id, assessment_id=a.id, passed=True).first():
            passed.add(a.id)
    total = len(lesson_ids) + len(course.assessments)
    finished = len(done) + len(passed)
    return {
        "done_lessons": done,
        "passed_assessments": passed,
        "percent": int(round(100 * finished / total)) if total else 0,
        "complete": total > 0 and finished == total,
    }


def maybe_issue_certificate(user, course):
    """Issue a certificate once every lesson is done and every assessment passed."""
    if not current_app.config.get("FEATURE_CERTIFICATES", True):
        return None
    existing = Certificate.query.filter_by(user_id=user.id, course_id=course.id).first()
    if existing:
        return existing
    if not course_progress(user, course)["complete"]:
        return None
    cert = Certificate(user_id=user.id, course_id=course.id)
    db.session.add(cert)
    db.session.commit()
    return cert


def attempts_used(user, assessment):
    return Attempt.query.filter(
        Attempt.user_id == user.id,
        Attempt.assessment_id == assessment.id,
        Attempt.submitted_at.isnot(None),
    ).count()


def can_attempt(user, assessment: Assessment):
    if user.is_staff or not assessment.max_attempts:
        return True
    if Attempt.query.filter_by(user_id=user.id, assessment_id=assessment.id, passed=True).first():
        return False
    return attempts_used(user, assessment) < assessment.max_attempts
