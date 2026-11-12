"""Course finder (filters and sorting), richer course pages, ratings and feedback."""
import re
from decimal import Decimal

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Course, Lesson, LessonProgress, Review, User, db
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads")})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


def login(c, email, password):
    return c.post("/login", data={"email": email, "password": password})


def register(c, email="student@example.com", name="Abebe Kebede"):
    return c.post("/register", data={"full_name": name, "email": email, "password": "secret123",
                                     "password2": "secret123", "agree": "1"})


def admin_client(app):
    c = app.test_client()
    login(c, "admin@example.com", "admin12345")
    return c


def add_course(app, title, *, level="Beginner", category=None, hours=None, minutes=None, price="0",
               summary="", description="", published=True):
    with app.app_context():
        c = Course(slug=title.lower().replace(" ", "-"), title=title, level=level, category=category,
                   hours=Decimal(str(hours)) if hours else None, price=Decimal(price), summary=summary,
                   description=description, is_published=published)
        db.session.add(c)
        db.session.flush()
        if minutes:
            db.session.add(Lesson(course_id=c.id, title="Only lesson", position=1, duration_min=minutes))
        db.session.commit()
        return c.id


def page_titles(html):
    return re.findall(r'<h3><a href="/courses/[^"]+">([^<]+)</a></h3>', html)


@pytest.fixture
def catalog(app):
    """Courses of different levels, lengths, prices and topics, in addition to the two demo courses."""
    with app.app_context():
        for c in Course.query.all():
            c.is_published = False
        db.session.commit()
    add_course(app, "Alpha Basics", level="Beginner", category="Business", hours=1, price="0",
               summary="Start here")
    add_course(app, "Beta Practice", level="Intermediate", category="Business", hours=4, price="500",
               description="Covers negotiation skills in depth")
    add_course(app, "Gamma Mastery", level="Advanced", category="Technology", hours=10, price="1500")
    add_course(app, "Delta Short", level="Beginner", category="Technology", minutes=90, price="200")
    add_course(app, "Hidden Draft", published=False)
    return app


# --- Course finder --------------------------------------------------------------------------

def test_length_comes_from_stated_hours_or_lesson_durations(app):
    cid = add_course(app, "Minutes Only", minutes=150)
    nothing = add_course(app, "Unknown Length")
    with app.app_context():
        assert db.session.get(Course, cid).length_hours == 2.5
        assert db.session.get(Course, nothing).length_hours is None and db.session.get(Course, nothing).length_band is None


@pytest.mark.parametrize("hours,band", [(1.9, "short"), (2, "medium"), (6, "medium"), (6.5, "long")])
def test_length_bands(app, hours, band):
    cid = add_course(app, f"Course {hours}", hours=hours)
    with app.app_context():
        assert db.session.get(Course, cid).length_band == band


def test_catalog_lists_published_courses_and_the_count(catalog):
    page = catalog.test_client().get("/courses").data.decode()
    assert sorted(page_titles(page)) == ["Alpha Basics", "Beta Practice", "Delta Short", "Gamma Mastery"]
    assert "4 courses" in page and "Hidden Draft" not in page


@pytest.mark.parametrize("query,expected", [
    ("level=Beginner", ["Alpha Basics", "Delta Short"]),
    ("level=Advanced", ["Gamma Mastery"]),
    ("length=short", ["Alpha Basics", "Delta Short"]),
    ("length=medium", ["Beta Practice"]),
    ("length=long", ["Gamma Mastery"]),
    ("price=free", ["Alpha Basics"]),
    ("price=paid", ["Beta Practice", "Delta Short", "Gamma Mastery"]),
    ("category=Technology", ["Delta Short", "Gamma Mastery"]),
    ("category=Technology&level=Beginner", ["Delta Short"]),
    ("q=negotiation", ["Beta Practice"]),            # found in the description
    ("q=start", ["Alpha Basics"]),                   # found in the summary
    ("q=technology", ["Delta Short", "Gamma Mastery"]),  # found in the topic
])
def test_catalog_filters(catalog, query, expected):
    page = catalog.test_client().get(f"/courses?{query}&sort=title").data.decode()
    assert page_titles(page) == expected
    assert "Clear all filters" in page


