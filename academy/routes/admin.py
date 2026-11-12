"""Back-office.

Admins manage everything. Instructors ("course admins") manage only the courses they
teach: their content, quizzes, announcements and the students enrolled in them.
Students, staff accounts, payments and contact messages are admin-only.
"""
import csv
import io
import os
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename

from .. import videohosts
from ..enrollment import (create_invitation, enroll_student, notify_enrolled, parse_emails)
from ..ledger import paid_total, paid_totals, record_payment
from ..settings import (PAYMENT_INSTRUCTIONS, PAYMENT_INSTRUCTIONS_AM, get_setting, payment_instructions,
                        set_setting)
from ..mail import send_email
from ..reports import monthly_report, report_years
from ..reviews import rating_summary
from ..models import (Announcement, Assessment, Attachment, Attempt, Certificate, ContactMessage, Course,
                      Enrollment, Invitation, Lesson, LessonProgress, Payment, Question, Review, User, db, utcnow)
from ..utils import admin_required, course_progress, save_upload, slugify, staff_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

STAFF_ROLES = ("admin", "instructor")


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Who may manage what ----------------------------------------------------------

def _can_manage(course):
    return current_user.is_admin or course.instructor_id == current_user.id


def _manage(course):
    if not _can_manage(course):
        abort(403)
    return course


def _course(course_id):
    return _manage(db.get_or_404(Course, course_id))


def _lesson(lesson_id):
    lesson = db.get_or_404(Lesson, lesson_id)
    _manage(lesson.course)
    return lesson


def _assessment(aid):
    a = db.get_or_404(Assessment, aid)
    _manage(a.course)
    return a


def _apply_roles(user, level, student):
    """Set a person's staff level ("student" = none, "instructor", "admin") and student role.
    Returns an error message, or None when saved. Someone always keeps at least one role."""
    if level not in User.ROLES:
        return "Choose Student, Teacher or Admin."
    if level == "student":
        student = True  # no staff level: the student role is what is left
    user.role = level
    user.is_student = bool(student)
    return None


def _staff_users():
    return (User.query.filter(User.role.in_(STAFF_ROLES), User.is_active_account.is_(True))
            .order_by(User.full_name).all())


# --- Dashboard -----------------------------------------------------------------

@bp.route("/")
@staff_required
def dashboard():
    if current_user.is_admin:
        revenue = (db.session.query(func.coalesce(func.sum(Payment.amount), 0))
                   .filter(Payment.status == "paid").scalar())
        stats = {
            "students": User.query.filter(User.is_student.is_(True)).count(),
            "staff": User.query.filter(User.role.in_(STAFF_ROLES)).count(),
            "courses": Course.query.count(),
            "active_enrollments": Enrollment.query.filter_by(status="active").count(),
            "pending_payments": Payment.query.filter_by(status="submitted").count(),
            "revenue": revenue,
            "certificates": Certificate.query.count(),
            "messages": ContactMessage.query.filter_by(is_read=False).count(),
        }
        recent_students = User.query.filter(User.is_student.is_(True)).order_by(User.created_at.desc()).limit(8).all()
        recent_payments = Payment.query.order_by(Payment.created_at.desc()).limit(8).all()
    else:
        mine = Course.query.filter_by(instructor_id=current_user.id)
        ids = [c.id for c in mine]
        stats = {
            "courses": len(ids),
            "active_enrollments": (Enrollment.query.filter(Enrollment.course_id.in_(ids),
                                                           Enrollment.status == "active").count() if ids else 0),
            "certificates": (Certificate.query.filter(Certificate.course_id.in_(ids)).count() if ids else 0),
        }
        recent_students, recent_payments = [], []
    return render_template("admin/dashboard.html", stats=stats, recent_students=recent_students,
                           recent_payments=recent_payments)


# --- Courses -----------------------------------------------------------------

@bp.route("/courses")
@staff_required
def courses():
    query = Course.query
    if not current_user.is_admin:
        query = query.filter_by(instructor_id=current_user.id)
    return render_template("admin/courses.html", courses=query.order_by(Course.created_at.desc()).all())


def _categories():
    return [c for (c,) in db.session.query(Course.category).filter(Course.category.isnot(None)).distinct()
            .order_by(Course.category)]


