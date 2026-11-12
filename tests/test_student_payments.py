"""The student's side of paying: choose a method at enroll time, upload an offline receipt, wait for
an admin to approve it, then find the course in My learning. Plus the admin-editable payment details."""
import io
import os

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Course, Enrollment, Payment, User, db
from academy.seed import seed

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def make_app(tmp_path, **extra):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads"), "PAYMENT_DEMO_MODE": False, **extra})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def login(c, email, password, **extra):
    return c.post("/login", data={"email": email, "password": password, **extra})


def student(app, email="stu@example.com"):
    c = app.test_client()
    c.post("/register", data={"full_name": "Abebe Kebede", "email": email, "password": "secret123",
                              "password2": "secret123", "agree": "1"})
    return c


def admin(app):
    c = app.test_client()
    login(c, "admin@example.com", "admin12345", area="staff")
    return c


def paid_course(app):
    with app.app_context():
        c = Course.query.filter(Course.price > 0).first()
        return c.id, c.slug


def submit(c, cid, name="slip.png", data=PNG, reference="FT123"):
    return c.post(f"/pay/course/{cid}/manual", data={"reference": reference, "receipt": (io.BytesIO(data), name)},
                  content_type="multipart/form-data", follow_redirects=True)


# --- What the student sees --------------------------------------------------------------------

def test_enroll_leads_to_a_page_with_the_payment_methods(app):
    cid, slug = paid_course(app)
    c = student(app)
    page = c.get(f"/pay/course/{cid}").data.decode()
    assert "Choose how to pay" in page and "Course fee:" in page
    assert 'type="file"' in page and 'name="receipt"' in page and "Submit receipt" in page
    assert "1000 1234 5678" in page and "0911 00 00 00" in page         # sample details until real ones are set
    assert "Pay online" not in page                                      # no digital payment connected yet


def test_online_and_offline_methods_are_offered_side_by_side(app, monkeypatch):
    monkeypatch.setattr("academy.routes.pay.chapa_enabled", lambda: True)
    cid, _ = paid_course(app)
    page = student(app).get(f"/pay/course/{cid}").data.decode()
    assert "Pay online" in page and "Submit receipt" in page and "Choose how to pay" in page


def test_a_guest_who_clicks_enroll_is_asked_to_log_in_first(app):
    cid, _ = paid_course(app)
    r = app.test_client().get(f"/pay/course/{cid}")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_no_method_available_says_so(tmp_path):
    app = make_app(tmp_path, MANUAL_PAYMENT_ENABLED=False)
    cid, _ = paid_course(app)
    page = student(app).get(f"/pay/course/{cid}").data.decode()
    assert "Online enrollment is not available right now" in page and "Choose how to pay" not in page


def test_checkout_in_amharic(app):
    cid, _ = paid_course(app)
    c = student(app)
    c.set_cookie("lang", "am")
    page = c.get(f"/pay/course/{cid}").data.decode()
    assert "እንዴት እንደሚከፍሉ ይምረጡ" in page and "Choose how to pay" not in page
    assert "የኢትዮጵያ ንግድ ባንክ" in page and "1000 1234 5678" in page


# --- Offline receipt, approval, My learning ---------------------------------------------------

def test_receipt_upload_then_admin_approval_adds_the_course_to_my_learning(app):
    cid, slug = paid_course(app)
    c = student(app)
    r = submit(c, cid)
    assert b"Receipt submitted" in r.data and b"being reviewed" in r.data
    with app.app_context():
        p = Payment.query.one()
        assert (p.status, p.method, p.provider_ref) == ("submitted", "manual", "FT123")
        assert os.path.exists(os.path.join(app.config["UPLOAD_DIR"], "receipts", p.receipt_file))
        pid = p.id
        user = User.query.filter_by(email="stu@example.com").one()
        assert not user.has_access(db.session.get(Course, cid))
    dash = c.get("/dashboard").data.decode()
    assert "Submitted" in dash and slug not in dash.split("Payments")[0]       # pending: not in My learning yet
    adm = admin(app)
    assert "Abebe Kebede" in adm.get("/admin/payments?status=submitted").data.decode()
    assert adm.get(f"/pay/receipt/{pid}").status_code == 200
    adm.post(f"/admin/payments/{pid}/review", data={"decision": "approve"})
    with app.app_context():
        assert db.session.get(Payment, pid).status == "paid"
        assert Enrollment.query.filter_by(course_id=cid).one().status == "active"
    assert slug in c.get("/dashboard").data.decode().split("Payments")[0]       # now in My learning


def test_a_rejected_receipt_can_be_replaced_by_a_new_one(app):
    cid, _ = paid_course(app)
    c = student(app)
    submit(c, cid)
    with app.app_context():
        pid = Payment.query.one().id
    admin(app).post(f"/admin/payments/{pid}/review", data={"decision": "reject", "note": "blurry"})
    assert c.get(f"/pay/course/{cid}").status_code == 200                   # can try again
    submit(c, cid, reference="FT999")
    with app.app_context():
        assert sorted(p.status for p in Payment.query.all()) == ["rejected", "submitted"]


def test_receipt_is_required_and_dangerous_files_are_refused(app):
    cid, _ = paid_course(app)
    c = student(app)
    r = c.post(f"/pay/course/{cid}/manual", data={"reference": "x"}, follow_redirects=True)
    assert b"Please attach a photo or PDF" in r.data
    r = c.post(f"/pay/course/{cid}/manual", data={"receipt": (io.BytesIO(b"<script>"), "evil.html")},
               content_type="multipart/form-data")
    assert r.status_code == 400
    with app.app_context():
        assert Payment.query.count() == 0


