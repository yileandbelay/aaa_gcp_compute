"""Back-office: content management (staff) and students/payments (admin)."""
import csv
import io
import os
import re
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user
from sqlalchemy import func

from ..models import (Announcement, Assessment, Attempt, Certificate, Course, Enrollment, Lesson,
                      Payment, Question, User, db)
from ..mail import send_email
from ..utils import admin_required, course_progress, save_upload, slugify, staff_required
from ..video import VideoError, upload_credentials, vdocipher_enabled, video_status

VDO_ID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Dashboard -----------------------------------------------------------------

@bp.route("/")
@staff_required
def dashboard():
    revenue = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "paid").scalar()
    stats = {
        "students": User.query.filter_by(role="student").count(),
        "courses": Course.query.count(),
        "active_enrollments": Enrollment.query.filter_by(status="active").count(),
        "pending_payments": Payment.query.filter_by(status="submitted").count(),
        "revenue": revenue,
        "certificates": Certificate.query.count(),
    }
    recent_students = User.query.filter_by(role="student").order_by(User.created_at.desc()).limit(8).all()
    recent_payments = Payment.query.order_by(Payment.created_at.desc()).limit(8).all()
    return render_template("admin/dashboard.html", stats=stats, recent_students=recent_students,
                           recent_payments=recent_payments)


# --- Courses -----------------------------------------------------------------

@bp.route("/courses")
@staff_required
def courses():
    return render_template("admin/courses.html", courses=Course.query.order_by(Course.created_at.desc()).all())


def _fill_course(course, form):
    course.title = form.get("title", "").strip()
    course.summary = form.get("summary", "").strip()
    course.description = form.get("description", "")
    course.level = form.get("level", "Beginner")
    try:
        course.price = Decimal(form.get("price") or "0")
        if course.price < 0:
            raise InvalidOperation
    except InvalidOperation:
        abort(400, "Invalid price")
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


@bp.route("/courses/new", methods=["GET", "POST"])
@staff_required
def course_new():
    if request.method == "POST":
        course = Course(instructor_id=current_user.id)
        _fill_course(course, request.form)
        db.session.add(course)
        db.session.commit()
        flash("Course created. Now add lessons and assessments.", "success")
        return redirect(url_for("admin.course_edit", course_id=course.id))
    return render_template("admin/course_form.html", course=None)


@bp.route("/courses/<int:course_id>", methods=["GET", "POST"])
@staff_required
def course_edit(course_id):
    course = db.get_or_404(Course, course_id)
    if request.method == "POST":
        _fill_course(course, request.form)
        db.session.commit()
        flash("Course saved.", "success")
        return redirect(url_for("admin.course_edit", course_id=course.id))
    return render_template("admin/course_form.html", course=course)


@bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@admin_required
def course_delete(course_id):
    course = db.get_or_404(Course, course_id)
    if Payment.query.filter_by(course_id=course.id, status="paid").first():
        flash("This course has paid enrollments. Unpublish it instead of deleting.", "error")
        return redirect(url_for("admin.course_edit", course_id=course.id))
    Payment.query.filter_by(course_id=course.id).delete()
    Certificate.query.filter_by(course_id=course.id).delete()
    db.session.delete(course)
    db.session.commit()
    flash("Course deleted.", "info")
    return redirect(url_for("admin.courses"))


@bp.route("/courses/<int:course_id>/students")
@staff_required
def course_students(course_id):
    course = db.get_or_404(Course, course_id)
    rows = [(e, course_progress(e.user, course)) for e in course.enrollments]
    return render_template("admin/course_students.html", course=course, rows=rows)


@bp.route("/courses/<int:course_id>/announce", methods=["POST"])
@staff_required
def announce(course_id):
    course = db.get_or_404(Course, course_id)
    title = request.form.get("title", "").strip()
    if title:
        db.session.add(Announcement(course_id=course.id, title=title, body=request.form.get("body", "")))
        db.session.commit()
        flash("Announcement posted.", "success")
    return redirect(url_for("admin.course_edit", course_id=course.id) + "#announcements")