def _fill_course(course, form):
    course.title = form.get("title", "").strip()
    course.summary = form.get("summary", "").strip()
    course.description = form.get("description", "")
    course.level = form.get("level", "Beginner")
    course.category = form.get("category", "").strip()[:60] or None
    try:
        course.hours = Decimal(form.get("hours")) if form.get("hours") else None
        if course.hours is not None and course.hours < 0:
            raise InvalidOperation
    except InvalidOperation:
        abort(400, "Invalid number of training hours")
    # Only admins set the price. A teacher's submission never changes it, and a course a teacher
    # creates starts free until an admin prices it.
    if current_user.is_admin:
        try:
            price = Decimal(form.get("price") or "0")
            if price < 0:
                raise InvalidOperation
        except InvalidOperation:
            abort(400, "Invalid price")
        pricing = form.get("pricing")  # "free", "paid", or absent for older forms
        if pricing == "free":
            price = Decimal("0")
        elif pricing == "paid" and price <= 0:
            abort(400, "A paid course needs a price above 0.")
        course.price = price
    elif course.price is None:
        course.price = Decimal("0")
    course.learn_points = form.get("learn_points", "").strip() or None
    course.audience = form.get("audience", "").strip() or None
    course.prerequisites = form.get("prerequisites", "").strip() or None
    course.is_published = bool(form.get("is_published"))
    if not course.title:
        abort(400, "Title is required")
    if not course.slug:
        base = slug = slugify(course.title)
        n = 2
        while Course.query.filter_by(slug=slug).first():
            slug = f"{base}-{n}"
            n += 1
        course.slug = slug
    # Only admins choose who teaches a course; instructors keep their own.
    if current_user.is_admin:
        teacher = db.session.get(User, _int(form.get("instructor_id")))
        if teacher and teacher.is_staff:
            course.instructor_id = teacher.id


@bp.route("/courses/new", methods=["GET", "POST"])
@staff_required
def course_new():
    if request.method == "POST":
        course = Course(instructor_id=current_user.id)
        _fill_course(course, request.form)
        db.session.add(course)
        db.session.commit()
        flash("Course created. Next, add its lessons below.", "success")
        return redirect(url_for("admin.course_edit", course_id=course.id) + "#lessons")
    return render_template("admin/course_form.html", course=None, categories=_categories(),
                           staff=_staff_users())


@bp.route("/courses/<int:course_id>", methods=["GET", "POST"])
@staff_required
def course_edit(course_id):
    course = _course(course_id)
    if request.method == "POST":
        _fill_course(course, request.form)
        db.session.commit()
        flash("Course saved.", "success")
        return redirect(url_for("admin.course_edit", course_id=course.id))
    enrolled = Enrollment.query.filter_by(course_id=course.id, status="active").count()
    return render_template("admin/course_form.html", course=course, categories=_categories(),
                           staff=_staff_users(), enrolled=enrolled, rating=rating_summary([course.id]).get(course.id))


@bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@admin_required
def course_delete(course_id):
    course = db.get_or_404(Course, course_id)
    if Payment.query.filter_by(course_id=course.id, status="paid").first():
        flash("This course has paid enrollments. Unpublish it instead of deleting.", "error")
        return redirect(url_for("admin.course_edit", course_id=course.id))
    for lesson in course.lessons:
        _delete_video(lesson.video_file)
        for att in lesson.attachments:
            _delete_material(att.stored_name)
        LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
    Payment.query.filter_by(course_id=course.id).delete()
    Certificate.query.filter_by(course_id=course.id).delete()
    Invitation.query.filter_by(course_id=course.id).delete()
    Review.query.filter_by(course_id=course.id).delete()
    db.session.delete(course)
    db.session.commit()
    flash("Course deleted.", "info")
    return redirect(url_for("admin.courses"))


@bp.route("/courses/<int:course_id>/announce", methods=["POST"])
@staff_required
def announce(course_id):
    if not current_app.config["FEATURE_ANNOUNCEMENTS"]:
        abort(404)
    course = _course(course_id)
    title = request.form.get("title", "").strip()
    if title:
        db.session.add(Announcement(course_id=course.id, title=title, body=request.form.get("body", "")))
        db.session.commit()
        flash("Announcement posted.", "success")
    return redirect(url_for("admin.course_edit", course_id=course.id) + "#announcements")


@bp.route("/reviews/<int:review_id>", methods=["POST"])
@staff_required
def review_moderate(review_id):
    """Hide, show or delete a student's review (admin or the course's instructor)."""
    review = db.get_or_404(Review, review_id)
    _manage(review.course)
    action = request.form.get("action")
    if action == "delete":
        db.session.delete(review)
    elif action == "hide":
        review.is_hidden = True
    elif action == "show":
        review.is_hidden = False
    db.session.commit()
    return redirect(url_for("admin.course_edit", course_id=review.course_id) + "#reviews")


@bp.route("/announcements/<int:ann_id>/delete", methods=["POST"])
@staff_required
def announcement_delete(ann_id):
    ann = db.get_or_404(Announcement, ann_id)
    _manage(ann.course)
    cid = ann.course_id
    db.session.delete(ann)
    db.session.commit()
    return redirect(url_for("admin.course_edit", course_id=cid) + "#announcements")


# --- Who is enrolled in a course (assigning courses to students) ------------------

