"""Next release (PRD v1.1 section 8): one group of tests per NX task."""
import re
from decimal import Decimal

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Course, Lesson, User, db
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads"), "PAYMENT_DEMO_MODE": False})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


def login(c, email, password, **extra):
    return c.post("/login", data={"email": email, "password": password, **extra})


def admin_client(app):
    c = app.test_client()
    login(c, "admin@example.com", "admin12345", area="staff")
    return c


def teacher_client(app):
    c = app.test_client()
    login(c, "instructor@example.com", "instructor123", area="staff")
    return c


def student_client(app, email="stu@example.com"):
    c = app.test_client()
    c.post("/register", data={"full_name": "Abebe Kebede", "email": email, "password": "secret123",
                              "password2": "secret123", "agree": "1"})
    return c


def add_course(app, title, **kw):
    with app.app_context():
        c = Course(slug=title.lower().replace(" ", "-"), title=title, is_published=kw.pop("published", True),
                   price=Decimal(str(kw.pop("price", "0"))), **kw)
        db.session.add(c)
        db.session.commit()
        return c.id


def add_lesson(app, cid, title="L", minutes=None, **kw):
    with app.app_context():
        pos = Lesson.query.filter_by(course_id=cid).count() + 1
        l = Lesson(course_id=cid, title=title, position=pos, duration_min=minutes, **kw)
        db.session.add(l)
        db.session.commit()
        return l.id


# --- NX-1: durations that agree ---------------------------------------------------------------

@pytest.mark.parametrize("minutes,text", [(0, ""), (None, ""), (15, "15 min"), (60, "1 h"), (80, "1 h 20 min"),
                                          (180, "3 h"), (12, "12 min"), (59.6, "1 h")])
def test_duration_filter(app, minutes, text):
    with app.test_request_context("/"):
        assert app.jinja_env.filters["duration"](minutes) == text


def test_duration_filter_in_amharic(app):
    with app.test_request_context("/", headers={"Cookie": "lang=am"}):
        assert app.jinja_env.filters["duration"](80) == "1 ሰዓት 20 ደቂቃ"


def test_no_page_prints_decimal_hours_for_short_courses(app):
    cid = add_course(app, "Quick Intro")
    add_lesson(app, cid, "A", 7)
    add_lesson(app, cid, "B", 5)
    c = app.test_client()
    for path in ("/courses/quick-intro", "/courses", "/"):
        page = c.get(path).data.decode()
        assert not re.search(r"\b0\.\d h\b", page), path
    course_page = c.get("/courses/quick-intro").data.decode()
    assert "12 min of lessons" in course_page and "2 lessons" in course_page
    assert re.search(r"2 lessons[^<]*· 12 min", course_page)           # syllabus total


def test_course_card_and_syllabus_use_hours_and_minutes(app):
    cid = add_course(app, "Mixed Length")
    add_lesson(app, cid, "A", 80)
    page = app.test_client().get("/courses/mixed-length").data.decode()
    assert "1 h 20 min of lessons" in page and "1 h 20 min" in page.split('id="syllabus"')[1]
    assert "1 h 20 min" in app.test_client().get("/courses").data.decode()


def test_certificate_hours_stay_as_hours_of_training(app):
    with app.app_context():
        course = Course.query.first()
        course.hours = Decimal("4.5")
        db.session.commit()
        course_id = course.id
    with app.app_context():
        from academy.models import Certificate
        user = User(email="c@example.com", full_name="C Holder")
        user.set_password("secret123")
        db.session.add(user)
        db.session.flush()
        cert = Certificate(user_id=user.id, course_id=course_id)
        db.session.add(cert)
        db.session.commit()
        code = cert.code
    assert "4.5 hours of training" in app.test_client().get(f"/certificates/{code}").data.decode()


# --- NX-2: shorter hero on phones -------------------------------------------------------------

def test_hero_panel_is_in_the_page_once_and_ordered_below_the_courses_on_phones(app):
    page = app.test_client().get("/").data.decode()
    assert page.count('class="hero-panel"') == 1                       # moved with CSS, never duplicated
    assert '<div class="home">' in page and 'class="section home-topics"' in page and 'class="section home-courses"' in page
    css = open("academy/static/style.css").read()
    phone = css.split("@media (max-width: 899px) {\n  .home {")[1].split("\n}\n")[0]
    order = lambda sel: int(re.search(rf"{re.escape(sel)}[^}}]*?[\s{{]order: (\d+)", phone).group(1))
    assert order(".home .hero-main") < order(".home .home-topics") < order(".home .home-courses") \
        < order(".home .hero-panel") < order(".home .home-free") < order(".home .home-how")


def test_desktop_hero_keeps_its_two_columns(app):
    css = open("academy/static/style.css").read()
    assert "grid-template-columns: 3fr 2fr" in css and "@media (min-width: 900px)" in css


# --- NX-3: one sign-up prompt when the homepage is empty --------------------------------------

def test_empty_homepage_asks_a_guest_to_sign_up_at_most_twice(app):
    with app.app_context():
        for c in Course.query.all():
            c.is_published = False
        db.session.commit()
    page = app.test_client().get("/").data.decode()
    assert page.count(">Create a free account<") <= 2 and 'class="cta-band' not in page


def test_call_to_action_band_shows_when_courses_exist_and_hides_when_signed_in(app):
    assert 'class="cta-band' in app.test_client().get("/").data.decode()
    assert 'class="cta-band' not in student_client(app).get("/").data.decode()


