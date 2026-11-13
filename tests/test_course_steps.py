"""The course editor as steps: Details, Lessons, Quizzes, Students, Announcements, Reviews. Each step is its
own page with only its form; the step bar shows what is done."""
import re
from decimal import Decimal

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Announcement, Assessment, Course, Lesson, Review, User, db
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


def teacher_client(app):
    c = app.test_client()
    login(c, "instructor@example.com", "instructor123", area="staff")
    return c


def make_course(app, title="Step Course", lessons=0, quizzes=0, mine=True):
    with app.app_context():
        c = Course(slug=title.lower().replace(" ", "-"), title=title, level="Beginner", price=Decimal(0), is_published=True,
                   instructor_id=User.query.filter_by(email="instructor@example.com").one().id if mine else None)
        db.session.add(c)
        db.session.flush()
        for i in range(lessons):
            db.session.add(Lesson(course_id=c.id, title=f"L{i + 1}", position=i + 1, kind="text"))
        for i in range(quizzes):
            db.session.add(Assessment(course_id=c.id, title=f"Q{i + 1}"))
        db.session.commit()
        return c.id


def bar(html):
    return html.split('class="tabs course-steps"', 1)[1].split("</nav>", 1)[0]


def labels(fragment):
    """Every step in the bar, in order: links, and the switched-off ones (spans)."""
    items = re.findall(r'<a href[^>]*>(.*?)</a>|<span class="step-off is-disabled"[^>]*>(.*?)</span>', fragment, re.S)
    return [(link or off) for link, off in items]


ALL = ["Details", "Lessons", "Quizzes", "Students", "Announcements", "Reviews"]


def clean(label):
    """A step's name without its tick or count."""
    return re.sub(r"\s+", " ", re.sub(r"[✓○]|\(\d+\)", "", re.sub(r"<[^>]+>", "", label))).strip()


def current(fragment):
    """The name of the step marked as the current page."""
    return clean(re.findall(r'aria-current="page"[^>]*>(.*?)</a>', fragment, re.S)[0])


def test_every_step_page_has_the_bar_in_order_with_the_current_step_marked(app):
    cid = make_course(app)
    admin = admin_client(app)
    pages = {"Details": f"/admin/courses/{cid}", "Lessons": f"/admin/courses/{cid}/lessons", "Quizzes": f"/admin/courses/{cid}/quizzes",
             "Students": f"/admin/courses/{cid}/students", "Announcements": f"/admin/courses/{cid}/announcements",
             "Reviews": f"/admin/courses/{cid}/reviews"}
    for name, path in pages.items():
        r = admin.get(path)
        assert r.status_code == 200, path
        steps = bar(r.data.decode())
        assert [clean(x) for x in labels(steps)] == ALL, path
        assert current(steps) == name, path


def test_marks_show_what_is_done_and_counts_appear(app):
    empty = make_course(app, "Empty Course")
    full = make_course(app, "Full Course", lessons=2, quizzes=1)
    admin = admin_client(app)
    e = bar(admin.get(f"/admin/courses/{empty}").data.decode())
    f = bar(admin.get(f"/admin/courses/{full}").data.decode())
    assert re.search(r'class="done" aria-label="done">✓</span>\s*Details', e)
    assert re.search(r'class="todo" aria-label="not done yet">○</span>\s*Lessons', e) and re.search(r'class="todo"[^>]*>○</span>\s*Quizzes', e)
    assert re.search(r'class="done"[^>]*>✓</span>\s*Lessons\s*<span class="small muted">\(2\)', f)
    assert re.search(r'class="done"[^>]*>✓</span>\s*Quizzes\s*<span class="small muted">\(1\)', f)
    assert "Students <span" in f and "(0)" in f.split("Students")[1][:60]


def test_the_old_setup_checklist_is_gone(app):
    cid = make_course(app)
    for path in (f"/admin/courses/{cid}", f"/admin/courses/{cid}/lessons"):
        assert "Setup checklist" not in admin_client(app).get(path).data.decode()


# --- A new course ------------------------------------------------------------------------------

def test_a_new_course_shows_only_the_details_step_with_the_others_switched_off(app):
    page = admin_client(app).get("/admin/courses/new").data.decode()
    steps = bar(page)
    assert 'aria-current="page"' in steps and steps.count("is-disabled") == 5 and steps.count('aria-disabled="true"') == 5
    assert 'title="Save the details first"' in steps
    assert "Save and continue" in page and re.search(r'name="then" value="stay"', page) is None   # no plain Save yet
    assert 'id="lessons"' not in page and "Quick add" not in page and "Quizzes and exams" not in page


def test_saving_a_new_course_creates_it_and_continues_to_the_lessons(app):
    admin = admin_client(app)
    r = admin.post("/admin/courses/new", data={"title": "Fresh Course", "level": "Beginner", "pricing": "free", "then": "next"})
    with app.app_context():
        cid = Course.query.filter_by(title="Fresh Course").one().id
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    page = admin.get(r.headers["Location"]).data.decode()
    assert "Quick add" in page and 'aria-current="page"' in bar(page) and "disabled" not in bar(page)