@bp.route("/courses/<int:course_id>/students", methods=["GET", "POST"])
@staff_required
def course_students(course_id):
    course = _course(course_id)
    if request.method == "POST":
        _course_students_action(course)
        return redirect(url_for("admin.course_students", course_id=course.id))
    enrollments = [e for e in course.enrollments if e.user.can_study]
    enrollments.sort(key=lambda e: (e.status != "active", e.user.full_name.lower()))
    rows = [(e, course_progress(e.user, course)) for e in enrollments]
    enrolled_ids = {e.user_id for e in enrollments if e.status == "active"}
    # Browsing every student is for admins; instructors add people by email address.
    candidates = []
    if current_user.is_admin:
        candidates = [u for u in User.query.filter(User.is_student.is_(True), User.is_active_account.is_(True))
                      .order_by(User.full_name).limit(500) if u.id not in enrolled_ids]
    invitations = Invitation.query.filter_by(course_id=course.id).order_by(Invitation.created_at.desc()).all()
    extra = {}
    if current_user.is_admin:  # money is for admins only
        paid = paid_totals(course.id)
        pay_for = db.session.get(User, _int(request.args.get("student")))
        extra = {"paid": paid, "collected": sum(paid.values(), Decimal("0")),
                 "pay_for": pay_for if pay_for and pay_for.can_study else None, **_record_form_data()}
    return render_template("admin/course_students.html", course=course, rows=rows, candidates=candidates,
                           invitations=invitations, **extra)


def _course_students_action(course):
    f = request.form
    action = f.get("action")
    if action == "enroll":
        notify = bool(f.get("notify"))
        enrolled, already, invited, skipped = [], 0, [], []
        for uid in f.getlist("student_ids"):
            user = db.session.get(User, _int(uid))
            if user and user.can_study and user.is_active_account:
                if enroll_student(user, course, by=current_user):
                    enrolled.append(user)
                else:
                    already += 1
        emails, bad = parse_emails(f.get("emails", ""))
        for email in emails:
            user = User.query.filter_by(email=email).first()
            if user is None:
                if create_invitation(email, course, by=current_user, notify=notify):
                    invited.append(email)
                else:
                    already += 1
            elif user.can_study and user.is_active_account:
                if enroll_student(user, course, by=current_user):
                    enrolled.append(user)
                else:
                    already += 1
            else:
                skipped.append(email)
        db.session.commit()
        if notify:
            for user in enrolled:
                notify_enrolled(user, course)
        parts = []
        if enrolled:
            parts.append(f"{len(enrolled)} enrolled")
        if invited:
            parts.append(f"{len(invited)} invited (no account yet; they get the course when they register)")
        if already:
            parts.append(f"{already} already had access")
        if not parts and not (skipped or bad):
            parts.append("Nobody was selected")
        if parts:
            flash(". ".join(parts) + ".", "success" if (enrolled or invited) else "info")
        if skipped:
            flash("Skipped staff or suspended accounts: " + ", ".join(skipped), "error")
        if bad:
            flash("These do not look like email addresses: " + ", ".join(bad[:10]), "error")
    elif action in ("revoke", "activate", "remove"):
        enr = db.get_or_404(Enrollment, _int(f.get("enrollment_id")))
        if enr.course_id != course.id:
            abort(404)
        if action == "revoke":
            enr.status = "revoked"
            flash(f"Access removed for {enr.user.full_name}.", "info")
        elif action == "activate":
            enr.activate(source="assigned", by=current_user)
            flash(f"{enr.user.full_name} now has access.", "success")
        else:
            db.session.delete(enr)
            flash("Enrollment removed.", "info")
        db.session.commit()
    elif action == "cancel_invite":
        inv = db.get_or_404(Invitation, _int(f.get("invitation_id")))
        if inv.course_id != course.id:
            abort(404)
        db.session.delete(inv)
        db.session.commit()
        flash("Invitation cancelled.", "info")


# --- Lessons -------------------------------------------------------------------

def _delete_material(name):
    if name:
        path = os.path.join(current_app.config["UPLOAD_DIR"], "materials", name)
        if os.path.exists(path):
            os.remove(path)


def _delete_video(name):
    if name:
        path = os.path.join(current_app.config["UPLOAD_DIR"], "videos", name)
        if os.path.exists(path):
            os.remove(path)


def _clean_url(text):
    """http(s) URLs only (no javascript:, data:, ...). Returns None if invalid."""
    text = (text or "").strip()
    if text and "://" not in text and not text.startswith(("mailto:", "javascript:", "data:")):
        text = "https://" + text
    try:
        url = urlparse(text)
    except ValueError:
        return None
    if url.scheme not in ("http", "https") or not url.hostname or len(text) > 500:
        return None
    return text