# --- NX-4: student-first sign-in --------------------------------------------------------------

def test_student_first_sign_in_in_amharic(app):
    c = app.test_client()
    c.set_cookie("lang", "am")
    page = c.get("/login").data.decode()
    assert "መምህር ወይም አስተዳዳሪ ነዎት? እዚህ ይግቡ" in page and "Teacher or admin?" not in page
    staff = c.get("/login?area=staff").data.decode()
    assert "ተማሪ ነዎት? እዚህ ይግቡ" in staff and "Student?" not in staff


def test_wrong_door_messages_point_to_the_other_sign_in_not_to_tabs(app):
    c = app.test_client()
    r = login(c, "admin@example.com", "admin12345", area="student")
    assert b"teacher and admin sign-in" in r.data and b"tab" not in r.data.split(b'class="flash')[1].split(b"</div>")[0]


# --- NX-5: topic icon on course card covers ---------------------------------------------------

def cover_of(app, slug):
    html = app.test_client().get("/courses").data.decode()
    for m in re.finditer(r'<article class="card course-card">.*?</article>', html, re.S):
        if f"/courses/{slug}" in m.group(0):
            return re.search(r'<div class="cover".*?</div>', m.group(0), re.S).group(0)


def test_card_cover_shows_the_matching_topic_icon_and_keeps_the_text(app):
    add_course(app, "Biz Course", category="Business")
    add_course(app, "Odd Course", category="Cooking")
    add_course(app, "No Topic")
    biz = cover_of(app, "biz-course")
    assert "#i-briefcase" in biz and "Business" in biz and 'aria-hidden="true"' in biz
    assert "#i-book" in cover_of(app, "odd-course") and "Cooking" in cover_of(app, "odd-course")
    assert "#i-book" in cover_of(app, "no-topic")


# --- NX-6: footer contact ---------------------------------------------------------------------

def footer(app):
    return app.test_client().get("/").data.decode().split('<footer class="site-footer">')[1]


def test_footer_shows_real_email_and_phone_as_links(app):
    app.config.update(CONTACT_EMAIL="hello@mek-academy.com", CONTACT_PHONE="+251 911 22 33 44")
    page = footer(app)
    assert 'href="mailto:hello@mek-academy.com"' in page and 'href="tel:+251911223344"' in page
    assert ">+251 911 22 33 44<" in page


@pytest.mark.parametrize("email,phone", [("", ""), (None, None), ("info@example.com", "+251 9XX XXX XXX")])
def test_footer_hides_unset_and_placeholder_contact_details(app, email, phone):
    app.config.update(CONTACT_EMAIL=email, CONTACT_PHONE=phone)
    page = footer(app)
    assert "mailto:" not in page and "tel:" not in page


def test_footer_shows_only_the_detail_that_is_set(app):
    app.config.update(CONTACT_EMAIL="hello@mek-academy.com", CONTACT_PHONE="+251 9XX XXX XXX")
    page = footer(app)
    assert "mailto:hello@mek-academy.com" in page and "tel:" not in page


# --- NX-7: clear pricing block in the course editor -------------------------------------------

def course_form_data(**extra):
    data = {"title": "Priced Course", "level": "Beginner", "summary": "x"}
    data.update(extra)
    return data


def price_of(app, slug):
    with app.app_context():
        return Course.query.filter_by(slug=slug).one().price


def test_pricing_block_has_free_and_paid_choices_and_a_price_input(app):
    page = admin_client(app).get("/admin/courses/new").data.decode()
    assert '<fieldset class="pricing">' in page and 'name="pricing" value="free"' in page
    assert 'name="pricing" value="paid"' in page and 'id="price"' in page and "<script" not in page.split('class="pricing"')[1].split("</fieldset>")[0]
    assert re.search(r'name="pricing" value="free" checked', page)           # new courses start free


def test_free_saves_price_zero_even_if_a_price_was_typed(app):
    admin_client(app).post("/admin/courses/new", data=course_form_data(pricing="free", price="900"))
    assert price_of(app, "priced-course") == 0


def test_paid_needs_a_price_above_zero_and_saves_it(app):
    admin = admin_client(app)
    for bad in ("", "0", "-5"):
        assert admin.post("/admin/courses/new", data=course_form_data(pricing="paid", price=bad)).status_code == 400
    with app.app_context():
        assert Course.query.filter_by(slug="priced-course").count() == 0
    admin.post("/admin/courses/new", data=course_form_data(pricing="paid", price="1250.50"))
    assert price_of(app, "priced-course") == Decimal("1250.50")


def test_editing_shows_the_saved_choice(app):
    admin = admin_client(app)
    admin.post("/admin/courses/new", data=course_form_data(pricing="paid", price="700"))
    with app.app_context():
        cid = Course.query.filter_by(slug="priced-course").one().id
    page = admin.get(f"/admin/courses/{cid}").data.decode()
    assert re.search(r'value="paid" checked', page) and 'value="700.00"' in page


