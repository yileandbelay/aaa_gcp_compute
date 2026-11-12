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