def _add_links(lesson, text):
    """Add one resource link per line. Format: ``Title | https://address`` or just the address."""
    bad = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        title, _, address = line.rpartition("|") if "|" in line else ("", "", line)
        url = _clean_url(address)
        if not url:
            bad.append(line[:60])
            continue
        title = title.strip() or (urlparse(url).hostname or url).removeprefix("www.")
        lesson.attachments.append(Attachment(url=url, original_name=title[:255]))
    if bad:
        flash("These links were skipped because they are not web addresses: " + "; ".join(bad), "error")


@bp.route("/courses/<int:course_id>/lessons/new", methods=["GET", "POST"])
@bp.route("/lessons/<int:lesson_id>", methods=["GET", "POST"])
@staff_required
def lesson_form(course_id=None, lesson_id=None):
    lesson = _lesson(lesson_id) if lesson_id else None
    course = lesson.course if lesson else _course(course_id)
    if request.method == "POST":
        f = request.form
        if not lesson:
            lesson = Lesson(course_id=course.id, position=len(course.lessons) + 1)
            db.session.add(lesson)
        lesson.title = f.get("title", "").strip() or "Untitled lesson"
        lesson.kind = f.get("kind") if f.get("kind") in Lesson.TYPES else "text"
        if "body" in f:
            lesson.body = f.get("body", "")
        if "duration_min" in f:
            lesson.duration_min = _int(f.get("duration_min")) or None
        if "title" in f and "is_preview_present" in f:
            lesson.is_preview = bool(f.get("is_preview"))
        if lesson.kind == "video":
            _save_video_choice(lesson, f)
        for f_upload in request.files.getlist("materials"):
            if f_upload and f_upload.filename:
                stored = save_upload(f_upload, "materials", current_app.config["ALLOWED_MATERIAL_EXT"])
                path = os.path.join(current_app.config["UPLOAD_DIR"], "materials", stored)
                lesson.attachments.append(Attachment(
                    stored_name=stored, original_name=secure_filename(f_upload.filename) or stored,
                    size_bytes=os.path.getsize(path)))
        _add_links(lesson, f.get("links", ""))
        upload = request.files.get("video")
        if upload and upload.filename and lesson.kind == "video" and (lesson.video_provider or "local") == "local":
            new_name = save_upload(upload, "videos", current_app.config["ALLOWED_VIDEO_EXT"])
            _delete_video(lesson.video_file)
            lesson.video_file = new_name
        if lesson.kind == "video" and not lesson.has_video:
            flash("Video lesson saved without a video - add one to make it playable.", "info")
        db.session.commit()
        flash("Lesson saved.", "success")
        action = f.get("action")
        if action == "save_add":
            return redirect(url_for("admin.lesson_form", course_id=course.id))
        if action == "edit":  # quick-add from the course page: open the new lesson for editing
            return redirect(url_for("admin.lesson_form", lesson_id=lesson.id))
        return redirect(url_for("admin.course_edit", course_id=course.id) + "#lessons")

    current_host = videohosts.get(lesson.video_provider) if lesson else None
    host_status = None
    if current_host and current_host.enabled() and lesson.video_ref and current_host.key != "external":
        host_status = current_host.status(lesson.video_ref)
    hosts = videohosts.enabled_providers()
    # A lesson already using a provider that is no longer configured keeps it selectable.
    if current_host and current_host not in hosts:
        hosts.append(current_host)
    ref_value = ""
    if current_host and lesson.video_ref:
        ref_value = current_host.display_ref(lesson.video_ref)
    return render_template(
        "admin/lesson_form.html", course=course, lesson=lesson, hosts=hosts,
        enabled_keys=[h.key for h in videohosts.enabled_providers()],
        upload_keys=[h.key for h in videohosts.uploading_providers()],
        host_status=host_status, ref_value=ref_value, dict_help={h.key: h.help for h in hosts})


def _save_video_choice(lesson, f):
    provider = f.get("video_provider")
    if provider == "local":
        lesson.video_provider = "local"
        return
    host = videohosts.get(provider)
    if not host:
        return
    lesson.video_provider = provider
    ref = f.get("video_ref", "").strip()
    if not ref:
        lesson.video_ref = None
        return
    try:
        lesson.video_ref = host.normalize_ref(ref)
    except ValueError as exc:
        abort(400, str(exc))


@bp.route("/materials/<int:attachment_id>/delete", methods=["POST"])
@staff_required
def material_delete(attachment_id):
    att = db.get_or_404(Attachment, attachment_id)
    _manage(att.lesson.course)
    lesson_id = att.lesson_id
    _delete_material(att.stored_name)
    db.session.delete(att)
    db.session.commit()
    flash("Resource removed.", "info")
    return redirect(url_for("admin.lesson_form", lesson_id=lesson_id) + "#materials")