def test_catalog_no_match_message_and_unknown_values_are_ignored(catalog):
    c = catalog.test_client()
    assert "No course matches these filters" in c.get("/courses?level=Beginner&price=paid&length=long").data.decode()
    page = c.get("/courses?length=nonsense&sort=nonsense").data.decode()
    assert len(page_titles(page)) == 4  # an unknown length filter is not applied


def test_catalog_sorting(catalog):
    c = catalog.test_client()
    assert page_titles(c.get("/courses?sort=title").data.decode()) == [
        "Alpha Basics", "Beta Practice", "Delta Short", "Gamma Mastery"]
    assert page_titles(c.get("/courses?sort=shortest").data.decode()) == [
        "Alpha Basics", "Delta Short", "Beta Practice", "Gamma Mastery"]
    # "newest" is the default: the last course created comes first
    assert page_titles(c.get("/courses").data.decode())[0] == "Delta Short"


def test_catalog_sorts_by_rating_and_popularity(catalog):
    with catalog.app_context():
        ids = {c.title: c.id for c in Course.query.all()}
        names = ["Ann One", "Bo Two", "Cy Three"]
        users = []
        for i, n in enumerate(names):
            u = User(full_name=n, email=f"u{i}@example.com", role="student")
            u.set_password("secret123")
            db.session.add(u)
            users.append(u)
        db.session.flush()
        for u, stars in zip(users, (5, 5, 4)):
            db.session.add(Review(user_id=u.id, course_id=ids["Gamma Mastery"], rating=stars))
        db.session.add(Review(user_id=users[0].id, course_id=ids["Alpha Basics"], rating=3))
        from academy.models import Enrollment

        for u in users:  # Beta is the most popular
            enr = Enrollment(user_id=u.id, course_id=ids["Beta Practice"])
            db.session.add(enr)
            enr.activate(source="assigned")
        db.session.commit()
    c = catalog.test_client()
    by_rating = page_titles(c.get("/courses?sort=rating").data.decode())
    assert by_rating[:2] == ["Gamma Mastery", "Alpha Basics"]
    assert page_titles(c.get("/courses?sort=popular").data.decode())[0] == "Beta Practice"
    page = c.get("/courses").data.decode()
    assert "4.7" in page and "(3)" in page  # Gamma's average and count on its card


def test_filters_use_only_published_facets(catalog):
    page = catalog.test_client().get("/courses").data.decode()
    assert '<option value="Technology"' in page and '<option value="Advanced"' in page


# --- Richer course pages -----------------------------------------------------------------------

def test_admin_saves_course_page_sections(app):
    admin = admin_client(app)
    with app.app_context():
        cid = Course.query.first().id
    admin.post(f"/admin/courses/{cid}", data={
        "title": "Project Management Fundamentals", "price": "2500", "is_published": "1",
        "learn_points": "- Plan a project\n* Manage risks\n\n  Report status  ",
        "audience": "Team leaders", "prerequisites": "A laptop"})
    with app.app_context():
        c = db.session.get(Course, cid)
        assert c.learn_list == ["Plan a project", "Manage risks", "Report status"]
        assert (c.audience, c.prerequisites) == ("Team leaders", "A laptop")
    form = admin.get(f"/admin/courses/{cid}").data.decode()
    assert "Team leaders" in form and "A laptop" in form


def test_course_page_shows_sections_includes_and_syllabus(app):
    page = app.test_client().get("/courses/project-management-fundamentals").data.decode()
    assert "What you'll learn" in page and "Define a project&#39;s scope" in page or "Define a project's scope" in page
    assert "Who this course is for" in page and "Before you start" in page
    assert "Syllabus" in page and "22 min" in page            # lesson durations in the syllabus
    assert "lessons (1 video)" in page and "Certificate" in page and "quiz" in page
    assert "12 h of training" in page