def test_a_teacher_sees_the_price_read_only_and_cannot_change_it(app):
    admin = admin_client(app)
    admin.post("/admin/courses/new", data=course_form_data(pricing="paid", price="700"))
    with app.app_context():
        c = Course.query.filter_by(slug="priced-course").one()
        c.instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
        cid = c.id
    teacher = teacher_client(app)
    page = teacher.get(f"/admin/courses/{cid}").data.decode()
    assert "700.00" in page and 'name="price"' not in page and 'name="pricing"' not in page
    assert "Only an admin sets the price" in page
    teacher.post(f"/admin/courses/{cid}", data=course_form_data(pricing="free", price="0"))
    assert price_of(app, "priced-course") == Decimal("700")


def test_a_course_created_by_a_teacher_starts_free(app):
    teacher_client(app).post("/admin/courses/new", data=course_form_data(pricing="paid", price="5000"))
    assert price_of(app, "priced-course") == 0


def test_older_forms_without_the_pricing_choice_still_save_the_price(app):
    admin_client(app).post("/admin/courses/new", data=course_form_data(price="320"))
    assert price_of(app, "priced-course") == Decimal("320")


# --- NX-8: "Teacher" wording ------------------------------------------------------------------

def visible_text(html):
    """Page text without tags, attributes and class names (CSS class names may still say instructor)."""
    return re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\\1>", "", html, flags=re.S))


def test_no_instructor_wording_on_student_facing_pages(app):
    with app.app_context():
        course = Course.query.first()
        course.instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        slug, cid = course.slug, course.id
        db.session.commit()
    lesson_id = add_lesson(app, cid, "Intro", 5, is_preview=True)
    guest = app.test_client()
    for path in ("/", "/courses", f"/courses/{slug}", f"/learn/lesson/{lesson_id}", "/contact", "/login",
                 "/policies/terms", "/policies/privacy", "/policies/refunds"):
        response = guest.get(path)
        assert response.status_code == 200, path
        assert "nstructor" not in visible_text(response.data.decode()), path
    course_page = guest.get(f"/courses/{slug}").data.decode()
    assert "Your teacher" in course_page and "Taught by" in course_page
    with app.app_context():
        paid_id = Course.query.filter(Course.price > 0).first().id
    stu = student_client(app)
    for path in ("/profile", "/dashboard", f"/pay/course/{paid_id}"):
        assert "nstructor" not in visible_text(stu.get(path).data.decode()), path


def test_default_site_intro_says_teachers_and_an_old_settings_value_is_replaced(monkeypatch):
    import importlib

    from academy import config

    old = ("Practical, professional training taught by experienced instructors. Watch lessons, download "
           "materials, test yourself and earn a certificate - on your phone or computer.")
    monkeypatch.setenv("SITE_INTRO", old)
    assert "experienced teachers" in importlib.reload(config).Config.SITE_INTRO
    monkeypatch.setenv("SITE_INTRO", "Our own words.")
    assert importlib.reload(config).Config.SITE_INTRO == "Our own words."
    monkeypatch.delenv("SITE_INTRO")
    importlib.reload(config)


def test_a_teachers_profile_and_comment_badge_say_teacher(app):
    t = teacher_client(app)
    profile = t.get("/profile").data.decode()
    assert "Teacher profile" in profile and "Teacher" in profile.split("joined")[0]


def test_teacher_wording_in_amharic_uses_the_glossary_term(app):
    c = student_client(app)
    c.set_cookie("lang", "am")
    with app.app_context():
        course = Course.query.first()
        course.instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        slug = course.slug
        db.session.commit()
    page = c.get(f"/courses/{slug}").data.decode()
    assert "መምህርዎ" in page and "አስተማሪ" not in page


# --- NX-9: new-receipt email to admins --------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def upload_receipt(app, c):
    with app.app_context():
        cid = Course.query.filter(Course.price > 0).first().id
    return c.post(f"/pay/course/{cid}/manual", data={"reference": "FT55", "receipt": (__import__("io").BytesIO(PNG), "s.png")},
                  content_type="multipart/form-data", follow_redirects=True), cid


def second_admin(app):
    with app.app_context():
        u = User(email="boss2@example.com", full_name="Second Admin", role="admin")
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()


def test_each_admin_gets_one_email_with_student_course_amount_and_link(app, monkeypatch):
    sent = []
    monkeypatch.setattr("academy.routes.pay.send_email", lambda to, subject, body, reply_to=None: sent.append((to, subject, body)) or True)
    second_admin(app)
    r, cid = upload_receipt(app, student_client(app))
    assert b"Receipt submitted" in r.data
    assert sorted(to for to, _s, _b in sent) == ["admin@example.com", "boss2@example.com"]
    _to, subject, body = sent[0]
    assert "Abebe Kebede" in body and "stu@example.com" in body and "2,500.00 ETB" in body and "FT55" in body
    assert "/admin/payments?status=submitted" in body and "Receipt to review" in subject


def test_teachers_students_and_suspended_admins_are_not_emailed(app, monkeypatch):
    sent = []
    monkeypatch.setattr("academy.routes.pay.send_email", lambda to, subject, body, reply_to=None: sent.append(to) or True)
    with app.app_context():
        u = User(email="gone@example.com", full_name="Gone Admin", role="admin", is_active_account=False)
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()
    upload_receipt(app, student_client(app))
    assert sent == ["admin@example.com"]