@bp.route("/video/<provider>/upload", methods=["POST"])
@staff_required
def video_upload_info(provider):
    """Create an empty video at the host and return what the browser needs to upload to it."""
    host = videohosts.get(provider)
    if not host or not host.enabled() or host.key == "external":
        return jsonify(error="That video host is not configured."), 400
    title = ((request.get_json(silent=True) or {}).get("title") or "Lesson video").strip()
    try:
        return jsonify(host.upload_info(title))
    except videohosts.VideoError as exc:
        current_app.logger.error("%s upload setup failed: %s", host.label, exc)
        return jsonify(error=str(exc)), 502


@bp.route("/video/<provider>/status")
@staff_required
def video_status(provider):
    """Processing status of an uploaded video, for the lesson editor."""
    host = videohosts.get(provider)
    ref = request.args.get("ref", "").strip()
    if not host or not host.enabled() or host.key == "external" or not host.valid_ref(ref):
        return jsonify(status=None)
    return jsonify(status=host.status(ref))


@bp.route("/lessons/<int:lesson_id>/delete", methods=["POST"])
@staff_required
def lesson_delete(lesson_id):
    lesson = _lesson(lesson_id)
    cid = lesson.course_id
    _delete_video(lesson.video_file)
    for att in lesson.attachments:
        _delete_material(att.stored_name)
    LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
    db.session.delete(lesson)
    db.session.commit()
    flash("Lesson deleted.", "info")
    return redirect(url_for("admin.course_edit", course_id=cid) + "#lessons")


@bp.route("/lessons/<int:lesson_id>/public", methods=["POST"])
@staff_required
def lesson_public(lesson_id):
    """Switch one lesson between public (anyone may watch) and locked (enrolled students only)."""
    lesson = _lesson(lesson_id)
    lesson.is_preview = not lesson.is_preview
    db.session.commit()
    flash(f"\"{lesson.title}\" is now {'public: anyone can watch it' if lesson.is_preview else 'locked: enrolled students only'}.",
          "success")
    return redirect(url_for("admin.course_edit", course_id=lesson.course_id) + "#lessons")


@bp.route("/lessons/<int:lesson_id>/move/<direction>", methods=["POST"])
@staff_required
def lesson_move(lesson_id, direction):
    lesson = _lesson(lesson_id)
    lessons = list(lesson.course.lessons)
    i = lessons.index(lesson)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(lessons):
        lessons[i], lessons[j] = lessons[j], lessons[i]
        for pos, l in enumerate(lessons, start=1):
            l.position = pos
        db.session.commit()
    return redirect(url_for("admin.course_edit", course_id=lesson.course_id) + "#lessons")


# --- Assessments ------------------------------------------------------------------

@bp.route("/courses/<int:course_id>/assessments/new", methods=["GET", "POST"])
@bp.route("/assessments/<int:aid>", methods=["GET", "POST"])
@staff_required
def assessment_form(course_id=None, aid=None):
    a = _assessment(aid) if aid else None
    course = a.course if a else _course(course_id)
    if request.method == "POST":
        f = request.form
        if not a:
            a = Assessment(course_id=course.id)
            db.session.add(a)
        a.title = f.get("title", "").strip() or "Quiz"
        a.instructions = f.get("instructions", "")
        a.pass_mark = max(0, min(100, _int(f.get("pass_mark"), 70)))
        a.max_attempts = max(0, _int(f.get("max_attempts"), 3))
        a.time_limit_min = max(0, _int(f.get("time_limit_min"), 0))
        a.is_final = bool(f.get("is_final"))
        db.session.commit()
        flash("Assessment saved.", "success")
        return redirect(url_for("admin.assessment_form", aid=a.id))
    return render_template("admin/assessment_form.html", course=course, a=a)


@bp.route("/assessments/<int:aid>/delete", methods=["POST"])
@staff_required
def assessment_delete(aid):
    a = _assessment(aid)
    cid = a.course_id
    db.session.delete(a)
    db.session.commit()
    flash("Assessment deleted.", "info")
    return redirect(url_for("admin.course_edit", course_id=cid) + "#assessments")


@bp.route("/assessments/<int:aid>/questions", methods=["POST"])
@bp.route("/questions/<int:qid>", methods=["POST"])
@staff_required
def question_save(aid=None, qid=None):
    q = db.get_or_404(Question, qid) if qid else None
    a = _assessment(q.assessment_id) if q else _assessment(aid)
    options = [line.strip() for line in request.form.get("options", "").splitlines() if line.strip()]
    correct = _int(request.form.get("correct"), 0) - 1
    text = request.form.get("text", "").strip()
    if not text or len(options) < 2 or not (0 <= correct < len(options)):
        flash("A question needs text, at least two options (one per line) and a valid correct option number.",
              "error")
        return redirect(url_for("admin.assessment_form", aid=a.id))
    if not q:
        q = Question(assessment_id=a.id, position=len(a.questions) + 1)
        db.session.add(q)
    q.text = text
    q.options = options
    q.correct_index = correct
    q.explanation = request.form.get("explanation", "")
    db.session.commit()
    flash("Question saved.", "success")
    return redirect(url_for("admin.assessment_form", aid=a.id) + "#questions")