def test_course_page_hides_sections_that_are_empty(app):
    cid = add_course(app, "Bare Course")
    page = app.test_client().get("/courses/bare-course").data.decode()
    assert "What you&#39;ll learn" not in page and "What you'll learn" not in page
    assert "Who this course is for" not in page and "Before you start" not in page
    assert "Lessons coming soon" in page


# --- Ratings and feedback ------------------------------------------------------------------------

def pm_ids(app):
    with app.app_context():
        c = Course.query.filter_by(slug="project-management-fundamentals").one()
        return c.id, [l.id for l in c.lessons]


def enrolled_student(app, email="student@example.com", name="Abebe Kebede", lessons_done=1):
    cid, lessons = pm_ids(app)
    c = app.test_client()
    register(c, email=email, name=name)
    c.post(f"/pay/course/{cid}/demo")
    for lid in lessons[:lessons_done]:
        c.post(f"/learn/lesson/{lid}/complete")
    return c


def test_cannot_review_before_completing_a_lesson(app):
    c = enrolled_student(app, lessons_done=0)
    page = c.get("/courses/project-management-fundamentals").data.decode()
    assert "after completing your first lesson" in page and "Submit review" not in page
    r = c.post("/courses/project-management-fundamentals/review", data={"rating": "5"}, follow_redirects=True)
    assert b"once you are enrolled and have completed a lesson" in r.data
    with app.app_context():
        assert Review.query.count() == 0


def test_not_enrolled_students_and_visitors_cannot_review(app):
    c = app.test_client()
    register(c)
    assert b"Submit review" not in c.get("/courses/project-management-fundamentals").data
    c.post("/courses/project-management-fundamentals/review", data={"rating": "5"})
    anon = app.test_client()
    assert anon.post("/courses/project-management-fundamentals/review", data={"rating": "5"}).status_code == 302
    with app.app_context():
        assert Review.query.count() == 0


def test_staff_cannot_review(app):
    admin = admin_client(app)
    assert b"Submit review" not in admin.get("/courses/project-management-fundamentals").data
    admin.post("/courses/project-management-fundamentals/review", data={"rating": "5"})
    with app.app_context():
        assert Review.query.count() == 0


def test_student_rates_course_and_the_page_shows_it(app):
    c = enrolled_student(app)
    page = c.get("/courses/project-management-fundamentals").data.decode()
    assert "Rate this course" in page and "Submit review" in page
    r = c.post("/courses/project-management-fundamentals/review",
               data={"rating": "4", "comment": "Clear and practical."}, follow_redirects=True)
    assert b"Thank you for your feedback" in r.data
    page = r.data.decode()
    assert "Clear and practical." in page and "Abebe K." in page and "Abebe Kebede" not in page.split('id="reviews"')[1]
    assert "1 rating" in page and "4.0" in page and "Your review" in page
    with app.app_context():
        assert Review.query.one().rating == 4


def test_one_review_per_student_and_it_can_be_updated_or_removed(app):
    c = enrolled_student(app)
    url = "/courses/project-management-fundamentals/review"
    c.post(url, data={"rating": "2", "comment": "Meh"})
    c.post(url, data={"rating": "5", "comment": "Much better after the quiz"})
    with app.app_context():
        r = Review.query.one()
        assert (r.rating, r.comment) == (5, "Much better after the quiz")
    c.post(url + "/delete")
    with app.app_context():
        assert Review.query.count() == 0


@pytest.mark.parametrize("rating", ["", "0", "6", "abc", "-1"])
def test_invalid_ratings_are_rejected(app, rating):
    c = enrolled_student(app)
    r = c.post("/courses/project-management-fundamentals/review", data={"rating": rating}, follow_redirects=True)
    assert b"rating from 1 to 5" in r.data
    with app.app_context():
        assert Review.query.count() == 0


def test_average_and_distribution(app):
    for i, stars in enumerate((5, 5, 4, 1)):
        c = enrolled_student(app, email=f"s{i}@example.com", name=f"Student {chr(65 + i)}")
        c.post("/courses/project-management-fundamentals/review", data={"rating": str(stars)})
    page = app.test_client().get("/courses/project-management-fundamentals").data.decode()
    assert "3.8" in page and "4 ratings" in page          # (5+5+4+1)/4 = 3.75
    assert 'style="width:50%"' in page                     # two of four reviews are 5 stars