def test_save_and_continue_goes_to_the_lessons_and_plain_save_stays(app):
    cid = make_course(app)
    admin = admin_client(app)
    data = {"title": "Step Course", "level": "Beginner", "pricing": "free", "is_published": "1"}
    r = admin.post(f"/admin/courses/{cid}", data={**data, "then": "next"})
    assert r.headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    r = admin.post(f"/admin/courses/{cid}", data={**data, "then": "stay"}, follow_redirects=True)
    assert r.request.path == f"/admin/courses/{cid}" and b"Course saved" in r.data
    r = admin.post(f"/admin/courses/{cid}", data=data)                                  # older forms without the button
    assert r.headers["Location"].endswith(f"/admin/courses/{cid}")


def test_the_details_page_offers_both_buttons_for_an_existing_course(app):
    cid = make_course(app)
    page = admin_client(app).get(f"/admin/courses/{cid}").data.decode()
    assert "Save and continue →" in page and re.search(r'name="then" value="stay"[^>]*>Save<', page)


# --- Each step shows only its own part ----------------------------------------------------------

def test_each_step_shows_only_its_own_content(app):
    cid = make_course(app, lessons=1, quizzes=1)
    admin = admin_client(app)
    details = admin.get(f"/admin/courses/{cid}").data.decode()
    lessons = admin.get(f"/admin/courses/{cid}/lessons").data.decode()
    quizzes = admin.get(f"/admin/courses/{cid}/quizzes").data.decode()
    ann = admin.get(f"/admin/courses/{cid}/announcements").data.decode()
    assert 'id="title"' in details and 'id="lessons"' not in details and "Quick add" not in details and 'id="assessments"' not in details
    assert 'id="lessons"' in lessons and "Quick add" in lessons and 'id="title" name="title"' not in lessons and 'id="assessments"' not in lessons
    assert 'id="assessments"' in quizzes and "Quizzes and exams" in quizzes and 'id="lessons"' not in quizzes and 'id="title"' not in quizzes
    assert 'id="announcements"' in ann and "Post announcement" in ann and 'id="lessons"' not in ann


def test_next_links_walk_through_the_steps_and_the_last_step_has_none(app):
    cid = make_course(app)
    admin = admin_client(app)
    expected = {f"/admin/courses/{cid}/lessons": "Quizzes", f"/admin/courses/{cid}/quizzes": "Students",
                f"/admin/courses/{cid}/students": "Announcements", f"/admin/courses/{cid}/announcements": "Reviews"}
    for path, nxt in expected.items():
        page = admin.get(path).data.decode()
        assert f"Next: {nxt} →" in page, path
    assert "Next:" not in admin.get(f"/admin/courses/{cid}/reviews").data.decode()


def test_steps_for_switched_off_features_disappear(app):
    cid = make_course(app)
    app.config["FEATURE_ANNOUNCEMENTS"] = False
    admin = admin_client(app)
    page = admin.get(f"/admin/courses/{cid}/lessons").data.decode()
    assert "Announcements" not in bar(page) and admin.get(f"/admin/courses/{cid}/announcements").status_code == 404
    assert "Next: Reviews →" in admin.get(f"/admin/courses/{cid}/students").data.decode()
    app.config["FEATURE_REVIEWS"] = False
    assert "Reviews" not in bar(admin.get(f"/admin/courses/{cid}/lessons").data.decode())
    assert admin.get(f"/admin/courses/{cid}/reviews").status_code == 404
    assert "Next:" not in admin.get(f"/admin/courses/{cid}/students").data.decode()


def test_the_reviews_step_is_the_staff_view_of_ratings_and_feedback(app):
    cid = make_course(app)
    with app.app_context():
        u = User(email="r@example.com", full_name="Rater One")
        u.set_password("secret123")
        db.session.add(u)
        db.session.flush()
        db.session.add(Review(user_id=u.id, course_id=cid, rating=4, comment="Useful course"))
        db.session.commit()
    page = admin_client(app).get(f"/admin/courses/{cid}/reviews").data.decode()
    assert "Ratings and feedback" in page and "Useful course" in page and "Average 4.0 from 1 rating" in page and "hide</button>" in page
    assert "(1)" in bar(page).split("Reviews")[1][:80]
    public = app.test_client().get("/courses/step-course").data.decode()                  # everyone sees it on the course page
    assert "Useful course" in public


# --- Header, redirects, permissions ------------------------------------------------------------

