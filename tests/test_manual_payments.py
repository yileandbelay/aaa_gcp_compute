"""Payments entered by an admin: cash, Telebirr, bank transfer, with an optional receipt."""
import io
import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.ledger import paid_total
from academy.models import Course, Enrollment, Payment, User, db
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads")})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr("academy.routes.admin.send_email",
                        lambda to, subject, body, reply_to=None: sent.append((to, subject, body)) or True)
    return sent


def login(c, email, password, **extra):
    return c.post("/login", data={"email": email, "password": password, **extra})


def admin_client(app):
    c = app.test_client()
    login(c, "admin@example.com", "admin12345", area="staff")
    return c


def make_student(app, email="payer@example.com", **extra):
    with app.app_context():
        u = User(email=email, full_name="Payer Student", **extra)
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()
        return u.id


def paid_course(app):
    with app.app_context():
        c = Course.query.filter(Course.price > 0).first()
        return c.id, Decimal(str(c.price))


def record(admin, uid, cid, **fields):
    data = {"user_id": uid, "course_id": cid, "amount": "1000", "channel": "cash", "confirm": "1"}
    data.update(fields)
    return admin.post("/admin/payments/record", data=data, follow_redirects=True)


def the_payment(app):
    with app.app_context():
        return Payment.query.order_by(Payment.id.desc()).first()


# --- Recording and confirming -----------------------------------------------------------------

def test_admin_records_a_confirmed_cash_payment_and_the_course_unlocks(app):
    uid = make_student(app)
    cid, fee = paid_course(app)
    r = record(admin_client(app), uid, cid, amount=str(fee), channel="cash", reference="slip 77", note="at the office")
    assert b"Payment recorded and confirmed" in r.data and b"paid in full" in r.data
    with app.app_context():
        p = Payment.query.one()
        assert (p.status, p.method, p.channel, p.provider_ref, p.note) == ("paid", "manual", "cash", "slip 77", "at the office")
        assert p.amount == fee and p.user_id == uid and p.course_id == cid
        assert p.recorded_by.email == "admin@example.com" and p.paid_at is not None
        enr = Enrollment.query.filter_by(user_id=uid, course_id=cid).one()
        assert enr.status == "active" and db.session.get(User, uid).has_access(db.session.get(Course, cid))


@pytest.mark.parametrize("channel", ["cash", "telebirr", "bank", "other"])
def test_every_channel_is_accepted(app, channel):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    record(admin_client(app), uid, cid, channel=channel)
    assert the_payment(app).channel == channel