@bp.route("/announcements/<int:ann_id>/delete", methods=["POST"])
@staff_required
def announcement_delete(ann_id):
    ann = db.get_or_404(Announcement, ann_id)
    cid = ann.course_id
    db.session.delete(ann)
    db.session.commit()
    return redirect(url_for("admin.course_edit", course_id=cid) + "#announcements")


# --- Lessons -------------------------------------------------------------------

def _delete_video(name):
    if name:
        path = os.path.join(current_app.config["UPLOAD_DIR"], "videos", name)
        if os.path.exists(path):
            os.remove(path)


@bp.route("/courses/<int:course_id>/lessons/new", methods=["GET", "POST"])
@bp.route("/lessons/<int:lesson_id>", methods=["GET", "POST"])
@staff_required
def lesson_form(course_id=None, lesson_id=None):
    lesson = db.get_or_404(Lesson, lesson_id) if lesson_id else None
    course = lesson.course if lesson else db.get_or_404(Course, course_id)
    if request.method == "POST":
        f = request.form
        if not lesson:
            lesson = Lesson(course_id=course.id, position=len(course.lessons) + 1)
            db.session.add(lesson)
        lesson.title = f.get("title", "").strip() or "Untitled lesson"
        lesson.kind = f.get("kind") if f.get("kind") in Lesson.TYPES else "text"
        lesson.body = f.get("body", "")
        lesson.duration_min = _int(f.get("duration_min")) or None
        lesson.is_preview = bool(f.get("is_preview"))
        provider = f.get("video_provider") if f.get("video_provider") in ("local", "vdocipher") else None
        if provider:
            lesson.video_provider = provider
        vdo_id = f.get("vdocipher_id", "").strip()
        if vdo_id and not VDO_ID_RE.match(vdo_id):
            abort(400, "That does not look like a VdoCipher video ID (letters and digits only).")
        lesson.vdocipher_id = vdo_id or None
        upload = request.files.get("video")
        if upload and upload.filename:
            new_name = save_upload(upload, "videos", current_app.config["ALLOWED_VIDEO_EXT"])
            _delete_video(lesson.video_file)
            lesson.video_file = new_name
        if lesson.kind == "video" and not lesson.has_video:
            flash("Video lesson saved without a video - add one to make it playable.", "info")
        db.session.commit()
        flash("Lesson saved.", "success")
        return redirect(url_for("admin.course_edit", course_id=course.id) + "#lessons")
    vdo_status = None
    if lesson and lesson.vdocipher_id and vdocipher_enabled():
        vdo_status = video_status(lesson.vdocipher_id)
    return render_template("admin/lesson_form.html", course=course, lesson=lesson,
                           vdocipher=vdocipher_enabled(), vdo_status=vdo_status)


@bp.route("/vdocipher/upload-credentials", methods=["POST"])
@staff_required
def vdocipher_credentials():
    """Give the browser a one-time form to upload a video straight to VdoCipher."""
    if not vdocipher_enabled():
        return jsonify(error="VdoCipher is not configured."), 400
    title = ((request.get_json(silent=True) or {}).get("title") or "Kasina lesson").strip()
    try:
        return jsonify(upload_credentials(title))
    except VideoError as exc:
        return jsonify(error=str(exc)), 502


@bp.route("/lessons/<int:lesson_id>/delete", methods=["POST"])
@staff_required
def lesson_delete(lesson_id):
    lesson = db.get_or_404(Lesson, lesson_id)
    cid = lesson.course_id
    _delete_video(lesson.video_file)
    from ..models import LessonProgress

    LessonProgress.query.filter_by(lesson_id=lesson.id).delete()
    db.session.delete(lesson)
    db.session.commit()
    flash("Lesson deleted.", "info")
    return redirect(url_for("admin.course_edit", course_id=cid) + "#lessons")


