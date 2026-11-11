"""Enrollment & payments: free enroll, Chapa checkout, manual receipt upload."""
import os

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user, login_required

from ..models import Course, Enrollment, Payment, db
from ..payments import PaymentError, chapa_enabled, chapa_initialize, chapa_verify
from ..utils import save_upload

bp = Blueprint("pay", __name__, url_prefix="/pay")


def _published_course(course_id):
    course = db.get_or_404(Course, course_id)
    if not course.is_published:
        abort(404)
    return course


def _ensure_enrollment(course):
    enr = current_user.enrollment_for(course)
    if not enr:
        enr = Enrollment(user_id=current_user.id, course_id=course.id, status="pending")
        db.session.add(enr)
    return enr


@bp.route("/course/<int:course_id>", methods=["GET", "POST"])
@login_required
def checkout(course_id):
    course = _published_course(course_id)
    if current_user.has_access(course):
        return redirect(url_for("main.course_detail", slug=course.slug))
    if course.is_free and request.method == "GET":
        return redirect(url_for("main.course_detail", slug=course.slug))
    enr = _ensure_enrollment(course)
    if course.is_free:
        enr.activate()
        db.session.commit()
        flash(f"You are enrolled in {course.title}. Enjoy!", "success")
        return redirect(url_for("main.course_detail", slug=course.slug))
    db.session.commit()
    return render_template(
        "pay/checkout.html", course=course, chapa=chapa_enabled(),
        manual=current_app.config["MANUAL_PAYMENT_ENABLED"],
        demo=current_app.config["PAYMENT_DEMO_MODE"],
        instructions=current_app.config["MANUAL_PAYMENT_INSTRUCTIONS"],
    )


def _new_payment(course, method):
    p = Payment(user_id=current_user.id, course_id=course.id, amount=course.price,
                currency=current_app.config["CURRENCY"], method=method)
    db.session.add(p)
    return p


@bp.route("/course/<int:course_id>/chapa", methods=["POST"])
@login_required
def chapa_start(course_id):
    course = _published_course(course_id)
    if not chapa_enabled():
        abort(404)
    payment = _new_payment(course, "chapa")
    db.session.commit()
    try:
        url = chapa_initialize(
            payment, current_user,
            return_url=url_for("pay.chapa_return", tx_ref=payment.tx_ref, _external=True),
            callback_url=url_for("pay.chapa_webhook", _external=True),
        )
    except PaymentError as exc:
        payment.status = "failed"
        payment.note = str(exc)[:500]
        db.session.commit()
        flash(f"Payment could not be started: {exc}", "error")
        return redirect(url_for("pay.checkout", course_id=course.id))
    return redirect(url)


def _verify_and_activate(payment):
    if payment.status == "paid":
        return True
    ref = chapa_verify(payment)
    if ref:
        payment.mark_paid(provider_ref=ref)
        db.session.commit()
        return True
    return False


@bp.route("/chapa/return/<tx_ref>")
@login_required
def chapa_return(tx_ref):
    payment = Payment.query.filter_by(tx_ref=tx_ref).first_or_404()
    if payment.user_id != current_user.id:
        abort(403)
    if _verify_and_activate(payment):
        flash("Payment received - your course is unlocked!", "success")
    else:
        flash("We could not confirm your payment yet. If you were charged, it will "
              "activate automatically shortly, or contact us with reference " + payment.tx_ref, "error")
    return redirect(url_for("main.course_detail", slug=payment.course.slug))


@bp.route("/chapa/webhook", methods=["GET", "POST"])
def chapa_webhook():
    """Server-to-server notification. We never trust the payload itself -
    we look up the tx_ref and verify it with Chapa's API."""
    data = request.get_json(silent=True) or request.values
    tx_ref = data.get("tx_ref") or data.get("trx_ref")
    payment = Payment.query.filter_by(tx_ref=tx_ref, method="chapa").first() if tx_ref else None
    if payment:
        _verify_and_activate(payment)
    return {"ok": True}


@bp.route("/course/<int:course_id>/manual", methods=["POST"])
@login_required
def manual_submit(course_id):
    course = _published_course(course_id)
    if not current_app.config["MANUAL_PAYMENT_ENABLED"]:
        abort(404)
    receipt = request.files.get("receipt")
    reference = request.form.get("reference", "").strip()
    if not receipt or not receipt.filename:
        flash("Please attach a photo or PDF of your payment receipt.", "error")
        return redirect(url_for("pay.checkout", course_id=course.id))
    payment = _new_payment(course, "manual")
    payment.receipt_file = save_upload(receipt, "receipts", current_app.config["ALLOWED_RECEIPT_EXT"])
    payment.provider_ref = reference[:128] or None
    payment.status = "submitted"
    db.session.commit()
    flash("Receipt submitted. We will verify it and unlock your course shortly.", "success")
    return redirect(url_for("main.course_detail", slug=course.slug))


@bp.route("/course/<int:course_id>/demo", methods=["POST"])
@login_required
def demo_pay(course_id):
    """Simulated payment for local testing (PAYMENT_DEMO_MODE=true only)."""
    if not current_app.config["PAYMENT_DEMO_MODE"]:
        abort(404)
    course = _published_course(course_id)
    payment = _new_payment(course, "demo")
    payment.mark_paid(provider_ref="DEMO")
    db.session.commit()
    flash("Demo payment complete - course unlocked.", "success")
    return redirect(url_for("main.course_detail", slug=course.slug))


@bp.route("/receipt/<int:payment_id>")
@login_required
def receipt_file(payment_id):
    p = db.get_or_404(Payment, payment_id)
    if not p.receipt_file or (p.user_id != current_user.id and not current_user.is_staff):
        abort(404)
    return send_from_directory(os.path.join(current_app.config["UPLOAD_DIR"], "receipts"), p.receipt_file)


@bp.route("/invoice/<int:payment_id>")
@login_required
def invoice(payment_id):
    p = db.get_or_404(Payment, payment_id)
    if p.user_id != current_user.id and not current_user.is_staff:
        abort(403)
    return render_template("pay/invoice.html", p=p)