def test_upload_still_succeeds_when_mail_fails(app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("academy.routes.pay.send_email", boom)
    second_admin(app)
    r, _cid = upload_receipt(app, student_client(app))
    assert b"Receipt submitted" in r.data
    from academy.models import Payment
    with app.app_context():
        assert Payment.query.filter_by(status="submitted").count() == 1


def test_one_failing_address_does_not_skip_the_other_admin(app, monkeypatch):
    sent = []

    def flaky(to, subject, body, reply_to=None):
        if to == "admin@example.com":
            raise OSError("mailbox full")
        sent.append(to)
        return True

    monkeypatch.setattr("academy.routes.pay.send_email", flaky)
    second_admin(app)
    upload_receipt(app, student_client(app))
    assert sent == ["boss2@example.com"]


def test_without_a_mail_server_the_notice_is_only_logged(app):
    r, _cid = upload_receipt(app, student_client(app))                  # MAIL_SERVER is empty in tests
    assert b"Receipt submitted" in r.data


def test_admin_recorded_payments_do_not_send_the_receipt_notice(app, monkeypatch):
    sent = []
    monkeypatch.setattr("academy.routes.pay.send_email", lambda *a, **k: sent.append(a) or True)
    with app.app_context():
        stu = User(email="s2@example.com", full_name="S Two")
        stu.set_password("secret123")
        db.session.add(stu)
        db.session.commit()
        sid, cid = stu.id, Course.query.filter(Course.price > 0).first().id
    admin_client(app).post("/admin/payments/record", data={"user_id": sid, "course_id": cid, "amount": "100", "channel": "cash", "confirm": "1"})
    assert sent == []


# --- NX-14: revenue and enrolment report ------------------------------------------------------

from datetime import datetime  # noqa: E402


def seed_money(app):
    """Confirmed payments in March and April 2026 (and one in 2025), plus ignored ones."""
    from academy.models import Enrollment, Payment
    with app.app_context():
        a, b = [c.id for c in Course.query.order_by(Course.id).limit(2)]
        users = []
        for i in range(5):
            u = User(email=f"r{i}@example.com", full_name=f"Reporter {i}")
            u.set_password("secret123")
            db.session.add(u)
            users.append(u)
        db.session.flush()

        def pay(user, course, amount, when, status="paid"):
            db.session.add(Payment(user_id=user.id, course_id=course, amount=Decimal(amount), currency="ETB", method="manual",
                                   status=status, paid_at=when if status == "paid" else None))

        def join(user, course, when):
            e = Enrollment(user_id=user.id, course_id=course, status="active", activated_at=when)
            db.session.add(e)

        pay(users[0], a, "1000", datetime(2026, 3, 5, 12)); pay(users[1], a, "1500.50", datetime(2026, 3, 20, 12))
        pay(users[2], b, "700", datetime(2026, 3, 9, 12)); pay(users[3], a, "999", datetime(2026, 3, 9), status="submitted")
        pay(users[3], b, "400", datetime(2026, 4, 2, 12)); pay(users[4], a, "250", datetime(2025, 12, 31, 12))
        join(users[0], a, datetime(2026, 3, 5)); join(users[1], a, datetime(2026, 3, 20)); join(users[2], b, datetime(2026, 3, 9))
        join(users[3], b, datetime(2026, 4, 2)); join(users[4], a, datetime(2025, 12, 31))
        db.session.commit()
        return a, b


def parse_table(html):
    rows = re.findall(r"<tr><td>(\w+)</td><td>([^<]+)</td><td>(\d+)</td><td>([\d,\.]+)</td><td>(\d+)</td></tr>", html)
    return [(m, c, int(p), r.replace(",", ""), int(e)) for m, c, p, r, e in rows]


def test_report_totals_match_the_payments(app):
    seed_money(app)
    page = admin_client(app).get("/admin/reports?year=2026").data.decode()
    rows = parse_table(page)
    march = [r for r in rows if r[0] == "March"]
    assert sum(r[2] for r in march) == 3 and sum(Decimal(r[3]) for r in march) == Decimal("3200.50")   # paid only
    assert sum(r[4] for r in march) == 3
    april = [r for r in rows if r[0] == "April"]
    assert sum(Decimal(r[3]) for r in april) == Decimal("400.00") and sum(r[4] for r in april) == 1
    assert "3,600.50 ETB" in page and "2026" in page
    assert all("December" != r[0] for r in rows)                          # last year's payment is not in 2026
    # the same numbers as the payments table
    with app.app_context():
        from academy.models import Payment
        total = sum(Decimal(str(p.amount)) for p in Payment.query.filter(Payment.status == "paid") if p.paid_at.year == 2026)
    assert total == Decimal("3600.50")


def test_report_filters_by_year_and_lists_years_with_data(app):
    seed_money(app)
    admin = admin_client(app)
    page = admin.get("/admin/reports?year=2025").data.decode()
    assert "250.00 ETB" in page and "December" in page and "2026" in page.split("Year:")[1][:200]
    assert "Nothing recorded in" in admin.get("/admin/reports?year=2031").data.decode()
    assert "Nothing recorded" not in admin.get("/admin/reports?year=2026").data.decode()
    assert admin.get("/admin/reports?year=banana").status_code == 200       # falls back to this year


def test_report_csv_matches_the_screen(app):
    seed_money(app)
    admin = admin_client(app)
    screen = parse_table(admin.get("/admin/reports?year=2026").data.decode())
    r = admin.get("/admin/reports.csv?year=2026")
    assert r.status_code == 200 and r.mimetype == "text/csv" and "report-2026.csv" in r.headers["Content-Disposition"]
    lines = [l.split(",") for l in r.data.decode().strip().splitlines()]
    assert lines[0][:3] == ["year", "month", "course"]
    body, total = lines[1:-1], lines[-1]
    assert [(l[1], l[2], int(l[3]), l[4], int(l[6])) for l in body] == [(m, c, p, f"{Decimal(rev):.2f}", e) for m, c, p, rev, e in screen]
    assert total[1] == "TOTAL" and total[4] == "3600.50" and total[3] == "4" and total[6] == "4"


def test_only_admins_can_see_the_report(app):
    seed_money(app)
    student = student_client(app, "x@example.com")
    for path in ("/admin/reports", "/admin/reports.csv"):
        assert teacher_client(app).get(path).status_code == 403
        assert student.get(path).status_code == 403
        assert app.test_client().get(path).status_code in (302, 401, 403)
    assert 'href="/admin/reports"' in admin_client(app).get("/admin/").data.decode()
    assert 'href="/admin/reports"' not in teacher_client(app).get("/admin/").data.decode()


def test_report_is_empty_but_works_with_no_payments(app):
    page = admin_client(app).get("/admin/reports").data.decode()
    assert "Nothing recorded in" in page and "0.00 ETB" in page


# --- NX-20: lesson lock control for admins only -----------------------------------------------

def own_course_with_lessons(app):
    with app.app_context():
        c = Course.query.filter_by(slug="project-management-fundamentals").one()
        c.instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
        return c.id, [l.id for l in c.lessons]


def flag(app, lesson_id):
    with app.app_context():
        return db.session.get(Lesson, lesson_id).is_preview


def test_a_teachers_toggle_request_returns_403_and_changes_nothing(app):
    cid, ids_ = own_course_with_lessons(app)
    for lid in ids_[:2]:
        before = flag(app, lid)
        assert teacher_client(app).post(f"/admin/lessons/{lid}/public").status_code == 403
        assert flag(app, lid) is before


def test_teachers_see_a_read_only_label_and_no_toggle(app):
    cid, ids_ = own_course_with_lessons(app)
    with app.app_context():
        db.session.get(Lesson, ids_[0]).is_preview = True
        db.session.commit()
    page = teacher_client(app).get(f"/admin/courses/{cid}").data.decode()
    table = page.split('id="lessons"')[1].split("Quick add")[0]
    assert "Make public" not in table and "Lock it" not in table and "/public" not in table
    assert 'class="badge active">public' in table and 'class="badge">locked' in table
    form = teacher_client(app).get(f"/admin/lessons/{ids_[0]}").data.decode()
    assert 'name="is_preview"' not in form and "only an admin can make a lesson public" in form


def test_admin_still_sees_the_toggle_and_can_use_it(app):
    cid, ids_ = own_course_with_lessons(app)
    before = flag(app, ids_[1])
    admin = admin_client(app)
    table = admin.get(f"/admin/courses/{cid}").data.decode().split('id="lessons"')[1].split("Quick add")[0]
    assert "Make public" in table or "Lock it" in table
    assert admin.post(f"/admin/lessons/{ids_[1]}/public").status_code == 302 and flag(app, ids_[1]) is (not before)
    assert 'name="is_preview"' in admin.get(f"/admin/lessons/{ids_[1]}").data.decode()


def test_a_teacher_cannot_change_the_flag_through_the_lesson_form(app):
    cid, ids_ = own_course_with_lessons(app)
    with app.app_context():
        db.session.get(Lesson, ids_[0]).is_preview = False
        db.session.get(Lesson, ids_[1]).is_preview = True
        db.session.commit()
    t = teacher_client(app)
    t.post(f"/admin/lessons/{ids_[0]}", data={"title": "Edited", "kind": "text", "is_preview_present": "1", "is_preview": "1"})
    t.post(f"/admin/lessons/{ids_[1]}", data={"title": "Edited 2", "kind": "text", "is_preview_present": "1"})
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]).title == "Edited" and db.session.get(Lesson, ids_[0]).is_preview is False
        assert db.session.get(Lesson, ids_[1]).title == "Edited 2" and db.session.get(Lesson, ids_[1]).is_preview is True


