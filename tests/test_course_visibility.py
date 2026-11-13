"""Course names are public; the lock is on the lessons. New courses are visible by default, free or
paid, with every lesson listed and locked until an admin makes it public."""
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


def new_course(client, title, price="0"):
    """Create a course the way the editor does, leaving the visibility box as the form offers it."""
    page = client.get("/admin/courses/new").data.decode()
    box = re.search(r'<input type="checkbox" name="is_published" value="1"([^>]*)>', page).group(1)
    data = {"title": title, "level": "Beginner", "summary": "x", "pricing": "paid" if Decimal(price) else "free", "price": price}
    if "checked" in box:
        data["is_published"] = "1"
    client.post("/admin/courses/new", data=data)
    with client.application.app_context():
        return Course.query.filter_by(title=title).one().id


def add_lessons(app, cid, count=3):
    with app.app_context():
        ids = []
        for i in range(count):
            l = Lesson(course_id=cid, title=f"Lesson {i + 1}", position=i + 1, duration_min=5, kind="text")
            db.session.add(l)
            db.session.flush()
            ids.append(l.id)
        db.session.commit()
        return ids


def test_the_new_course_form_offers_visibility_ticked_by_default(app):
    page = admin_client(app).get("/admin/courses/new").data.decode()
    assert re.search(r'name="is_published" value="1" checked', page)
    assert "Show this course in the public list" in page and "Lessons stay locked" in page


def test_a_course_created_with_the_default_form_is_visible_to_everyone_and_lists_its_lessons_locked(app):
    cid = new_course(admin_client(app), "Brand New Course", price="1500")
    add_lessons(app, cid, 3)
    guest = app.test_client()
    assert "Brand New Course" in guest.get("/courses").data.decode()
    assert "Brand New Course" in guest.get("/").data.decode()
    page = guest.get("/courses/brand-new-course").data.decode()
    syllabus = page.split('id="syllabus"')[1].split("</ol>")[0]
    assert all(f"Lesson {i}" in syllabus for i in (1, 2, 3))
    assert syllabus.count('class="small muted locked"') == 3            # every lesson locked by default
    assert "Free preview" not in syllabus and '<a href="/learn/lesson/' not in syllabus


def test_free_and_paid_courses_are_both_listed_with_their_lessons(app):
    admin = admin_client(app)
    free_id = new_course(admin, "Free One", "0")
    paid_id = new_course(admin, "Paid One", "900")
    add_lessons(app, free_id, 2)
    add_lessons(app, paid_id, 2)
    catalog = app.test_client().get("/courses").data.decode()
    assert "Free One" in catalog and "Paid One" in catalog and ">Enroll<" in catalog
    for slug in ("free-one", "paid-one"):
        syllabus = app.test_client().get(f"/courses/{slug}").data.decode().split('id="syllabus"')[1].split("</ol>")[0]
        assert "Lesson 1" in syllabus and "Lesson 2" in syllabus and "locked" in syllabus


def test_locked_lessons_cannot_be_opened_but_a_lesson_an_admin_makes_public_can(app):
    admin = admin_client(app)
    cid = new_course(admin, "Mixed Access")
    ids_ = add_lessons(app, cid, 3)
    guest = app.test_client()
    assert guest.get(f"/learn/lesson/{ids_[0]}").status_code == 302                 # locked: sent to sign in
    admin.post(f"/admin/lessons/{ids_[0]}/public")
    assert guest.get(f"/learn/lesson/{ids_[0]}").status_code == 200                 # public intro opens for anyone
    assert guest.get(f"/learn/lesson/{ids_[1]}").status_code == 302                 # the rest stay locked
    syllabus = guest.get("/courses/mixed-access").data.decode().split('id="syllabus"')[1].split("</ol>")[0]
    assert f'href="/learn/lesson/{ids_[0]}"' in syllabus and "Free preview" in syllabus
    assert f'href="/learn/lesson/{ids_[1]}"' not in syllabus