@bp.route("/questions/<int:qid>/delete", methods=["POST"])
@staff_required
def question_delete(qid):
    q = db.get_or_404(Question, qid)
    _assessment(q.assessment_id)
    aid = q.assessment_id
    db.session.delete(q)
    db.session.commit()
    return redirect(url_for("admin.assessment_form", aid=aid) + "#questions")


@bp.route("/assessments/<int:aid>/results")
@staff_required
def assessment_results(aid):
    a = _assessment(aid)
    attempts = (Attempt.query.filter(Attempt.assessment_id == a.id, Attempt.submitted_at.isnot(None))
                .order_by(Attempt.submitted_at.desc()).all())
    return render_template("admin/assessment_results.html", a=a, attempts=attempts)


# --- Students (admin) --------------------------------------------------------

def _search(query, q):
    if q:
        like = f"%{q}%"
        query = query.filter((User.full_name.ilike(like)) | (User.email.ilike(like)) | (User.phone.ilike(like)))
    return query


@bp.route("/students")
@admin_required
def students():
    q = request.args.get("q", "").strip()
    users = _search(User.query.filter(User.is_student.is_(True)), q).order_by(User.created_at.desc()).all()
    return render_template("admin/students.html", users=users, q=q)


@bp.route("/students.csv")
@admin_required
def students_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "full_name", "email", "phone", "city", "registered", "active_courses"])
    for u in User.query.filter(User.is_student.is_(True)).order_by(User.id):
        active = ", ".join(e.course.title for e in u.enrollments if e.status == "active")
        w.writerow([u.id, u.full_name, u.email, u.phone or "", u.city or "",
                    u.created_at.strftime("%Y-%m-%d"), active])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=students.csv"})


# --- Staff: admins and instructors (admin) -------------------------------------

@bp.route("/staff")
@admin_required
def staff():
    users = User.query.filter(User.role.in_(STAFF_ROLES)).order_by(User.role, User.full_name).all()
    taught = dict(db.session.query(Course.instructor_id, func.count(Course.id)).group_by(Course.instructor_id).all())
    return render_template("admin/staff.html", users=users, taught=taught)