def test_lessons_a_teacher_creates_start_locked(app):
    cid, _ids = own_course_with_lessons(app)
    teacher_client(app).post(f"/admin/courses/{cid}/lessons/new", data={"title": "Teacher made", "kind": "text", "is_preview_present": "1", "is_preview": "1"})
    with app.app_context():
        assert Lesson.query.filter_by(title="Teacher made").one().is_preview is False


def test_an_admin_can_make_a_new_lesson_public_from_the_form(app):
    cid, _ids = own_course_with_lessons(app)
    admin_client(app).post(f"/admin/courses/{cid}/lessons/new", data={"title": "Admin made", "kind": "text", "is_preview_present": "1", "is_preview": "1"})
    with app.app_context():
        assert Lesson.query.filter_by(title="Admin made").one().is_preview is True


# --- NX-19: category management and bulk lesson tools -----------------------------------------

def categories_of(app):
    with app.app_context():
        return {c.slug: c.category for c in Course.query.all()}


def test_categories_page_lists_names_with_course_counts(app):
    add_course(app, "One", category="Business")
    add_course(app, "Two", category="Business")
    add_course(app, "Three", category="Health")
    add_course(app, "Four")
    page = admin_client(app).get("/admin/categories").data.decode()
    assert re.search(r"<td><b>Business</b></td><td>2</td>", page)
    assert re.search(r"<td><b>Health</b></td><td>1</td>", page) and "1 course has no category" in page