def test_reviews_without_comment_are_counted_but_not_listed(app):
    c = enrolled_student(app)
    c.post("/courses/project-management-fundamentals/review", data={"rating": "5"})
    page = app.test_client().get("/courses/project-management-fundamentals").data.decode()
    assert "1 rating" in page and 'class="review"' not in page


def test_admin_can_hide_show_and_delete_reviews(app):
    c = enrolled_student(app)
    c.post("/courses/project-management-fundamentals/review", data={"rating": "1", "comment": "Rude words here"})
    with app.app_context():
        rid = Review.query.one().id
    admin = admin_client(app)
    assert "Rude words here" in admin.get("/admin/courses/1").data.decode()
    admin.post(f"/admin/reviews/{rid}", data={"action": "hide"})
    public = app.test_client().get("/courses/project-management-fundamentals").data.decode()
    assert "Rude words here" not in public and "No ratings yet" in public
    assert "hidden" in admin.get("/admin/courses/1").data.decode()
    admin.post(f"/admin/reviews/{rid}", data={"action": "show"})
    assert "Rude words here" in app.test_client().get("/courses/project-management-fundamentals").data.decode()
    admin.post(f"/admin/reviews/{rid}", data={"action": "delete"})
    with app.app_context():
        assert Review.query.count() == 0


def test_only_the_courses_own_instructor_or_admin_can_moderate(app):
    c = enrolled_student(app)
    c.post("/courses/project-management-fundamentals/review", data={"rating": "3", "comment": "ok"})
    with app.app_context():
        rid = Review.query.one().id
        pm = Course.query.filter_by(slug="project-management-fundamentals").one()
        pm.instructor_id = User.query.filter_by(email="admin@example.com").one().id  # not the instructor's
        db.session.commit()
        pm_id = pm.id
    ins = app.test_client()
    login(ins, "instructor@example.com", "instructor123")
    assert ins.post(f"/admin/reviews/{rid}", data={"action": "delete"}).status_code == 403
    assert app.test_client().post(f"/admin/reviews/{rid}", data={"action": "delete"}).status_code == 302
    with app.app_context():
        assert Review.query.count() == 1
        db.session.get(Course, pm_id).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
    assert ins.post(f"/admin/reviews/{rid}", data={"action": "hide"}).status_code == 302


def test_reviews_can_be_switched_off(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "u"), "FEATURE_REVIEWS": False})
    app = create_app(cfg)
    with app.app_context():
        seed()
    c = enrolled_student(app)
    page = c.get("/courses/project-management-fundamentals").data.decode()
    assert "Ratings and feedback" not in page
    assert c.post("/courses/project-management-fundamentals/review", data={"rating": "5"}).status_code == 404
    assert "Highest rated" not in c.get("/courses").data.decode()
    assert "Ratings and feedback" not in admin_client(app).get("/admin/courses/1").data.decode()


def test_deleting_a_course_removes_its_reviews(app):
    c = enrolled_student(app)
    c.post("/courses/project-management-fundamentals/review", data={"rating": "5"})
    cid = add_course(app, "Throwaway")
    with app.app_context():
        u = User.query.filter_by(email="student@example.com").one()
        db.session.add(Review(user_id=u.id, course_id=cid, rating=2))
        db.session.commit()
    admin_client(app).post(f"/admin/courses/{cid}/delete")
    with app.app_context():
        assert Review.query.count() == 1


def test_migration_round_trip_for_review_tables(tmp_path):
    from flask_migrate import downgrade, upgrade

    from academy.config import Config

    cfg = type("Cfg", (Config,), {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "m.db"),
                                  "UPLOAD_DIR": str(tmp_path / "up")})
    app = create_app(cfg)
    with app.app_context():
        upgrade()
        downgrade(revision="62d0bef7c5e1")
        upgrade()


def test_logo_mark_hidden_from_screen_readers_and_footer_year(app):
    from datetime import datetime, timezone

    html = app.test_client().get("/").get_data(as_text=True)
    assert html.count('<span class="mark" aria-hidden="true">') == 2
    assert f"© {datetime.now(timezone.utc).year} " in html


