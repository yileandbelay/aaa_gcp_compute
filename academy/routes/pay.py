"""Enrollment & payments: free enroll, Chapa checkout, manual receipt upload."""
import os

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user, login_required

from ..i18n import gettext as _
from ..mail import send_email
from ..models import Course, Enrollment, Payment, User, db
from ..payments import PaymentError, chapa_enabled, chapa_initialize, chapa_verify
from ..settings import payment_instructions
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


def _already_has_access(course):
    """Staff and enrolled students do not pay. Used to stop payments being created by mistake."""
    if current_user.has_access(course):
        flash(_("You already have access to this course."), "info")
        return redirect(url_for("main.course_detail", slug=course.slug))
    return None


@bp.route("/course/<int:course_id>", methods=["GET", "POST"])
@login_required
def checkout(course_id):
    course = _published_course(course_id)
    # Staff see every course, so they cannot enroll; they may look at this page to see what students see.
    preview = current_user.is_staff and not course.is_free and request.method == "GET"
    if current_user.has_access(course) and not preview:
        return redirect(url_for("main.course_detail", slug=course.slug))
    if course.is_free and request.method == "GET":
        return redirect(url_for("main.course_detail", slug=course.slug))
    if not preview:
        enr = _ensure_enrollment(course)
        if course.is_free:
            enr.activate(source="free")
            db.session.commit()
            flash(_("You are enrolled in %(course)s. Enjoy!", course=course.title), "success")
            return redirect(url_for("main.course_detail", slug=course.slug))
        db.session.commit()
    instructions, _sample = payment_instructions()
    return render_template(
        "pay/checkout.html", course=course, chapa=chapa_enabled(), preview=preview,
        manual=current_app.config["MANUAL_PAYMENT_ENABLED"],
        demo=current_app.config["PAYMENT_DEMO_MODE"], instructions=instructions,
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
    if (blocked := _already_has_access(course)):
        return blocked
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
        flash(_("Payment could not be started: %(error)s", error=str(exc)), "error")
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
        flash(_("Payment received - your course is unlocked!"), "success")
    else:
        flash(_("We could not confirm your payment yet. If you were charged, it will activate automatically "
                "shortly, or contact us with reference %(ref)s", ref=payment.tx_ref), "error")
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


def _tell_admins_about_receipt(payment):
    """Email every active admin that a receipt is waiting. Never raises: a mail problem must not
    stop the student's upload, so failures are only logged."""
    try:
        link = url_for("admin.payments", status="submitted", _external=True)
        site = current_app.config["SITE_NAME"]
        body = (f"A payment receipt is waiting for review on {site}.\n\n"
                f"Student: {payment.user.full_name} ({payment.user.email})\n"
                f"Course: {payment.course.title}\n"
                f"Amount: {payment.amount:,.2f} {payment.currency}\n"
                f"Reference: {payment.provider_ref or payment.tx_ref}\n\n"
                f"Review it here:\n{link}\n")
        for admin in User.query.filter_by(role="admin", is_active_account=True).all():
            try:
                send_email(admin.email, f"Receipt to review: {payment.course.title}", body)
            except Exception:  # noqa: BLE001 - one bad address must not skip the others
                current_app.logger.exception("Could not email receipt notice to %s", admin.email)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Could not prepare the receipt notice for payment %s", payment.id)


@bp.route("/course/<int:course_id>/manual", methods=["POST"])
@login_required
def manual_submit(course_id):
    course = _published_course(course_id)
    if not current_app.config["MANUAL_PAYMENT_ENABLED"]:
        abort(404)
    if (blocked := _already_has_access(course)):
        return blocked
    receipt = request.files.get("receipt")
    reference = request.form.get("reference", "").strip()
    if not receipt or not receipt.filename:
        flash(_("Please attach a photo or PDF of your payment receipt."), "error")
        return redirect(url_for("pay.checkout", course_id=course.id))
    payment = _new_payment(course, "manual")
    payment.receipt_file = save_upload(receipt, "receipts", current_app.config["ALLOWED_RECEIPT_EXT"])
    payment.provider_ref = reference[:128] or None
    payment.status = "submitted"
    db.session.commit()
    _tell_admins_about_receipt(payment)
    flash(_("Receipt submitted. We will verify it and unlock your course shortly."), "success")
    return redirect(url_for("main.course_detail", slug=course.slug))


@bp.route("/course/<int:course_id>/demo", methods=["POST"])
@login_required
def demo_pay(course_id):
    """Simulated payment for local testing (PAYMENT_DEMO_MODE=true only)."""
    if not current_app.config["PAYMENT_DEMO_MODE"]:
        abort(404)
    course = _published_course(course_id)
    if (blocked := _already_has_access(course)):
        return blocked
    payment = _new_payment(course, "demo")
    payment.mark_paid(provider_ref="DEMO")
    db.session.commit()
    flash(_("Demo payment complete - course unlocked."), "success")
    return redirect(url_for("main.course_detail", slug=course.slug))


@bp.route("/receipt/<int:payment_id>")
@login_required
def receipt_file(payment_id):
    p = db.get_or_404(Payment, payment_id)
    if not p.receipt_file or (p.user_id != current_user.id and not current_user.is_admin):
        abort(404)
    return send_from_directory(os.path.join(current_app.config["UPLOAD_DIR"], "receipts"), p.receipt_file)


@bp.route("/invoice/<int:payment_id>")
@login_required
def invoice(payment_id):
    p = db.get_or_404(Payment, payment_id)
    if p.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template("pay/invoice.html", p=p)
