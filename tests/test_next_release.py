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