@bp.route("/lessons/<int:lesson_id>/move/<direction>", methods=["POST"])
@staff_required
def lesson_move(lesson_id, direction):
    lesson = db.get_or_404(Lesson, lesson_id)
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
    a = db.get_or_404(Assessment, aid) if aid else None
    course = a.course if a else db.get_or_404(Course, course_id)
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
    a = db.get_or_404(Assessment, aid)
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
    a = q.assessment if q else db.get_or_404(Assessment, aid)
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
    aid = q.assessment_id
    db.session.delete(q)
    db.session.commit()
    return redirect(url_for("admin.assessment_form", aid=aid) + "#questions")


@bp.route("/assessments/<int:aid>/results")
@staff_required
def assessment_results(aid):
    a = db.get_or_404(Assessment, aid)
    attempts = (Attempt.query.filter(Attempt.assessment_id == a.id, Attempt.submitted_at.isnot(None))
                .order_by(Attempt.submitted_at.desc()).all())
    return render_template("admin/assessment_results.html", a=a, attempts=attempts)


# --- Students (admin) --------------------------------------------------------

@bp.route("/students")
@admin_required
def students():
    q = request.args.get("q", "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter((User.full_name.ilike(like)) | (User.email.ilike(like)) | (User.phone.ilike(like)))
    users = query.order_by(User.created_at.desc()).all()
    return render_template("admin/students.html", users=users, q=q)


@bp.route("/students.csv")
@admin_required
def students_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "full_name", "email", "phone", "city", "role", "registered", "active_courses"])
    for u in User.query.order_by(User.id):
        active = ", ".join(e.course.title for e in u.enrollments if e.status == "active")
        w.writerow([u.id, u.full_name, u.email, u.phone or "", u.city or "", u.role,
                    u.created_at.strftime("%Y-%m-%d"), active])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=students.csv"})


@bp.route("/students/<int:user_id>", methods=["GET", "POST"])
@admin_required
def student_detail(user_id):
    user = db.get_or_404(User, user_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "role" and user.id != current_user.id:
            role = request.form.get("role")
            if role in User.ROLES:
                user.role = role
        elif action == "toggle_active" and user.id != current_user.id:
            user.is_active_account = not user.is_active_account
        elif action == "grant":
            course = db.get_or_404(Course, _int(request.form.get("course_id")))
            enr = user.enrollment_for(course) or Enrollment(user_id=user.id, course_id=course.id)
            db.session.add(enr)
            enr.activate()
        elif action == "revoke":
            enr = db.get_or_404(Enrollment, _int(request.form.get("enrollment_id")))
            if enr.user_id == user.id:
                enr.status = "revoked"
        db.session.commit()
        flash("Student updated.", "success")
        return redirect(url_for("admin.student_detail", user_id=user.id))
    rows = [(e, course_progress(user, e.course)) for e in user.enrollments]
    payments = Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at.desc()).all()
    return render_template("admin/student_detail.html", user=user, rows=rows, payments=payments,
                           courses=Course.query.order_by(Course.title).all())


# --- Payments (admin) --------------------------------------------------------

@bp.route("/payments")
@admin_required
def payments():
    status = request.args.get("status", "")
    query = Payment.query
    if status:
        query = query.filter_by(status=status)
    return render_template("admin/payments.html", payments=query.order_by(Payment.created_at.desc()).all(),
                           status=status)


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
    if decision == "approve":
        send_email(p.user.email, f"You're enrolled: {p.course.title}",
                   f"Hello {p.user.full_name},\n\nYour payment (ref {p.tx_ref}) was confirmed and "
                   f"{p.course.title} is now unlocked:\n{course_link}\n\nHappy baking!\nKasina Cakery Academy\n")
    elif decision == "reject":
        send_email(p.user.email, f"Payment not confirmed: {p.course.title}",
                   f"Hello {p.user.full_name},\n\nWe could not confirm your payment (ref {p.tx_ref})."
                   f"{' Reason: ' + p.note if p.note else ''}\n\nYou can submit a new receipt here:\n{course_link}\n"
                   "\nKasina Cakery Academy\n")
    return redirect(url_for("admin.payments", status=request.form.get("back_status", "")))