def test_renaming_a_category_updates_every_course_and_the_catalog_filter(app):
    add_course(app, "One", category="Busines")
    add_course(app, "Two", category="Busines")
    admin = admin_client(app)
    r = admin.post("/admin/categories", data={"old": "Busines", "new": "Business Skills"}, follow_redirects=True)
    assert b"2 courses moved" in r.data
    cats = categories_of(app)
    assert cats["one"] == cats["two"] == "Business Skills"
    catalog = app.test_client().get("/courses?category=Business+Skills").data.decode()
    assert "One" in catalog and "Two" in catalog
    assert "0 courses" in app.test_client().get("/courses?category=Busines").data.decode()


def test_giving_a_category_an_existing_name_merges_them_ignoring_capitals(app):
    add_course(app, "A", category="Tech")
    add_course(app, "B", category="Technology")
    r = admin_client(app).post("/admin/categories", data={"old": "Tech", "new": "technology"}, follow_redirects=True)
    assert b"merged with the existing category" in r.data
    assert categories_of(app)["a"] == "Technology"                      # the existing spelling wins


@pytest.mark.parametrize("old,new,message", [("Nope", "X", b"no longer exists"), ("Business", "   ", b"Enter the new name"),
                                             ("Business", "Business", b"unchanged")])
def test_category_rename_validation(app, old, new, message):
    with app.app_context():
        c = Course.query.first(); c.category = "Business"; db.session.commit()
    before = categories_of(app)
    r = admin_client(app).post("/admin/categories", data={"old": old, "new": new}, follow_redirects=True)
    assert message in r.data and categories_of(app) == before


def test_category_names_are_trimmed_and_limited(app):
    add_course(app, "A", category="Old")
    admin_client(app).post("/admin/categories", data={"old": "Old", "new": "  Spaced    out  " + "x" * 80})
    assert categories_of(app)["a"].startswith("Spaced out xxx") and len(categories_of(app)["a"]) == 60


def test_category_tools_are_admin_only_and_need_csrf(app):
    add_course(app, "A", category="Old")
    for client in (teacher_client(app), student_client(app)):
        assert client.get("/admin/categories").status_code == 403
        assert client.post("/admin/categories", data={"old": "Old", "new": "New"}).status_code == 403
    assert categories_of(app)["a"] == "Old"
    assert "Manage categories" in admin_client(app).get("/admin/courses").data.decode()
    assert "Manage categories" not in teacher_client(app).get("/admin/courses").data.decode()
    assert 'name="_csrf"' in admin_client(app).get("/admin/categories").data.decode()


def bulk_setup(app):
    cid = add_course(app, "Bulk Course")
    ids_ = [add_lesson(app, cid, f"Lesson {i}", 5) for i in range(1, 5)]
    return cid, ids_


def test_bulk_make_public_and_lock(app):
    cid, ids_ = bulk_setup(app)
    admin = admin_client(app)
    r = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "public", "lesson_ids": ids_[:3]}, follow_redirects=True)
    assert b"3 lessons are now public" in r.data
    assert [flag(app, i) for i in ids_] == [True, True, True, False]
    r = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "lock", "lesson_ids": [ids_[0], ids_[2]]}, follow_redirects=True)
    assert b"2 lessons are now locked" in r.data
    assert [flag(app, i) for i in ids_] == [False, True, False, False]


def test_bulk_delete_asks_for_confirmation_first_and_keeps_numbering(app):
    cid, ids_ = bulk_setup(app)
    admin = admin_client(app)
    ask = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "delete", "lesson_ids": ids_[1:3]})
    assert ask.status_code == 200 and b"Yes, delete 2 lessons" in ask.data and b"Lesson 2" in ask.data
    with app.app_context():
        assert Lesson.query.filter_by(course_id=cid).count() == 4          # nothing deleted yet
    done = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "delete", "confirmed": "1", "lesson_ids": ids_[1:3]}, follow_redirects=True)
    assert b"Deleted 2 lessons" in done.data
    with app.app_context():
        left = Lesson.query.filter_by(course_id=cid).order_by(Lesson.position).all()
        assert [l.title for l in left] == ["Lesson 1", "Lesson 4"] and [l.position for l in left] == [1, 2]


def test_bulk_ignores_lessons_of_other_courses_and_requires_a_choice(app):
    cid, ids_ = bulk_setup(app)
    other = add_course(app, "Other Course")
    foreign = add_lesson(app, other, "Foreign", 5)
    admin = admin_client(app)
    admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "public", "lesson_ids": [foreign, ids_[0]]})
    assert flag(app, foreign) is False and flag(app, ids_[0]) is True
    none = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "public"}, follow_redirects=True)
    assert b"Tick at least one lesson" in none.data
    bad = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "explode", "lesson_ids": ids_}, follow_redirects=True)
    assert b"Tick at least one lesson" in bad.data
    only_foreign = admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "delete", "confirmed": "1", "lesson_ids": [foreign]}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Lesson, foreign) is not None