# --- Homepage hero panel ----------------------------------------------------------------------

def hero_panel(app, path="/"):
    html = app.test_client().get(path).get_data(as_text=True)
    m = re.search(r'<aside class="hero-panel".*?</aside>', html, re.S)
    return m.group(0) if m else None


def test_hero_panel_lists_real_features_without_numbers(app):
    panel = hero_panel(app)
    assert panel and "What you get" in panel
    assert not re.search(r"\d", re.sub(r"<[^>]+>", "", panel))
    assert "certificate" in panel and "English or Amharic" in panel


def test_hero_panel_hides_lines_for_disabled_features(app):
    app.config["FEATURE_CERTIFICATES"] = False
    assert "certificate" not in hero_panel(app)
    app.config["LANGUAGES"] = ["en"]
    assert "Amharic" not in hero_panel(app)


def test_hero_panel_in_amharic(app):
    c = app.test_client()
    c.set_cookie("lang", "am")
    panel = re.search(r'<aside class="hero-panel".*?</aside>', c.get("/").get_data(as_text=True), re.S).group(0)
    assert "የሚያገኙት" in panel and "What you get" not in panel


# --- Empty states -----------------------------------------------------------------------------

def unpublish_all(app):
    with app.app_context():
        for c in Course.query.all():
            c.is_published = False
        db.session.commit()


def test_homepage_empty_state_shown_without_published_courses(app):
    unpublish_all(app)
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'class="empty-panel"' in html and "Courses are on their way" in html
    assert "Create a free account" in html and "/contact" in html


def test_homepage_empty_state_hidden_when_a_course_is_published(app):
    assert 'class="empty-panel"' not in app.test_client().get("/").get_data(as_text=True)


def test_homepage_empty_state_ignores_draft_courses(app):
    unpublish_all(app)
    add_course(app, "Secret Draft", published=False)
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'class="empty-panel"' in html and "Secret Draft" not in html


def test_empty_state_hides_signup_for_signed_in_users(app):
    unpublish_all(app)
    c = app.test_client()
    register(c)
    html = c.get("/").get_data(as_text=True)
    panel = re.search(r'<div class="empty-panel">.*?</div>\s*</div>', html, re.S).group(0)
    assert "btn" in panel and "/register" not in panel and "Contact us" in panel


def test_empty_state_in_amharic(app):
    unpublish_all(app)
    c = app.test_client()
    c.set_cookie("lang", "am")
    html = c.get("/").get_data(as_text=True)
    assert "ኮርሶች በቅርቡ ይመጣሉ" in html and "Courses are on their way" not in html


def test_how_it_works_section_has_an_anchor(app):
    assert 'id="how-it-works"' in app.test_client().get("/").get_data(as_text=True)


# --- Meta description and link previews -------------------------------------------------------

def meta(html, key, attr="name"):
    m = re.search(rf'<meta {attr}="{key}" content="([^"]*)"', html)
    return m.group(1) if m else None


def test_homepage_has_description_and_open_graph(app):
    html = app.test_client().get("/").get_data(as_text=True)
    for key, attr in (("description", "name"), ("og:title", "property"), ("og:description", "property"),
                      ("og:url", "property")):
        assert meta(html, key, attr), key
    assert "og:image" not in html


def test_course_page_uses_the_course_summary_with_html_stripped(app):
    add_course(app, "Meta Course", summary="Learn <b>fast</b> &amp; well")
    html = app.test_client().get("/courses/meta-course").get_data(as_text=True)
    assert meta(html, "description") == "Learn fast &amp; well"
    assert meta(html, "og:description", "property") == "Learn fast &amp; well"
    assert meta(html, "og:title", "property").startswith("Meta Course")
    assert "<b>fast</b>" not in html.split("</head>")[0]


def test_course_without_summary_falls_back_to_site_description(app):
    add_course(app, "Plain Course")
    html = app.test_client().get("/courses/plain-course").get_data(as_text=True)
    assert meta(html, "description")