def test_lessons_a_teacher_adds_are_locked_by_default_and_only_admins_can_make_them_public(app):
    teacher = teacher_client(app)
    cid = new_course(teacher, "Teacher Course")
    ids_ = add_lessons(app, cid, 1)
    teacher.post(f"/admin/lessons/{ids_[0]}", data={"title": "Lesson 1", "kind": "text", "is_preview_present": "1", "is_preview": "1"})
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]).is_preview is False
    assert "Teacher Course" in app.test_client().get("/courses").data.decode()      # the name is public anyway


def test_a_course_can_still_be_hidden_while_it_is_being_built_and_shown_again_with_one_click(app):
    admin = admin_client(app)
    cid = new_course(admin, "Work In Progress")
    guest = app.test_client()
    assert "Work In Progress" in guest.get("/courses").data.decode()
    r = admin.post(f"/admin/courses/{cid}/visibility", data={"next": "/admin/courses"}, follow_redirects=True)
    assert b"hidden from the public list" in r.data
    assert "Work In Progress" not in guest.get("/courses").data.decode()
    assert guest.get("/courses/work-in-progress").status_code == 404
    assert b"Hidden" in admin.get("/admin/courses").data
    r = admin.post(f"/admin/courses/{cid}/visibility", data={"next": "/admin/courses"}, follow_redirects=True)
    assert b"visible to everyone" in r.data and guest.get("/courses/work-in-progress").status_code == 200


def test_the_visibility_switch_is_for_the_courses_teacher_and_admins_only(app):
    cid = new_course(admin_client(app), "Switch Me")
    with app.app_context():
        db.session.get(Course, cid).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        other = User(email="other@example.com", full_name="Other", role="instructor")
        other.set_password("secret123")
        stu = User(email="s@example.com", full_name="S")
        stu.set_password("secret123")
        db.session.add_all([other, stu])
        db.session.commit()
    o = app.test_client(); login(o, "other@example.com", "secret123", area="staff")
    s = app.test_client(); login(s, "s@example.com", "secret123", area="student")
    for client in (o, s):
        assert client.post(f"/admin/courses/{cid}/visibility").status_code == 403
    assert app.test_client().post(f"/admin/courses/{cid}/visibility").status_code in (302, 400, 401, 403)
    assert teacher_client(app).post(f"/admin/courses/{cid}/visibility").status_code == 302      # the course's own teacher


def test_visibility_redirects_stay_inside_the_admin_area(app):
    cid = new_course(admin_client(app), "Safe Redirect")
    r = admin_client(app).post(f"/admin/courses/{cid}/visibility", data={"next": "https://evil.example/x"})
    assert r.headers["Location"].endswith("/admin/courses")


def test_admin_can_show_every_hidden_course_at_once_and_teachers_cannot(app):
    with app.app_context():
        for i in range(3):
            db.session.add(Course(slug=f"hid{i}", title=f"Hidden {i}", is_published=False, price=Decimal(0)))
        db.session.commit()
    admin = admin_client(app)
    assert b"Show all hidden courses" in admin.get("/admin/courses").data
    assert teacher_client(app).post("/admin/courses/show-all").status_code == 403
    guest = app.test_client()
    assert "Hidden 0" not in guest.get("/courses").data.decode()
    r = admin.post("/admin/courses/show-all", follow_redirects=True)
    assert b"3 hidden courses are now visible" in r.data
    catalog = guest.get("/courses").data.decode()
    assert all(f"Hidden {i}" in catalog for i in range(3))
    assert b"Show all hidden courses" not in admin.get("/admin/courses").data
    assert b"already visible" in admin.post("/admin/courses/show-all", follow_redirects=True).data


def test_admin_list_explains_the_rule_and_uses_plain_words(app):
    page = admin_client(app).get("/admin/courses").data.decode()
    assert "Lessons are locked by default" in page and "Visible to everyone" in page
    assert "Draft" not in page and "Published" not in page


def test_the_course_editor_checklist_and_status_use_the_same_words(app):
    admin = admin_client(app)
    cid = new_course(admin, "Words Course")
    page = admin.get(f"/admin/courses/{cid}").data.decode()
    assert "Visible to everyone" in page and "Show this course in the public list" in page and "Published" not in page