def test_bulk_tools_are_admin_only_even_for_the_courses_teacher(app):
    cid, ids_ = bulk_setup(app)
    with app.app_context():
        db.session.get(Course, cid).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
    t = teacher_client(app)
    for action in ("public", "lock", "delete"):
        assert t.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": action, "confirmed": "1", "lesson_ids": ids_}).status_code == 403
    assert app.test_client().post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "delete", "confirmed": "1", "lesson_ids": ids_}).status_code in (302, 400, 401, 403)
    with app.app_context():
        assert Lesson.query.filter_by(course_id=cid).count() == 4 and not any(flag(app, i) for i in ids_)
    page = t.get(f"/admin/courses/{cid}").data.decode()
    assert 'name="lesson_ids"' not in page and "With the ticked lessons" not in page
    admin_page = admin_client(app).get(f"/admin/courses/{cid}").data.decode()
    assert admin_page.count('name="lesson_ids"') == 4 and 'id="bulk-lessons"' in admin_page


def test_single_lesson_delete_still_works_for_the_teacher(app):
    cid, ids_ = bulk_setup(app)
    with app.app_context():
        db.session.get(Course, cid).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
    assert teacher_client(app).post(f"/admin/lessons/{ids_[0]}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]) is None


def test_new_admin_actions_need_a_csrf_token_when_csrf_is_on(tmp_path):
    """With CSRF on, a login that carries the page's own token works; posts without one are refused."""
    class Cfg(TestConfig):
        CSRF_ENABLED = True
        UPLOAD_DIR = str(tmp_path / "u")

    app = create_app(Cfg)
    with app.app_context():
        seed()
    c = app.test_client()
    token = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login?area=staff").data.decode()).group(1)
    assert c.post("/login", data={"email": "admin@example.com", "password": "admin12345", "area": "staff", "_csrf": token}).status_code == 302
    with app.app_context():
        cid = Course.query.first().id
        lid = Course.query.first().lessons[0].id
        before = db.session.get(Lesson, lid).is_preview
    for path, data in (("/admin/categories", {"old": "Business & Management", "new": "X"}),
                       (f"/admin/courses/{cid}/lessons/bulk", {"action": "public", "lesson_ids": [lid]}),
                       (f"/admin/lessons/{lid}/public", {}), ("/admin/payments/details", {"instructions": "x"})):
        assert c.post(path, data=data).status_code == 400, path
    with app.app_context():
        assert db.session.get(Lesson, lid).is_preview is before


# --- NX-15: course cover images ---------------------------------------------------------------

import io  # noqa: E402
import os  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def edit_with_cover(client, cid, data, name, **extra):
    fields = {"title": "Cover Course", "level": "Beginner", "pricing": "free", "is_published": "1", **extra}
    if data is not None:
        fields["cover"] = (io.BytesIO(data), name)
    return client.post(f"/admin/courses/{cid}", data=fields, content_type="multipart/form-data")


def cover_of_course(app, cid):
    with app.app_context():
        return db.session.get(Course, cid).cover_image


def cover_files(app):
    folder = os.path.join(app.config["UPLOAD_DIR"], "covers")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


@pytest.mark.parametrize("data,name", [(PNG_BYTES, "pic.png"), (JPG_BYTES, "pic.jpg"), (JPG_BYTES, "pic.JPEG"), (WEBP_BYTES, "pic.webp")])
def test_valid_cover_images_are_stored_and_served(app, data, name):
    cid = add_course(app, "Cover Course")
    assert edit_with_cover(admin_client(app), cid, data, name).status_code == 302
    stored = cover_of_course(app, cid)
    assert stored and stored.rsplit(".", 1)[1] in ("png", "jpg", "webp") and cover_files(app) == [stored]
    r = app.test_client().get(f"/media/covers/{stored}")
    assert r.status_code == 200 and r.data == data and "max-age" in r.headers["Cache-Control"]


@pytest.mark.parametrize("data,name,message", [
    (b"GIF89a" + b"0" * 30, "pic.gif", b"JPG, PNG or WebP"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "pic.svg", b"JPG, PNG or WebP"),
    (b"just text pretending", "pic.png", b"not a real"),
    (PNG_BYTES, "pic.jpg", b"not a real"),                      # content does not match the name
    (JPG_BYTES, "pic.webp", b"not a real"),
    (b"", "empty.png", b"not a real"),
    (PNG_BYTES + b"0" * (1024 * 1024), "big.png", b"larger than 1 MB"),
])
def test_wrong_type_content_or_size_is_rejected_with_a_clear_message(app, data, name, message):
    cid = add_course(app, "Cover Course")
    r = edit_with_cover(admin_client(app), cid, data, name)
    assert r.status_code == 400 and message in r.data
    assert cover_of_course(app, cid) is None and cover_files(app) == []


def test_exactly_one_megabyte_is_accepted(app):
    cid = add_course(app, "Cover Course")
    data = PNG_BYTES + b"0" * (1024 * 1024 - len(PNG_BYTES))
    assert len(data) == 1024 * 1024 and edit_with_cover(admin_client(app), cid, data, "edge.png").status_code == 302


def test_cards_and_course_page_show_the_image_with_size_and_lazy_loading(app):
    cid = add_course(app, "Cover Course")
    edit_with_cover(admin_client(app), cid, PNG_BYTES, "pic.png")
    stored = cover_of_course(app, cid)
    c = app.test_client()
    for path in ("/", "/courses"):
        card = [m.group(0) for m in re.finditer(r'<article class="card course-card">.*?</article>', c.get(path).data.decode(), re.S) if "/courses/cover-course" in m.group(0)][0]
        assert f'src="/media/covers/{stored}"' in card and 'width="640" height="280"' in card and 'loading="lazy"' in card
        assert 'class="cover has-image"' in card and "#i-" not in card.split("</div>")[0]
    page = c.get("/courses/cover-course").data.decode()
    assert f'src="/media/covers/{stored}"' in page and 'width="800" height="450"' in page and 'loading="lazy"' in page


def test_cards_without_an_image_keep_the_topic_icon_and_the_same_cover_shape(app):
    cid = add_course(app, "Cover Course", category="Business")
    add_course(app, "Plain Course", category="Business")
    edit_with_cover(admin_client(app), cid, PNG_BYTES, "pic.png")
    html = app.test_client().get("/courses").data.decode()
    plain = [m.group(0) for m in re.finditer(r'<article class="card course-card">.*?</article>', html, re.S) if "/courses/plain-course" in m.group(0)][0]
    assert "#i-briefcase" in plain and "<img" not in plain and 'class="cover"' in plain
    css = open("academy/static/style.css").read()
    assert re.search(r"\.course-card \.cover \{[^}]*aspect-ratio: 16 / 7", css)       # one shape for every cover


def test_replacing_and_removing_the_cover_deletes_the_old_file(app):
    cid = add_course(app, "Cover Course")
    admin = admin_client(app)
    edit_with_cover(admin, cid, PNG_BYTES, "a.png")
    first = cover_of_course(app, cid)
    edit_with_cover(admin, cid, JPG_BYTES, "b.jpg")
    second = cover_of_course(app, cid)
    assert second != first and cover_files(app) == [second]
    edit_with_cover(admin, cid, None, "", remove_cover="1")
    assert cover_of_course(app, cid) is None and cover_files(app) == []


def test_saving_the_course_without_a_new_file_keeps_the_cover(app):
    cid = add_course(app, "Cover Course")
    admin = admin_client(app)
    edit_with_cover(admin, cid, PNG_BYTES, "a.png")
    kept = cover_of_course(app, cid)
    edit_with_cover(admin, cid, None, "")
    assert cover_of_course(app, cid) == kept


def test_deleting_a_course_deletes_its_cover(app):
    cid = add_course(app, "Cover Course")
    admin = admin_client(app)
    edit_with_cover(admin, cid, PNG_BYTES, "a.png")
    admin.post(f"/admin/courses/{cid}/delete")
    assert cover_files(app) == []


def test_covers_of_unpublished_courses_are_staff_only_and_unknown_names_404(app):
    cid = add_course(app, "Cover Course")
    admin = admin_client(app)
    edit_with_cover(admin, cid, PNG_BYTES, "a.png")
    stored = cover_of_course(app, cid)
    admin.post(f"/admin/courses/{cid}", data={"title": "Cover Course", "level": "Beginner", "pricing": "free"})   # unpublish
    assert app.test_client().get(f"/media/covers/{stored}").status_code == 404
    assert admin.get(f"/media/covers/{stored}").status_code == 200
    assert app.test_client().get("/media/covers/../../etc/passwd").status_code == 404
    assert app.test_client().get("/media/covers/nothing.png").status_code == 404


def test_a_teacher_can_set_the_cover_of_their_own_course_but_not_others(app):
    mine = add_course(app, "Cover Course")
    theirs = add_course(app, "Other Course")
    with app.app_context():
        db.session.get(Course, mine).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
    t = teacher_client(app)
    assert edit_with_cover(t, mine, PNG_BYTES, "a.png").status_code == 302 and cover_of_course(app, mine)
    assert edit_with_cover(t, theirs, PNG_BYTES, "a.png").status_code == 403 and cover_of_course(app, theirs) is None


def test_editor_form_offers_the_upload_and_a_remove_option(app):
    cid = add_course(app, "Cover Course")
    admin = admin_client(app)
    page = admin.get(f"/admin/courses/{cid}").data.decode()
    assert 'enctype="multipart/form-data"' in page and 'name="cover"' in page and "up to 1 MB" in page
    assert "remove_cover" not in page
    edit_with_cover(admin, cid, PNG_BYTES, "a.png")
    assert "remove_cover" in admin.get(f"/admin/courses/{cid}").data.decode()


def test_cover_migration_keeps_existing_courses_up_and_down(tmp_path):
    import sqlite3
    import subprocess
    db_file = tmp_path / "m.db"
    env = {**os.environ, "FLASK_APP": "wsgi.py", "SECRET_KEY": "x", "DATABASE_URL": f"sqlite:///{db_file}"}
    run = lambda *a: subprocess.run([__import__("sys").executable, "-m", "flask", *a], env=env, capture_output=True, text=True, cwd=os.getcwd())
    assert run("db", "upgrade", "d9e3f5a7b2c4").returncode == 0
    con = sqlite3.connect(db_file)
    con.execute("insert into course (slug, title, level, price, is_published, created_at) values ('c', 'C', 'Beginner', 0, 1, '2026-01-01')")
    con.commit(); con.close()
    assert run("db", "upgrade").returncode == 0
    assert sqlite3.connect(db_file).execute("select title, cover_image from course").fetchall() == [("C", None)]
    assert run("db", "downgrade").returncode == 0
    cols = [r[1] for r in sqlite3.connect(db_file).execute("pragma table_info(course)")]
    assert "cover_image" not in cols and sqlite3.connect(db_file).execute("select title from course").fetchall() == [("C",)]
