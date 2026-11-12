"""Payments entered by an admin: cash, Telebirr, bank transfer.

Digital payments (Chapa) confirm themselves; this is the parallel path for money received
outside the website. Both end the same way: a ``paid`` Payment and an active enrollment.
"""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import func

from .models import Payment, db, utcnow
from .utils import save_upload

MAX_AMOUNT = Decimal("100000000")


def paid_total(user_id, course_id):
    """Confirmed money received from one student for one course."""
    total = (db.session.query(func.coalesce(func.sum(Payment.amount), 0))
             .filter(Payment.user_id == user_id, Payment.course_id == course_id, Payment.status == "paid")
             .scalar())
    return Decimal(str(total))


def paid_totals(course_id):
    """{user_id: confirmed amount} for everyone who paid for a course."""
    rows = (db.session.query(Payment.user_id, func.sum(Payment.amount))
            .filter(Payment.course_id == course_id, Payment.status == "paid")
            .group_by(Payment.user_id).all())
    return {uid: Decimal(str(total)) for uid, total in rows}


def parse_amount(text):
    try:
        amount = Decimal((text or "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return amount if Decimal("0") < amount <= MAX_AMOUNT else None


def parse_date(text):
    """A YYYY-MM-DD date that is not in the future; empty means today."""
    text = (text or "").strip()
    if not text:
        return date.today()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day if day <= date.today() else None


def record_payment(user, course, *, amount, channel, reference="", paid_on="", note="", receipt=None,
                   confirmed=True, by=None):
    """Create a manual payment. Returns ``(payment, error)``; the caller commits.

    ``confirmed`` marks it paid and unlocks the course at once; otherwise it waits as
    "submitted" in the payments list, to be approved later.
    """
    if not user or not user.can_study or not user.is_active_account:
        return None, "Choose an active student account."
    if channel not in dict(Payment.CHANNELS):
        return None, "Choose how the money was received: cash, Telebirr, bank transfer or other."
    value = parse_amount(amount)
    if value is None:
        return None, "Enter the amount received as a positive number, for example 2500 or 1250.50."
    day = parse_date(paid_on)
    if day is None:
        return None, "The payment date must be a real date and not in the future."
    payment = Payment(user_id=user.id, course_id=course.id, amount=value,
                      currency=current_app.config["CURRENCY"], method="manual", channel=channel,
                      status="submitted", note=(note or "").strip()[:500] or None,
                      recorded_by_id=by.id if by else None, reviewed_by_id=by.id if by else None)
    if receipt and receipt.filename:
        payment.receipt_file = save_upload(receipt, "receipts", current_app.config["ALLOWED_RECEIPT_EXT"])
    db.session.add(payment)
    if confirmed:
        payment.mark_paid(provider_ref=(reference or "").strip()[:128] or None)
        payment.paid_at = utcnow() if day == date.today() else datetime.combine(day, time(12, 0))
    elif reference:
        payment.provider_ref = reference.strip()[:128]
    return payment, None