def test_unconfirmed_payment_waits_for_approval_and_does_not_unlock(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    r = record(admin, uid, cid, confirm="")
    assert b"not confirmed yet" in r.data
    with app.app_context():
        p = Payment.query.one()
        assert p.status == "submitted" and Enrollment.query.filter_by(user_id=uid, course_id=cid).count() == 0
        pid = p.id
    admin.post(f"/admin/payments/{pid}/review", data={"decision": "approve"})
    with app.app_context():
        assert db.session.get(Payment, pid).status == "paid"
        assert Enrollment.query.filter_by(user_id=uid, course_id=cid).one().status == "active"


def test_student_found_by_email_when_no_id_is_given(app):
    make_student(app, "byemail@example.com")
    cid, _fee = paid_course(app)
    r = admin_client(app).post("/admin/payments/record", data={"email": " ByEmail@Example.com ", "course_id": cid,
                               "amount": "500", "channel": "bank", "confirm": "1"}, follow_redirects=True)
    assert b"Payment recorded" in r.data and the_payment(app).channel == "bank"


def test_partial_payments_add_up_and_show_the_balance(app):
    uid = make_student(app)
    cid, fee = paid_course(app)
    admin = admin_client(app)
    first = record(admin, uid, cid, amount="1000")
    assert b"still to pay" in first.data
    record(admin, uid, cid, amount=str(fee - 1000), channel="telebirr")
    with app.app_context():
        assert paid_total(uid, cid) == fee
    page = admin.get(f"/admin/courses/{cid}/students").data.decode()
    assert f"{fee:,.2f}" in page and "Paid</th>" in page
    assert f"collected so far <b>{fee:,.2f}" in page


def test_payment_date_can_be_in_the_past_but_not_the_future(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    past = (date.today() - timedelta(days=9)).isoformat()
    record(admin, uid, cid, paid_on=past)
    assert the_payment(app).paid_at.date().isoformat() == past
    future = (date.today() + timedelta(days=2)).isoformat()
    r = record(admin, uid, cid, paid_on=future)
    assert b"not in the future" in r.data
    with app.app_context():
        assert Payment.query.count() == 1


@pytest.mark.parametrize("fields,message", [
    ({"amount": "0"}, b"positive number"), ({"amount": "-5"}, b"positive number"),
    ({"amount": "abc"}, b"positive number"), ({"amount": ""}, b"positive number"),
    ({"channel": "gold"}, b"how the money was received"), ({"channel": ""}, b"how the money was received"),
    ({"paid_on": "31/12/2026"}, b"real date"),
])
def test_invalid_entries_are_rejected_and_save_nothing(app, fields, message):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    r = record(admin_client(app), uid, cid, **fields)
    assert message in r.data
    with app.app_context():
        assert Payment.query.count() == 0 and Enrollment.query.filter_by(user_id=uid).count() == 0


def test_unknown_email_staff_accounts_and_suspended_students_are_refused(app):
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    r = admin.post("/admin/payments/record", data={"email": "nobody@example.com", "course_id": cid, "amount": "100",
                                                   "channel": "cash"}, follow_redirects=True)
    assert b"No student account has that email" in r.data
    teacher = admin.post("/admin/payments/record", data={"email": "instructor@example.com", "course_id": cid,
                                                         "amount": "100", "channel": "cash"}, follow_redirects=True)
    assert b"No student account has that email" in teacher.data
    uid = make_student(app, "frozen@example.com", is_active_account=False)
    assert b"active student account" in record(admin, uid, cid).data
    with app.app_context():
        assert Payment.query.count() == 0


def test_missing_course_is_refused(app):
    uid = make_student(app)
    assert b"Choose the course" in record(admin_client(app), uid, "").data


# --- Receipts ---------------------------------------------------------------------------------

def test_receipt_upload_is_optional_and_stored_when_given(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    record(admin, uid, cid)
    assert the_payment(app).receipt_file is None
    record(admin, uid, cid, receipt=(io.BytesIO(b"\x89PNG fake"), "slip.png"))
    p = the_payment(app)
    assert p.receipt_file and p.receipt_file.endswith(".png")
    assert os.path.exists(os.path.join(app.config["UPLOAD_DIR"], "receipts", p.receipt_file))
    assert admin.get(f"/pay/receipt/{p.id}").status_code == 200


def test_receipt_with_a_dangerous_file_type_is_rejected(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    r = admin_client(app).post("/admin/payments/record", data={
        "user_id": uid, "course_id": cid, "amount": "100", "channel": "cash", "confirm": "1",
        "receipt": (io.BytesIO(b"<script>"), "evil.html")}, content_type="multipart/form-data")
    assert r.status_code == 400
    with app.app_context():
        assert Payment.query.count() == 0


def test_only_the_student_and_admins_can_open_a_receipt_or_invoice(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    record(admin_client(app), uid, cid, receipt=(io.BytesIO(b"\x89PNG fake"), "slip.png"))
    pid = the_payment(app).id
    owner = app.test_client()
    login(owner, "payer@example.com", "secret123", area="student")
    assert owner.get(f"/pay/receipt/{pid}").status_code == 200 and owner.get(f"/pay/invoice/{pid}").status_code == 200
    make_student(app, "other@example.com")
    other = app.test_client()
    login(other, "other@example.com", "secret123", area="student")
    assert other.get(f"/pay/receipt/{pid}").status_code == 404 and other.get(f"/pay/invoice/{pid}").status_code == 403
    teacher = app.test_client()
    login(teacher, "instructor@example.com", "instructor123", area="staff")
    assert teacher.get(f"/pay/receipt/{pid}").status_code == 404 and teacher.get(f"/pay/invoice/{pid}").status_code == 403


# --- Who may do it ----------------------------------------------------------------------------

def test_teachers_and_guests_cannot_record_payments(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    teacher = app.test_client()
    login(teacher, "instructor@example.com", "instructor123", area="staff")
    assert teacher.post("/admin/payments/record", data={"user_id": uid, "course_id": cid, "amount": "100",
                                                        "channel": "cash", "confirm": "1"}).status_code == 403
    assert app.test_client().post("/admin/payments/record", data={}).status_code in (302, 400, 401, 403)
    with app.app_context():
        assert Payment.query.count() == 0


def test_teachers_do_not_see_amounts_paid_in_their_course(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    record(admin_client(app), uid, cid)
    with app.app_context():
        db.session.get(Course, cid).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
    teacher = app.test_client()
    login(teacher, "instructor@example.com", "instructor123", area="staff")
    page = teacher.get(f"/admin/courses/{cid}/students").data.decode()
    assert "Payer Student" in page and "Paid</th>" not in page and "record-payment" not in page
    assert "collected so far" not in page


# --- Notifications, redirects and screens -----------------------------------------------------

def test_student_is_emailed_only_when_confirmed_and_asked(app, outbox):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    record(admin, uid, cid, notify="1")
    assert len(outbox) == 1 and outbox[0][0] == "payer@example.com" and "Payment received" in outbox[0][1]
    assert "now unlocked" in outbox[0][2] or "is now unlocked" in outbox[0][2]
    record(admin, uid, cid)                                  # no notify box ticked
    record(admin, uid, cid, notify="1", confirm="")          # not confirmed yet
    assert len(outbox) == 1


def test_redirect_stays_inside_the_admin_area(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    r = admin.post("/admin/payments/record", data={"user_id": uid, "course_id": cid, "amount": "100",
                   "channel": "cash", "next": "https://evil.example/steal"})
    assert r.headers["Location"].endswith("/admin/payments")
    r = admin.post("/admin/payments/record", data={"user_id": uid, "course_id": cid, "amount": "100",
                   "channel": "cash", "next": f"/admin/users/{uid}"})
    assert r.headers["Location"].endswith(f"/admin/users/{uid}")


def test_record_form_appears_on_the_payments_student_and_course_pages(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    for path in ("/admin/payments", f"/admin/users/{uid}", f"/admin/courses/{cid}/students"):
        page = admin.get(path).data.decode()
        assert 'id="record-payment"' in page and 'name="channel"' in page and 'type="file"' in page, path
        assert "Telebirr" in page and "Bank transfer" in page and "Payment confirmed" in page, path
    assert 'list="rp-emails"' in admin.get("/admin/payments").data.decode()
    assert f'name="user_id" value="{uid}"' in admin.get(f"/admin/users/{uid}").data.decode()
    assert f'name="course_id" value="{cid}"' in admin.get(f"/admin/courses/{cid}/students").data.decode()


def test_course_page_can_prefill_the_student(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    page = admin_client(app).get(f"/admin/courses/{cid}/students?student={uid}").data.decode()
    assert 'value="payer@example.com"' in page


def test_manual_payments_count_as_revenue_and_export(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    admin = admin_client(app)
    record(admin, uid, cid, amount="1234.50", channel="telebirr", reference="TX99")
    assert b"1,234.50" in admin.get("/admin/").data
    csv_text = admin.get("/admin/payments.csv").data.decode()
    assert "Telebirr" in csv_text and "TX99" in csv_text and "1234.50" in csv_text and "Payer Student" in csv_text
    assert "Payer" not in app.test_client().get("/admin/payments.csv").data.decode()


def test_student_sees_how_they_paid_in_both_languages(app):
    uid = make_student(app)
    cid, _fee = paid_course(app)
    record(admin_client(app), uid, cid, channel="cash")
    c = app.test_client()
    login(c, "payer@example.com", "secret123", area="student")
    assert "Cash" in c.get("/dashboard").data.decode()
    c.set_cookie("lang", "am")
    page = c.get("/dashboard").data.decode()
    assert "ጥሬ ገንዘብ" in page
