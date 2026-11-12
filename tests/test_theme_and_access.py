"""The sage-and-emerald theme, the combined log in / create account page, Enroll buttons on every
course, the redesigned home page and student dashboard."""
import os
import re

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Course, Enrollment, User, db
from academy.seed import seed

CSS = open(os.path.join(os.path.dirname(__file__), "..", "academy", "static", "style.css"), encoding="utf-8").read()


def make_app(tmp_path, **overrides):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads"), **overrides})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def ids(app):
    with app.app_context():
        pm = Course.query.filter_by(slug="project-management-fundamentals").one()
        xl = Course.query.filter_by(slug="excel-for-the-workplace").one()
        return pm.id, xl.id


def register(c, email="student@example.com", name="Abebe Kebede", nxt=None):
    url = "/register" + (f"?next={nxt}" if nxt else "")
    return c.post(url, data={"full_name": name, "email": email, "password": "secret123",
                             "password2": "secret123", "agree": "1"})


def header(html):
    return re.search(r"<header.*?</header>", html, re.S).group(0)


# --- Palette and accessibility ---------------------------------------------------------------

def token(name):
    return re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", CSS).group(1).upper()


def luminance(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_the_requested_palette_is_the_theme():
    expected = {"emerald": "#287D63", "sage": "#E7F3EB", "off-white": "#FAFCFA", "forest": "#20352E",
                "muted-green": "#42594E", "gold": "#E8B96A", "white": "#FFFFFF"}
    for name, value in expected.items():
        assert token(name) == value, name


@pytest.mark.parametrize("fg,bg,minimum", [
    ("forest", "off-white", 7), ("forest", "white", 7), ("forest", "sage", 7),     # headings and body text
    ("muted-green", "white", 4.5), ("muted-green", "off-white", 4.5), ("muted-green", "sage", 4.5),  # secondary text
    ("emerald", "white", 4.5), ("emerald", "off-white", 4.5),                     # links
    ("white", "emerald", 4.5),                                                    # button text
])
def test_text_colours_are_readable(fg, bg, minimum):
    assert contrast(token(fg), token(bg)) >= minimum, (fg, bg)


def test_derived_text_colours_are_readable():
    assert contrast(token("brand-dark"), token("sage")) >= 4.5     # badges and hover on sage
    assert contrast(token("accent-ink"), "#FBF1DC") >= 4.5          # text on the gold tint
    assert contrast(token("err"), "#FBECE9") >= 4.5                 # error text on its tint


def test_gold_is_only_decoration():
    # Gold on white is below the 3:1 needed even for large text, so it must never colour running
    # text. It may fill shapes (stars, underline, borders) and colour the star glyphs.
    assert contrast(token("gold"), token("white")) < 3
    text_colour = re.compile(r"(?<![-\w])color:\s*var\(--gold\)")
    offenders = [selector.strip() for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
                 if text_colour.search(body) and ".stars" not in selector and ".starpick" not in selector]
    assert offenders == []


def test_layout_is_mobile_first():
    assert "max-width: 899px" not in CSS.split("/* 3")[0]
    queries = re.findall(r"@media \(([a-z-]+):", CSS)
    assert queries.count("min-width") > queries.count("max-width") * 5


# --- BRAND_COLOR ------------------------------------------------------------------------------------

def test_default_colour_comes_from_the_stylesheet_not_an_inline_override(app):
    page = app.test_client().get("/").data.decode()
    assert "--brand:" not in page and 'content="#287D63"' in page   # theme-color for phone browsers


def test_old_blue_default_in_an_existing_settings_file_is_ignored(tmp_path):
    app = make_app(tmp_path, BRAND_COLOR="#1f4fd1")
    assert app.config["BRAND_COLOR"] == "" and "--brand:" not in app.test_client().get("/").data.decode()


def test_a_custom_brand_colour_still_works(tmp_path):
    app = make_app(tmp_path, BRAND_COLOR="#7A3E9D")
    page = app.test_client().get("/").data.decode()
    assert "--brand: #7A3E9D" in page and "color-mix" in page


def test_stylesheet_and_theme_hook_are_linked(app):
    page = app.test_client().get("/").data.decode()
    assert "/static/style.css" in page and "classList.add('js')" in page


# --- One page for logging in and creating an account -----------------------------------------------

def test_guest_header_has_a_single_entry_point(app):
    head = header(app.test_client().get("/").data.decode())
    assert head.count('href="/login"') == 1 and 'href="/register"' not in head
    assert "Log in" in head and ">Register<" not in head


def test_both_addresses_show_the_same_page_with_the_right_tab(app):
    c = app.test_client()
    login = c.get("/login").data.decode()
    reg = c.get("/register").data.decode()
    for page in (login, reg):
        assert 'class="auth-tabs"' in page and page.count('role="tab"') == 2
    assert re.search(r'href="/login"[^>]*aria-selected="true"', login) and 'name="remember"' in login
    assert re.search(r'href="/register"[^>]*aria-selected="true"', reg) and 'name="agree"' in reg
    assert 'name="full_name"' not in login and 'name="remember"' not in reg
    assert "Create an account" in login and "Already registered?" in reg


def test_switching_tabs_keeps_where_the_visitor_was_going(app):
    page = app.test_client().get("/login?next=/pay/course/2").data.decode()
    assert 'href="/register?next=/pay/course/2"' in page
    page = app.test_client().get("/register?next=/pay/course/2").data.decode()
    assert 'href="/login?next=/pay/course/2"' in page
    assert 'action="/register?next=/pay/course/2"' in page


def test_errors_keep_the_visitor_on_the_right_tab(app):
    c = app.test_client()
    bad_login = c.post("/login", data={"email": "nobody@example.com", "password": "x"}).data.decode()
    assert re.search(r'href="/login"[^>]*aria-selected="true"', bad_login) and "Incorrect email or password" in bad_login
    bad_reg = c.post("/register", data={"full_name": "x", "email": "bad", "password": "1", "password2": "2"})
    assert bad_reg.status_code == 400 and re.search(r'href="/register"[^>]*aria-selected="true"', bad_reg.data.decode())


def test_next_is_followed_after_registering_and_logging_in_but_never_off_site(app):
    pm, _ = ids(app)
    c = app.test_client()
    r = register(c, nxt=f"/pay/course/{pm}")
    assert r.status_code == 302 and r.location.endswith(f"/pay/course/{pm}")
    c.post("/logout")
    r = c.post("/login?next=/courses", data={"email": "student@example.com", "password": "secret123"})
    assert r.location.endswith("/courses")
    c.post("/logout")
    r = c.post("/login?next=//evil.example", data={"email": "student@example.com", "password": "secret123"})
    assert "evil.example" not in r.location


# --- Enroll on every course ---------------------------------------------------------------------------

def card_buttons(html):
    return re.findall(r'<div class="actions">(.*?)</div>', html, re.S)


def test_guests_see_enroll_on_every_card_and_are_sent_to_create_an_account(app):
    pm, xl = ids(app)
    for url in ("/", "/courses"):
        page = app.test_client().get(url).data.decode()
        buttons = card_buttons(page)
        assert len(buttons) == page.count('class="card course-card"') >= 2, url
        assert all(">Enroll<" in b for b in buttons)
        assert f'href="/register?next=/pay/course/{pm}"' in page            # paid: back to payment
        assert 'href="/register?next=/courses/excel-for-the-workplace"' in page  # free: back to the course


def test_signed_in_students_enroll_from_the_card(app):
    pm, xl = ids(app)
    c = app.test_client()
    register(c)
    page = c.get("/courses").data.decode()
    assert f'href="/pay/course/{pm}">Enroll</a>' in page                                   # paid: to checkout
    assert re.search(rf'<form method="post" action="/pay/course/{xl}"><input type="hidden" name="_csrf"[^>]*><button class="btn small">Enroll</button>', page)
    c.post(f"/pay/course/{xl}")                                                                   # free course: one click
    after = c.get("/courses").data.decode()
    assert re.search(r'Continue learning</a>', after) and f'action="/pay/course/{xl}"' not in after


def test_enrolled_and_staff_see_continue_learning_instead(app):
    pm, _ = ids(app)
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    page = c.get("/").data.decode()
    assert "Continue learning" in page and f'href="/pay/course/{pm}">Enroll' not in page
    admin = app.test_client()
    admin.post("/login", data={"email": "admin@example.com", "password": "admin12345"})
    assert ">Enroll<" not in admin.get("/courses").data.decode()


def test_enroll_forms_work_for_guests_end_to_end(app):
    pm, xl = ids(app)
    c = app.test_client()
    # free course: create an account, land on the course, enroll
    r = register(c, nxt="/courses/excel-for-the-workplace")
    assert r.location.endswith("/courses/excel-for-the-workplace")
    assert "Enroll for free" in c.get(r.location).data.decode()
    c.post(f"/pay/course/{xl}")
    with app.app_context():
        assert Enrollment.query.filter_by(course_id=xl, status="active").count() == 1
    # paid course, new guest: create an account and land on payment
    d = app.test_client()
    r = register(d, email="other@example.com", nxt=f"/pay/course/{pm}")
    page = d.get(r.location).data.decode()
    assert "Course fee:" in page and "Submit receipt" in page


def test_course_page_asks_guests_to_sign_up_with_the_same_enroll_button(app):
    page = app.test_client().get("/courses/project-management-fundamentals").data.decode()
    pm, _ = ids(app)
    assert page.count(f'href="/register?next=/pay/course/{pm}"') == 2       # side card and the phone bar
    assert "Enroll now" in page and "Register to enroll" not in page
    assert "You will be asked to log in or create a free account." in page
    free = app.test_client().get("/courses/excel-for-the-workplace").data.decode()
    assert "Enroll for free" in free and "next=/courses/excel-for-the-workplace" in free


def test_enroll_buttons_in_amharic(app):
    c = app.test_client()
    c.set_cookie("lang", "am")
    page = c.get("/courses").data.decode()
    assert page.count("ይመዝገቡ") >= 2 and ">Enroll<" not in page
    login = c.get("/login").data.decode()
    assert "እንኳን ደህና መጡ" in login and "መለያ ይፍጠሩ" in login and "Welcome back" not in login


# --- Home page and dashboard ---------------------------------------------------------------------------

def test_home_page_sections(app):
    page = app.test_client().get("/").data.decode()
    assert 'class="hero-search"' in page and 'name="q"' in page
    assert 'href="/courses?category=Business+%26+Management"' in page and "Browse by topic" in page
    assert "How it works" in page and 'class="cta-band"' in page and "Ready to start learning?" in page


def test_home_page_for_signed_in_students_has_no_signup_prompts(app):
    c = app.test_client()
    register(c)
    page = c.get("/").data.decode()
    assert 'class="cta-band"' not in page and 'class="hero-actions"' not in page and "Create a free account" not in page


def test_free_courses_get_their_own_section_when_not_already_shown(tmp_path):
    app = make_app(tmp_path)
    with app.app_context():
        for i in range(7):  # push the free course out of the six "latest" cards
            db.session.add(Course(slug=f"c{i}", title=f"Newer {i}", price=500, is_published=True))
        db.session.commit()
    page = app.test_client().get("/").data.decode()
    assert "Start with a free course" in page and "Excel for the Workplace" in page


def test_dashboard_shows_stats_and_the_next_lesson(app):
    pm, _ = ids(app)
    with app.app_context():
        lessons = [l.id for l in db.session.get(Course, pm).lessons]
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    c.post(f"/learn/lesson/{lessons[0]}/complete")
    page = c.get("/dashboard").data.decode()
    assert re.search(r"<b>1</b><span>Courses in progress", page) and re.search(r"<b>1</b><span>Lessons completed", page)
    assert re.search(r"<b>0</b><span>Courses completed", page)
    assert "Next: Scope and the work breakdown structure" in page and f'href="/learn/lesson/{lessons[1]}"' in page


def test_dashboard_marks_finished_courses(app):
    pm, _ = ids(app)
    with app.app_context():
        c0 = db.session.get(Course, pm)
        lessons = [l.id for l in c0.lessons]
        quiz = c0.assessments[0]
        right = {f"q{q.id}": str(q.correct_index) for q in quiz.questions}
        qid = quiz.id
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    for lid in lessons:
        c.post(f"/learn/lesson/{lid}/complete")
    r = c.post(f"/assessments/{qid}/start", follow_redirects=True)
    c.post(r.request.path, data=right)
    page = c.get("/dashboard").data.decode()
    assert re.search(r"<b>1</b><span>Courses completed", page) and "Completed" in page
    assert re.search(r"<b>1</b><span>Certificates earned", page) and "Next:" not in page


def test_empty_dashboard_invites_the_student_to_browse(app):
    c = app.test_client()
    register(c)
    page = c.get("/dashboard").data.decode()
    assert "You are not enrolled in any course yet." in page and re.search(r"<b>0</b><span>Courses in progress", page)


# --- Mobile --------------------------------------------------------------------------------------------

def test_signed_in_users_get_a_bottom_tab_bar_on_phones_and_guests_do_not(app):
    guest = app.test_client().get("/").data.decode()
    assert 'class="tabbar"' not in guest and "has-tabbar" not in guest
    c = app.test_client()
    register(c)
    page = c.get("/dashboard").data.decode()
    assert 'class="tabbar"' in page and "has-tabbar" in page
    assert re.search(r'href="/dashboard"[^>]*aria-current="page"', page.split('class="tabbar"')[1])
    admin = app.test_client()
    admin.post("/login", data={"email": "admin@example.com", "password": "admin12345"})
    assert "Admin" in admin.get("/").data.decode().split('class="tabbar"')[1]


def test_phone_menu_and_search_are_present_with_accessible_labels(app):
    page = app.test_client().get("/").data.decode()
    assert 'class="nav-toggle"' in page and 'aria-controls="site-nav"' in page and 'aria-expanded="false"' in page
    assert 'role="search"' in page and 'class="sr" href="#main"' in page and 'id="main"' in page


def test_sticky_enroll_bar_only_for_visitors_who_can_still_enroll(app):
    pm, _ = ids(app)
    assert "mobile-cta" in app.test_client().get("/courses/project-management-fundamentals").data.decode()
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    page = c.get("/courses/project-management-fundamentals").data.decode()
    assert 'class="mobile-cta"' not in page and "has-mobile-cta" not in page


def test_lesson_list_collapses_on_phones(app):
    pm, _ = ids(app)
    with app.app_context():
        lid = db.session.get(Course, pm).lessons[0].id
    page = app.test_client().get(f"/learn/lesson/{lid}").data.decode()
    assert '<details class="sidebar card" open data-collapse-narrow>' in page and "Course content" in page


def test_catalog_filters_and_search_share_one_form(app):
    page = app.test_client().get("/courses").data.decode()
    assert 'id="finder"' in page and page.count('form="finder"') >= 4 and 'data-toggle="#filters"' in page
    assert "Filters" in page


def test_field_outlines_and_placeholders_are_visible():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "academy" / "static" / "style.css").read_text()
    line = re.search(r"--line-input:\s*(#[0-9A-Fa-f]{6})", css).group(1)
    placeholder = re.search(r"::placeholder \{ color: (#[0-9A-Fa-f]{6})", css).group(1)
    for bg in ("#FFFFFF", token("sage")):
        assert contrast(line, bg) >= 3
    assert contrast(placeholder, "#FFFFFF") >= 4.5
