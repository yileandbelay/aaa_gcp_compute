"""Assessments (multiple-choice quizzes / exams) with auto-grading."""
import json
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..models import Assessment, Attempt, db, utcnow
from ..utils import attempts_used, can_attempt, maybe_issue_certificate

bp = Blueprint("assess", __name__, url_prefix="/assessments")

GRACE = timedelta(seconds=30)  # network slack for timed exams


def _assessment_for_user(aid):
    a = db.get_or_404(Assessment, aid)
    if not current_user.has_access(a.course):
        abort(403)
    return a


@bp.route("/<int:aid>")
@login_required
def overview(aid):
    a = _assessment_for_user(aid)
    attempts = (
        Attempt.query.filter(Attempt.user_id == current_user.id, Attempt.assessment_id == a.id,
                             Attempt.submitted_at.isnot(None))
        .order_by(Attempt.submitted_at.desc()).all()
    )
    return render_template(
        "learn/assessment.html", a=a, attempts=attempts, can_attempt=can_attempt(current_user, a),
        used=attempts_used(current_user, a),
    )


@bp.route("/<int:aid>/start", methods=["POST"])
@login_required
def start(aid):
    a = _assessment_for_user(aid)
    if not a.questions:
        flash("This assessment has no questions yet.", "info")
        return redirect(url_for("assess.overview", aid=a.id))
    if not can_attempt(current_user, a):
        flash("You have no attempts remaining for this assessment.", "error")
        return redirect(url_for("assess.overview", aid=a.id))
    # Reuse an open attempt so refreshing does not burn attempts or reset the timer.
    attempt = Attempt.query.filter_by(user_id=current_user.id, assessment_id=a.id, submitted_at=None).first()
    if not attempt:
        attempt = Attempt(user_id=current_user.id, assessment_id=a.id)
        db.session.add(attempt)
        db.session.commit()
    return redirect(url_for("assess.take", attempt_id=attempt.id))


@bp.route("/attempt/<int:attempt_id>", methods=["GET", "POST"])
@login_required
def take(attempt_id):
    attempt = db.get_or_404(Attempt, attempt_id)
    if attempt.user_id != current_user.id:
        abort(403)
    if attempt.submitted_at:
        return redirect(url_for("assess.result", attempt_id=attempt.id))
    a = attempt.assessment
    deadline = attempt.started_at + timedelta(minutes=a.time_limit_min) if a.time_limit_min else None

    if request.method == "POST":
        late = deadline is not None and utcnow() > deadline + GRACE
        answers, correct = {}, 0
        for q in a.questions:
            raw = request.form.get(f"q{q.id}")
            if raw is not None and raw.isdigit() and not late:
                answers[q.id] = int(raw)
                if int(raw) == q.correct_index:
                    correct += 1
        attempt.answers_json = json.dumps(answers)
        attempt.score = int(round(100 * correct / len(a.questions))) if a.questions else 0
        attempt.passed = attempt.score >= a.pass_mark
        attempt.submitted_at = utcnow()
        db.session.commit()
        if late:
            flash("Time limit exceeded - the attempt was submitted with no answers counted.", "error")
        if attempt.passed and maybe_issue_certificate(current_user, a.course):
            flash("Congratulations! You completed the course - your certificate is ready.", "success")
        return redirect(url_for("assess.result", attempt_id=attempt.id))

    remaining = int((deadline - utcnow()).total_seconds()) if deadline else None
    return render_template("learn/take.html", a=a, attempt=attempt, remaining=remaining)


@bp.route("/attempt/<int:attempt_id>/result")
@login_required
def result(attempt_id):
    attempt = db.get_or_404(Attempt, attempt_id)
    if attempt.user_id != current_user.id and not current_user.is_staff:
        abort(403)
    if not attempt.submitted_at:
        return redirect(url_for("assess.take", attempt_id=attempt.id))
    a = attempt.assessment
    # Only reveal correct answers once the student passed or used all attempts,
    # so the answer key cannot be harvested from a failed attempt.
    reveal = attempt.passed or current_user.is_staff or not can_attempt(attempt.user, a)
    return render_template("learn/result.html", attempt=attempt, a=a, reveal=reveal)
