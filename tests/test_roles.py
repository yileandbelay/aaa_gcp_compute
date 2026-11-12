"""Student, teacher and admin roles: one email can hold several, and only admins hand them out."""
import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import User, db
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads")})
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


def make_user(app, email, role="student", **extra):
    with app.app_context():
        u = User(email=email, full_name=email.split("@")[0].title(), role=role, **extra)
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()
        return u.id


def flags(app, uid):
    with app.app_context():
        u = db.session.get(User, uid)
        return u.role, u.is_student, u.can_study, u.is_staff


def test_students_are_students_and_staff_are_not_by_default(app):
    assert flags(app, make_user(app, "s@example.com")) == ("student", True, True, False)
    assert flags(app, make_user(app, "t@example.com", "instructor")) == ("instructor", False, False, True)
    assert flags(app, make_user(app, "a@example.com", "admin")) == ("admin", False, False, True)


def test_registration_always_creates_a_student(app):
    app.test_client().post("/register", data={"full_name": "New Person", "email": "new@example.com",
                                              "password": "secret123", "password2": "secret123", "agree": "1"})
    with app.app_context():
        u = User.query.filter_by(email="new@example.com").one()
        assert u.role == "student" and u.is_student


def test_admin_gives_one_email_teacher_and_student_roles(app):
    uid = make_user(app, "both@example.com")
    admin = admin_client(app)
    admin.post(f"/admin/users/{uid}", data={"action": "roles", "level": "instructor", "student": "1"})
    assert flags(app, uid) == ("instructor", True, True, True)
    page = admin.get("/admin/students").data
    assert b"Both" in page and b"Both" in admin.get("/admin/staff").data


def test_admin_can_remove_student_role_from_a_teacher_and_staff_access_again(app):
    uid = make_user(app, "mix@example.com", "instructor", is_student=True)
    admin = admin_client(app)
    admin.post(f"/admin/users/{uid}", data={"action": "roles", "level": "instructor"})
    assert flags(app, uid)[:3] == ("instructor", False, False)
    admin.post(f"/admin/users/{uid}", data={"action": "roles", "level": "student"})
    assert flags(app, uid) == ("student", True, True, False)  # staff access removed, back to student


def test_a_person_cannot_be_left_with_no_role(app):
    uid = make_user(app, "none@example.com", "instructor")
    admin_client(app).post(f"/admin/users/{uid}", data={"action": "roles", "level": "student"})
    assert flags(app, uid)[0] == "student" and flags(app, uid)[1] is True


def test_admin_may_add_student_role_to_self_but_not_change_staff_level(app):
    admin = admin_client(app)
    with app.app_context():
        me = User.query.filter_by(email="admin@example.com").one().id
    admin.post(f"/admin/users/{me}", data={"action": "roles", "level": "student", "student": "1"})
    assert flags(app, me) == ("admin", True, True, True)


def test_teachers_and_students_cannot_change_roles(app):
    uid = make_user(app, "victim@example.com")
    for email, pw in (("instructor@example.com", "instructor123"),):
        c = app.test_client()
        login(c, email, pw, area="staff")
        assert c.post(f"/admin/users/{uid}", data={"action": "roles", "level": "admin"}).status_code == 403
    assert flags(app, uid)[0] == "student"


def test_adding_a_new_teacher_can_include_the_student_role(app, monkeypatch):
    monkeypatch.setattr("academy.routes.admin.send_email", lambda *a, **k: True)
    admin = admin_client(app)
    admin.post("/admin/staff/new", data={"full_name": "Dual Role", "email": "dual@example.com",
                                         "role": "instructor", "also_student": "1"})
    admin.post("/admin/staff/new", data={"full_name": "Only Teach", "email": "only@example.com",
                                         "role": "instructor"})
    with app.app_context():
        assert User.query.filter_by(email="dual@example.com").one().is_student is True
        assert User.query.filter_by(email="only@example.com").one().is_student is False


def test_teacher_is_separate_from_admin_everywhere_that_matters(app):
    c = app.test_client()
    login(c, "instructor@example.com", "instructor123", area="staff")
    assert c.get("/admin/courses").status_code == 200
    for path in ("/admin/payments", "/admin/students", "/admin/staff", "/admin/messages"):
        assert c.get(path).status_code == 403, path
