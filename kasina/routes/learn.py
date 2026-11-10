"""Protected lecture viewing: text & video lessons, progress, Q&A."""
import os

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user, login_required

from ..models import Comment, Lesson, LessonProgress, db
from ..utils import course_progress, make_video_token, maybe_issue_certificate, verify_video_token

bp = Blueprint("learn", __name__, url_prefix="/learn")


def _lesson_or_403(lesson_id):
    lesson = db.get_or_404(Lesson, lesson_id)
    if lesson.is_preview and lesson.course.is_published:
        return lesson
    if not current_user.is_authenticated:
        abort(403)
    if not current_user.has_access(lesson.course):
        abort(403)
    return lesson


@bp.route("/lesson/<int:lesson_id>")
def lesson(lesson_id):
    lesson = db.get_or_404(Lesson, lesson_id)
    if not (lesson.is_preview and lesson.course.is_published):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not current_user.has_access(lesson.course):
            flash("Enroll in this course to unlock this lesson.", "info")
            return redirect(url_for("main.course_detail", slug=lesson.course.slug))

    course = lesson.course
    lessons = course.lessons
    idx = lessons.index(lesson)
    prev_l = lessons[idx - 1] if idx > 0 else None
    next_l = lessons[idx + 1] if idx + 1 < len(lessons) else None
    video_url = None
    progress = None
    if current_user.is_authenticated:
        if lesson.kind == "video" and lesson.video_file:
            token = make_video_token(current_user.id, lesson.id)
            video_url = url_for("learn.video", lesson_id=lesson.id, t=token)
        if current_user.has_access(course):
            progress = course_progress(current_user, course)
    return render_template(
        "learn/lesson.html", lesson=lesson, course=course, lessons=lessons,
        prev_l=prev_l, next_l=next_l, video_url=video_url, progress=progress,
    )


@bp.route("/lesson/<int:lesson_id>/video")
@login_required
def video(lesson_id):
    """Stream a lecture video. Requires a short-lived token bound to this user
    AND an access check, so copied links do not work for others or for long."""
    lesson = _lesson_or_403(lesson_id)
    if not lesson.video_file or not verify_video_token(request.args.get("t", ""), current_user.id, lesson.id):
        abort(403)
    folder = os.path.join(current_app.config["UPLOAD_DIR"], "videos")
    resp = send_from_directory(folder, lesson.video_file, conditional=True)  # supports Range/seek
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@bp.route("/lesson/<int:lesson_id>/complete", methods=["POST"])
@login_required
def complete(lesson_id):
    lesson = _lesson_or_403(lesson_id)
    if not current_user.has_access(lesson.course):
        abort(403)
    if not LessonProgress.query.filter_by(user_id=current_user.id, lesson_id=lesson.id).first():
        db.session.add(LessonProgress(user_id=current_user.id, lesson_id=lesson.id))
        db.session.commit()
    cert = maybe_issue_certificate(current_user, lesson.course)
    if cert and request.form.get("announce_cert", "1") == "1":
        flash("Congratulations! You completed the course - your certificate is ready.", "success")
    nxt = request.form.get("next_lesson")
    if nxt and nxt.isdigit():
        return redirect(url_for("learn.lesson", lesson_id=int(nxt)))
    return redirect(url_for("learn.lesson", lesson_id=lesson.id))


@bp.route("/lesson/<int:lesson_id>/comment", methods=["POST"])
@login_required
def comment(lesson_id):
    lesson = _lesson_or_403(lesson_id)
    if not current_user.has_access(lesson.course):
        abort(403)
    body = request.form.get("body", "").strip()
    if body:
        db.session.add(Comment(lesson_id=lesson.id, user_id=current_user.id, body=body[:4000]))
        db.session.commit()
    return redirect(url_for("learn.lesson", lesson_id=lesson.id) + "#discussion")


@bp.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    c = db.get_or_404(Comment, comment_id)
    if c.user_id != current_user.id and not current_user.is_staff:
        abort(403)
    lesson_id = c.lesson_id
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for("learn.lesson", lesson_id=lesson_id) + "#discussion")
