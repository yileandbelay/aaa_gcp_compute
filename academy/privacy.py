"""Download my data, delete my account.

Deleting does not remove the database row: payment records must stay for accounting and
certificates must stay verifiable. Instead every personal detail is erased from the row and the
account is closed; the certificate page then shows "holder details removed on request".
"""
import os
import secrets

from flask import current_app, url_for

from .models import Certificate, Invitation, LoginAttempt, Payment, db, utcnow


def export_user_data(user):
    """Everything we hold about one person, as plain data for a JSON download."""
    def day(value):
        return value.isoformat(timespec="seconds") if value else None

    return {
        "exported_at": day(utcnow()),
        "profile": {"full_name": user.full_name, "email": user.email, "phone": user.phone, "city": user.city,
                    "headline": user.headline, "bio": user.bio, "roles": user.role_names, "joined": day(user.created_at)},
        "enrolments": [{"course": e.course.title, "status": e.status, "how": e.source, "since": day(e.activated_at or e.created_at)}
                       for e in user.enrollments],
        "payments": [{"reference": p.tx_ref, "course": p.course.title, "amount": str(p.amount), "currency": p.currency,
                      "method": p.method, "how": p.channel_label, "status": p.status, "transaction_id": p.provider_ref,
                      "created": day(p.created_at), "paid": day(p.paid_at), "has_receipt_file": bool(p.receipt_file)}
                     for p in Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at)],
        "certificates": [{"code": c.code, "course": c.course.title, "issued": day(c.issued_at),
                          "verify_at": url_for("main.certificate", code=c.code, _external=True)}
                         for c in Certificate.query.filter_by(user_id=user.id)],
    }


def erase_account(user):
    """Erase a person's details and close the account. The caller commits and signs them out."""
    old_email = user.email
    for payment in Payment.query.filter_by(user_id=user.id):
        if payment.receipt_file:
            try:
                os.remove(os.path.join(current_app.config["UPLOAD_DIR"], "receipts", os.path.basename(payment.receipt_file)))
            except OSError:
                pass
            payment.receipt_file = None
    Invitation.query.filter_by(email=old_email).delete()
    LoginAttempt.query.filter_by(email=old_email).delete()
    user.full_name = "Deleted user"
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.phone = user.city = user.headline = user.bio = None
    user.set_password(secrets.token_urlsafe(32))      # nobody can sign in again
    user.is_active_account = False
    user.is_student = False
    user.deleted_at = utcnow()