@bp.route("/staff/new", methods=["POST"])
@admin_required
def staff_new():
    from .auth import EMAIL_RE, make_reset_token

    f = request.form
    name = f.get("full_name", "").strip()
    email = f.get("email", "").strip().lower()
    role = f.get("role") if f.get("role") in STAFF_ROLES else "instructor"
    if len(name) < 2 or not EMAIL_RE.match(email):
        flash("Enter the person's full name and a valid email address.", "error")
        return redirect(url_for("admin.staff"))
    user = User.query.filter_by(email=email).first()
    if user and user.is_staff:
        flash(f"{email} already has a staff account.", "error")
        return redirect(url_for("admin.staff"))
    if user:  # an existing student account is promoted instead of duplicated; it stays a student too
        user.role = role
        flash(f"{user.full_name} already had a student account. They are now {User.ROLE_LABELS[role]} "
              f"and can still study as a student.", "success")
        db.session.commit()
        return redirect(url_for("admin.staff"))
    user = User(full_name=name, email=email, role=role, is_student=bool(f.get("also_student")))
    user.set_password(secrets.token_urlsafe(24))  # unusable until they set their own
    db.session.add(user)
    db.session.commit()
    link = url_for("auth.reset_password", token=make_reset_token(user, invite=True), _external=True)
    site = current_app.config["SITE_NAME"]
    days = max(1, current_app.config["INVITE_TTL"] // 86400)
    sent = send_email(email, f"Your {site} staff account",
                      f"Hello {name},\n\nAn account with the role '{User.ROLE_LABELS[role]}' was created for you on {site}.\n"
                      f"Choose your password within {days} days using this link:\n{link}\n\n{site}\n")
    if sent:
        flash(f"Account created for {name}. A link to set their password was emailed to {email}.", "success")
    else:  # email is not set up: show the link so the admin can pass it on
        flash(f"Account created for {name}, but email is not set up. Send them this link to set a password "
              f"(valid {days} days): {link}", "success")
    return redirect(url_for("admin.staff"))


# --- One user (student or staff) -----------------------------------------------

@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@bp.route("/students/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user_detail(user_id):
    user = db.get_or_404(User, user_id)
    if request.method == "POST":
        action = request.form.get("action")
        is_me = user.id == current_user.id
        if action == "roles":
            # Everyone may be given or lose the student role; only another admin changes the staff level.
            level = user.role if is_me else request.form.get("level", "student")
            error = _apply_roles(user, level, bool(request.form.get("student")))
            if not error and not user.is_student and user.role == "student":
                error = "Choose at least one role."
            if error:
                db.session.rollback()
                flash(error, "error")
                return redirect(url_for("admin.user_detail", user_id=user.id))
        elif action == "role" and not is_me:  # older single-role form
            role = request.form.get("role")
            if role in User.ROLES:
                _apply_roles(user, role, role == "student")
        elif action == "toggle_active" and not is_me:
            # Nobody can lock everyone out: the person acting is an active admin and cannot
            # change their own account, so at least one active admin always remains.
            user.is_active_account = not user.is_active_account
        elif action == "grant":
            course = db.get_or_404(Course, _int(request.form.get("course_id")))
            if enroll_student(user, course, by=current_user):
                if request.form.get("notify"):
                    db.session.commit()
                    notify_enrolled(user, course)
        elif action == "revoke":
            enr = db.get_or_404(Enrollment, _int(request.form.get("enrollment_id")))
            if enr.user_id == user.id:
                enr.status = "revoked"
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    rows = [(e, course_progress(user, e.course)) for e in user.enrollments]
    payments = Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at.desc()).all()
    active_ids = {e.course_id for e in user.enrollments if e.status == "active"}
    return render_template("admin/user_detail.html", user=user, rows=rows, payments=payments,
                           courses=[c for c in Course.query.order_by(Course.title) if c.id not in active_ids],
                           all_courses=Course.query.order_by(Course.title).all(),
                           paid={e.course_id: paid_total(user.id, e.course_id) for e in user.enrollments},
                           teaching=Course.query.filter_by(instructor_id=user.id).all())


# --- Payments (admin) --------------------------------------------------------

def _record_form_data():
    """What the "Record a payment" form needs: courses and the emails to suggest."""
    return {"courses": Course.query.order_by(Course.title).all(),
            "student_emails": [e for (e,) in db.session.query(User.email).filter(
                User.is_student.is_(True), User.is_active_account.is_(True)).order_by(User.email).limit(500)]}


@bp.route("/payments")
@admin_required
def payments():
    status = request.args.get("status", "")
    query = Payment.query
    if status:
        query = query.filter_by(status=status)
    return render_template("admin/payments.html", payments=query.order_by(Payment.created_at.desc()).all(),
                           status=status, sample_details=payment_instructions()[1], **_record_form_data())


@bp.route("/payments/details", methods=["GET", "POST"])
@admin_required
def payment_details():
    """The bank, Telebirr and cash details students see when they enroll in a paid course."""
    if request.method == "POST":
        set_setting(PAYMENT_INSTRUCTIONS, request.form.get("instructions", "")[:2000])
        set_setting(PAYMENT_INSTRUCTIONS_AM, request.form.get("instructions_am", "")[:2000])
        db.session.commit()
        flash("Payment details saved. Students see them on the enroll page.", "success")
        return redirect(url_for("admin.payment_details"))
    text, sample = payment_instructions()
    return render_template("admin/payment_details.html", sample=sample, shown=text,
                           instructions=get_setting(PAYMENT_INSTRUCTIONS),
                           instructions_am=get_setting(PAYMENT_INSTRUCTIONS_AM))


@bp.route("/payments.csv")
@admin_required
def payments_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "student", "email", "course", "amount", "currency", "method", "how", "reference",
                "status", "entered_by"])
    for p in Payment.query.order_by(Payment.created_at.desc()):
        w.writerow([(p.paid_at or p.created_at).strftime("%Y-%m-%d"), p.user.full_name, p.user.email,
                    p.course.title, p.amount, p.currency, p.method, p.channel_label or "",
                    p.provider_ref or p.tx_ref, p.status, p.recorded_by.full_name if p.recorded_by else ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=payments.csv"})


def _back_to(default):
    """Where to go after recording: a page inside the admin area, never an outside address."""
    target = request.form.get("next", "")
    return target if target.startswith("/admin/") and "//" not in target else default


@bp.route("/payments/record", methods=["POST"])
@admin_required
def payment_record():
    """Enter money received outside the website (cash, Telebirr, bank transfer) for one student and
    one course, with an optional receipt, and confirm it so the course unlocks."""
    f = request.form
    back = _back_to(url_for("admin.payments"))
    user = db.session.get(User, _int(f.get("user_id"))) if f.get("user_id") else \
        User.query.filter_by(email=f.get("email", "").strip().lower()).first()
    course = db.session.get(Course, _int(f.get("course_id")))
    if not user or not user.can_study:
        flash("No student account has that email address. Ask them to create an account first.", "error")
        return redirect(back)
    if not course:
        flash("Choose the course the payment is for.", "error")
        return redirect(back)
    confirmed = bool(f.get("confirm"))
    payment, error = record_payment(
        user, course, amount=f.get("amount"), channel=f.get("channel"), reference=f.get("reference", ""),
        paid_on=f.get("paid_on", ""), note=f.get("note", ""), receipt=request.files.get("receipt"),
        confirmed=confirmed, by=current_user)
    if error:
        db.session.rollback()
        flash(error, "error")
        return redirect(back)
    db.session.commit()
    total, fee = paid_total(user.id, course.id), course.price or 0
    summary = f"{payment.amount:,.2f} {payment.currency} ({payment.channel_label.lower()}) from {user.full_name} for {course.title}."
    if confirmed:
        left = Decimal(str(fee)) - total
        flash(f"Payment recorded and confirmed: {summary} {user.full_name} now has access. "
              + (f"Paid so far {total:,.2f} of {Decimal(str(fee)):,.2f} {payment.currency}; "
                 f"{left:,.2f} still to pay." if left > 0 else "The course fee is paid in full."), "success")
        if f.get("notify"):
            link = url_for("main.course_detail", slug=course.slug, _external=True)
            site = current_app.config["SITE_NAME"]
            send_email(user.email, f"Payment received: {course.title}",
                       f"Hello {user.full_name},\n\nWe received your payment of {payment.amount:,.2f} "
                       f"{payment.currency} ({payment.channel_label.lower()}) for {course.title} "
                       f"(ref {payment.tx_ref}). The course is now unlocked:\n{link}\n\nEnjoy the course!\n{site}\n")
    else:
        flash(f"Payment saved but not confirmed yet: {summary} It waits in Payments until you approve it.", "info")
    return redirect(back)


@bp.route("/payments/<int:payment_id>/review", methods=["POST"])
@admin_required
def payment_review(payment_id):
    p = db.get_or_404(Payment, payment_id)
    decision = request.form.get("decision")
    p.reviewed_by_id = current_user.id
    p.note = request.form.get("note", "")[:500] or p.note
    if decision == "approve":
        p.mark_paid()
        flash(f"Payment {p.tx_ref} approved; {p.user.full_name} now has access.", "success")
    elif decision == "reject":
        p.status = "rejected"
        flash(f"Payment {p.tx_ref} rejected.", "info")
    db.session.commit()
    course_link = url_for("main.course_detail", slug=p.course.slug, _external=True)
    site = current_app.config["SITE_NAME"]
    if decision == "approve":
        send_email(p.user.email, f"You're enrolled: {p.course.title}",
                   f"Hello {p.user.full_name},\n\nYour payment (ref {p.tx_ref}) was confirmed and "
                   f"{p.course.title} is now unlocked:\n{course_link}\n\nEnjoy the course!\n{site}\n")
    elif decision == "reject":
        send_email(p.user.email, f"Payment not confirmed: {p.course.title}",
                   f"Hello {p.user.full_name},\n\nWe could not confirm your payment (ref {p.tx_ref})."
                   f"{' Reason: ' + p.note if p.note else ''}\n\nYou can submit a new receipt here:\n{course_link}\n"
                   f"\n{site}\n")
    return redirect(url_for("admin.payments", status=request.form.get("back_status", "")))


# --- Revenue and enrolment report (admin) -----------------------------------------

def _report_year():
    year = _int(request.args.get("year"), 0)
    return year if 2000 <= year <= 2100 else utcnow().year


@bp.route("/reports")
@admin_required
def reports():
    year = _report_year()
    rows, totals = monthly_report(year)
    return render_template("admin/reports.html", year=year, years=report_years(), rows=rows, totals=totals)


@bp.route("/reports.csv")
@admin_required
def reports_csv():
    year = _report_year()
    rows, totals = monthly_report(year)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["year", "month", "course", "confirmed_payments", "revenue", "currency", "new_enrolments"])
    for r in rows:
        w.writerow([year, r["month_name"], r["course"], r["payments"], f"{r['revenue']:.2f}",
                    current_app.config["CURRENCY"], r["enrolments"]])
    w.writerow([year, "TOTAL", "", totals["payments"], f"{totals['revenue']:.2f}", current_app.config["CURRENCY"],
                totals["enrolments"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=report-{year}.csv"})


# --- Contact messages (admin) -------------------------------------------------

@bp.route("/messages")
@admin_required
def messages():
    items = ContactMessage.query.order_by(ContactMessage.created_at.desc()).limit(300).all()
    return render_template("admin/messages.html", items=items)


@bp.route("/messages/<int:mid>", methods=["POST"])
@admin_required
def message_update(mid):
    m = db.get_or_404(ContactMessage, mid)
    action = request.form.get("action")
    if action == "delete":
        db.session.delete(m)
    elif action == "unread":
        m.is_read = False
    else:
        m.is_read = True
    db.session.commit()
    return redirect(url_for("admin.messages"))