def test_free_courses_still_enroll_without_paying(app):
    with app.app_context():
        free = Course.query.filter(Course.price == 0).first()
        fid = free.id
    c = student(app)
    c.post(f"/pay/course/{fid}")
    with app.app_context():
        assert Enrollment.query.filter_by(course_id=fid).one().status == "active"
        assert Payment.query.count() == 0


# --- Staff cannot pay by accident, but can see the student's page -----------------------------

def test_admin_can_preview_the_student_payment_page_but_cannot_pay(app):
    cid, _ = paid_course(app)
    adm = admin(app)
    page = adm.get(f"/pay/course/{cid}")
    html = page.data.decode()
    assert page.status_code == 200 and "this is the page students see" in html.lower()
    assert html.count("disabled") >= 2 and 'name="receipt"' in html
    r = adm.post(f"/pay/course/{cid}/manual", data={"receipt": (io.BytesIO(PNG), "slip.png")},
                 content_type="multipart/form-data", follow_redirects=True)
    assert b"already have access" in r.data
    with app.app_context():
        assert Payment.query.count() == 0 and Enrollment.query.count() == 0


def test_enrolled_students_are_sent_back_to_the_course(app):
    cid, slug = paid_course(app)
    c = student(app)
    submit(c, cid)
    with app.app_context():
        pid = Payment.query.one().id
    admin(app).post(f"/admin/payments/{pid}/review", data={"decision": "approve"})
    r = c.get(f"/pay/course/{cid}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/courses/{slug}")


# --- Admin: the details students see ----------------------------------------------------------

def test_admin_is_warned_while_sample_details_are_shown(app):
    adm = admin(app)
    assert "sample" in adm.get("/admin/payments").data.decode()
    page = adm.get("/admin/payments/details").data.decode()
    assert "sample account numbers" in page and "1000 1234 5678" in page


def test_admin_sets_real_details_and_students_see_them_instead(app):
    cid, _ = paid_course(app)
    adm = admin(app)
    adm.post("/admin/payments/details", data={"instructions": "Dashen Bank 5551234\nTelebirr 0922334455",
                                              "instructions_am": "ዳሽን ባንክ 5551234"})
    assert "sample account numbers" not in adm.get("/admin/payments/details").data.decode()
    assert "sample" not in adm.get("/admin/payments").data.decode().lower().split("payment details shown")[1][:400]
    c = student(app)
    page = c.get(f"/pay/course/{cid}").data.decode()
    assert "Dashen Bank 5551234" in page and "1000 1234 5678" not in page
    c.set_cookie("lang", "am")
    assert "ዳሽን ባንክ 5551234" in c.get(f"/pay/course/{cid}").data.decode()


def test_only_english_text_is_used_for_amharic_visitors_when_no_amharic_is_given(app):
    cid, _ = paid_course(app)
    admin(app).post("/admin/payments/details", data={"instructions": "Dashen Bank 5551234"})
    c = student(app)
    c.set_cookie("lang", "am")
    assert "Dashen Bank 5551234" in c.get(f"/pay/course/{cid}").data.decode()


def test_emptying_the_details_brings_the_sample_back(app):
    cid, _ = paid_course(app)
    adm = admin(app)
    adm.post("/admin/payments/details", data={"instructions": "Dashen Bank 5551234"})
    adm.post("/admin/payments/details", data={"instructions": "", "instructions_am": ""})
    assert "1000 1234 5678" in student(app).get(f"/pay/course/{cid}").data.decode()


def test_details_are_shown_as_plain_text_not_html(app):
    cid, _ = paid_course(app)
    admin(app).post("/admin/payments/details", data={"instructions": "<script>alert(1)</script> Bank 1"})
    page = student(app).get(f"/pay/course/{cid}").data.decode()
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_a_settings_file_that_still_has_the_xxxx_placeholder_counts_as_sample(tmp_path):
    app = make_app(tmp_path, MANUAL_PAYMENT_INSTRUCTIONS="Transfer to account 1000XXXXXXXX (Your Bank).")
    cid, _ = paid_course(app)
    page = student(app).get(f"/pay/course/{cid}").data.decode()
    assert "XXXX" not in page and "1000 1234 5678" in page
    assert "sample" in admin(app).get("/admin/payments").data.decode()


def test_real_details_in_the_settings_file_are_respected(tmp_path):
    app = make_app(tmp_path, MANUAL_PAYMENT_INSTRUCTIONS="Awash Bank 777888")
    cid, _ = paid_course(app)
    assert "Awash Bank 777888" in student(app).get(f"/pay/course/{cid}").data.decode()
    assert "sample" not in admin(app).get("/admin/payments").data.decode().split("Payment details shown")[1][:300].lower()


def test_only_admins_can_edit_the_payment_details(app):
    teacher = app.test_client()
    login(teacher, "instructor@example.com", "instructor123", area="staff")
    assert teacher.get("/admin/payments/details").status_code == 403
    assert teacher.post("/admin/payments/details", data={"instructions": "hacked"}).status_code == 403
    assert student(app).get("/admin/payments/details").status_code == 403
    assert app.test_client().get("/admin/payments/details").status_code in (302, 401, 403)