def test_status_line_with_hide_and_show_that_returns_to_the_same_step(app):
    cid = make_course(app)
    admin = admin_client(app)
    page = admin.get(f"/admin/courses/{cid}/lessons").data.decode()
    assert "Visible to everyone" in page and "Hide it" in page and "View as a student sees it" in page
    assert f'name="next" value="/admin/courses/{cid}/lessons"' in page
    r = admin.post(f"/admin/courses/{cid}/visibility", data={"next": f"/admin/courses/{cid}/lessons"})
    assert r.headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    assert "Hidden" in admin.get(f"/admin/courses/{cid}/quizzes").data.decode() and "Show it" in admin.get(f"/admin/courses/{cid}/quizzes").data.decode()


def test_actions_return_to_the_step_they_came_from(app):
    cid = make_course(app, lessons=2)
    admin = admin_client(app)
    with app.app_context():
        lid = Lesson.query.filter_by(course_id=cid).first().id
    assert admin.post(f"/admin/courses/{cid}/sections", data={"title": "Part A"}).headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    assert admin.post(f"/admin/lessons/{lid}/move/down").headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    assert admin.post(f"/admin/lessons/{lid}/public").headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    assert admin.post(f"/admin/courses/{cid}/lessons/bulk", data={"action": "lock", "lesson_ids": [lid]}).headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    assert admin.post(f"/admin/lessons/{lid}/delete").headers["Location"].endswith(f"/admin/courses/{cid}/lessons")
    r = admin.post(f"/admin/courses/{cid}/announce", data={"title": "Hello", "body": "World"})
    assert r.headers["Location"].endswith(f"/admin/courses/{cid}/announcements")
    with app.app_context():
        ann = Announcement.query.filter_by(course_id=cid).one().id
    assert admin.post(f"/admin/announcements/{ann}/delete").headers["Location"].endswith(f"/admin/courses/{cid}/announcements")
    r = admin.post(f"/admin/courses/{cid}/assessments/new", data={"title": "Final", "pass_mark": "70"})
    assert r.status_code == 302


def test_the_lesson_and_quiz_editors_show_the_bar_with_their_step_current_and_one_h1(app):
    cid = make_course(app, lessons=1, quizzes=1)
    admin = admin_client(app)
    with app.app_context():
        lid, qid = Lesson.query.filter_by(course_id=cid).first().id, Assessment.query.filter_by(course_id=cid).first().id
    lesson = admin.get(f"/admin/lessons/{lid}").data.decode()
    quiz = admin.get(f"/admin/assessments/{qid}").data.decode()
    for page, step, back in ((lesson, "Lessons", "← Lessons"), (quiz, "Quizzes", "← Quizzes")):
        assert current(bar(page)) == step
        assert back in page and page.count("<h1") == 1
    assert "<h1>L1</h1>" in lesson and 'class="course-title">Step Course</p>' in lesson


def test_the_students_step_keeps_its_content_inside_the_bar(app):
    cid = make_course(app)
    page = admin_client(app).get(f"/admin/courses/{cid}/students").data.decode()
    assert "Enroll students" in page and "Enrolled (" in page and "<h1>Step Course</h1>" in page
    assert current(bar(page)) == "Students"


def test_delete_course_is_only_on_the_details_step_for_admins(app):
    cid = make_course(app)
    admin = admin_client(app)
    assert "Delete course" in admin.get(f"/admin/courses/{cid}").data.decode()
    for step in ("lessons", "quizzes", "students", "announcements", "reviews"):
        assert "Delete course" not in admin.get(f"/admin/courses/{cid}/{step}").data.decode()
    assert "Delete course" not in teacher_client(app).get(f"/admin/courses/{cid}").data.decode()


def test_a_courses_teacher_and_admins_can_open_every_step_and_nobody_else(app):
    cid = make_course(app)
    other = make_course(app, "Not Mine", mine=False)
    t = teacher_client(app)
    for step in ("", "/lessons", "/quizzes", "/students", "/announcements", "/reviews"):
        assert t.get(f"/admin/courses/{cid}{step}").status_code == 200, step
        assert t.get(f"/admin/courses/{other}{step}").status_code == 403, step
    with app.app_context():
        s = User(email="stud@example.com", full_name="Stu")
        s.set_password("secret123")
        db.session.add(s)
        db.session.commit()
    stud = app.test_client()
    login(stud, "stud@example.com", "secret123", area="student")
    for step in ("", "/lessons", "/quizzes", "/students", "/announcements", "/reviews"):
        assert stud.get(f"/admin/courses/{cid}{step}").status_code == 403
        assert app.test_client().get(f"/admin/courses/{cid}{step}").status_code in (302, 401, 403)


def test_a_teacher_still_sees_the_price_read_only_in_the_details_step(app):
    cid = make_course(app)
    page = teacher_client(app).get(f"/admin/courses/{cid}").data.decode()
    assert 'name="price"' not in page and "Only an admin sets the price" in page


def test_the_step_bar_wraps_on_phones_instead_of_scrolling(app):
    css = open("academy/static/style.css").read()
    assert re.search(r"\.course-steps \{ flex-wrap: wrap; overflow-x: visible;", css)
    assert ".course-steps { display: none" not in css                    # unlike the admin tab strip, it stays on phones
