"""Admin navigation: clear names (Browse courses on the public site, Manage courses in the admin),
one phone menu in two groups, the admin bottom bar, and the "Administration" marker."""
import re

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import ContactMessage, User, db
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


def admin_client(app, lang=None):
    c = app.test_client()
    login(c, "admin@example.com", "admin12345", area="staff")
    if lang:
        c.set_cookie("lang", lang)
    return c


def teacher_client(app):
    c = app.test_client()
    login(c, "instructor@example.com", "instructor123", area="staff")
    return c


def student_client(app):
    c = app.test_client()
    c.post("/register", data={"full_name": "Abebe Kebede", "email": "stu@example.com", "password": "secret123",
                              "password2": "secret123", "agree": "1"})
    return c


def part(html, start, end):
    return html.split(start, 1)[1].split(end, 1)[0]


def link_texts(fragment):
    return [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<a [^>]*>(.*?)</a>", fragment, re.S)]


ADMIN_TABS = ["Overview", "Manage courses", "Manage students", "Manage staff", "Payments", "Reports", "Messages"]


# --- Names ------------------------------------------------------------------------------------

def test_public_pages_say_browse_courses_everywhere(app):
    guest = app.test_client()
    home = guest.get("/").data.decode()
    assert "Browse courses" in part(home, 'id="site-nav"', "</nav>")
    assert "Browse courses" in part(home, '<footer class="site-footer">', "</footer>")
    catalog = guest.get("/courses").data.decode()
    assert "<h1>Browse courses</h1>" in catalog and "<title>Browse courses ·" in catalog
    for page in (home, catalog):
        assert ">Courses<" not in part(page, 'id="site-nav"', "</nav>")


def test_public_bottom_bar_says_browse_for_signed_in_people(app):
    bar = part(student_client(app).get("/").data.decode(), '<nav class="tabbar"', "</nav>")
    assert link_texts(bar) == ["Home", "Browse", "My learning", "Profile"]


def test_staff_on_a_public_page_keep_the_usual_bar_with_one_admin_entry(app):
    bar = part(admin_client(app).get("/").data.decode(), '<nav class="tabbar"', "</nav>")
    assert link_texts(bar) == ["Home", "Browse", "Admin", "Profile"]
    bar = part(teacher_client(app).get("/").data.decode(), '<nav class="tabbar"', "</nav>")
    assert link_texts(bar) == ["Home", "Browse", "Teaching", "Profile"]


def test_amharic_public_labels(app):
    c = student_client(app)
    c.set_cookie("lang", "am")
    page = c.get("/").data.decode()
    assert "ኮርሶችን ይመልከቱ" in part(page, 'id="site-nav"', "</nav>")
    assert "ያስሱ" in part(page, '<nav class="tabbar"', "</nav>") and "Browse" not in part(page, '<nav class="tabbar"', "</nav>")


# --- Tabs -------------------------------------------------------------------------------------

def test_admin_tabs_in_the_agreed_order_with_the_new_names(app):
    page = admin_client(app).get("/admin/").data.decode()
    assert link_texts(part(page, 'class="tabs admin-tabs"', "</nav>")) == ADMIN_TABS


def test_a_teacher_has_only_overview_and_manage_courses(app):
    page = teacher_client(app).get("/admin/").data.decode()
    assert link_texts(part(page, 'class="tabs admin-tabs"', "</nav>")) == ["Overview", "Manage courses"]
    assert "Payments" not in part(page, 'id="site-nav"', "</nav>") and "Manage students" not in page


def test_page_headings_match_the_tab_names(app):
    admin = admin_client(app)
    for path, heading in (("/admin/courses", "Manage courses"), ("/admin/students", "Manage students"),
                          ("/admin/staff", "Manage staff"), ("/admin/payments", "Payments"),
                          ("/admin/reports", "Reports"), ("/admin/messages", "Messages")):
        page = admin.get(path).data.decode()
        assert f"<h1>{heading}</h1>" in page, path
        assert f"<title>{heading} · Admin</title>" in page or heading in ("Payments", "Reports", "Messages"), path


def test_the_current_tab_is_marked_on_every_admin_page(app):
    admin = admin_client(app)
    with app.app_context():
        student_id = User.query.filter(User.email == "admin@example.com").one().id
        db.session.add(ContactMessage(name="N", email="n@example.com", message="hello"))
        db.session.commit()
    expectations = {"/admin/": "Overview", "/admin/courses": "Manage courses", "/admin/courses/new": "Manage courses",
                    "/admin/categories": "Manage courses", "/admin/students": "Manage students",
                    "/admin/staff": "Manage staff", f"/admin/users/{student_id}": "Manage staff",
                    "/admin/payments": "Payments", "/admin/payments/details": "Payments",
                    "/admin/reports": "Reports", "/admin/messages": "Messages"}
    for path, label in expectations.items():
        page = admin.get(path).data.decode()
        # the phone menu is on every admin page; the tab strip is on the list pages
        group = part(page, 'class="phone-only nav-group"', "</div>")
        assert re.findall(r'aria-current="page"[^>]*>([^<]+)', group) == [label], path
        if 'class="tabs admin-tabs"' in page:
            tabs = part(page, 'class="tabs admin-tabs"', "</nav>")
            assert re.findall(r'aria-current="page"[^>]*>([^<]+)', tabs) == [label], path


def test_a_students_detail_page_highlights_manage_students(app):
    with app.app_context():
        u = User(email="s2@example.com", full_name="S Two")
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    group = part(admin_client(app).get(f"/admin/users/{uid}").data.decode(), 'class="phone-only nav-group"', "</div>")
    assert re.findall(r'aria-current="page"[^>]*>([^<]+)', group) == ["Manage students"]


def test_messages_keep_their_unread_badge_next_to_reports(app):
    with app.app_context():
        for i in range(3):
            db.session.add(ContactMessage(name=f"N{i}", email="n@example.com", message="hello"))
        db.session.commit()
    page = admin_client(app).get("/admin/").data.decode()
    tabs = part(page, 'class="tabs admin-tabs"', "</nav>")
    assert tabs.index("Reports") < tabs.index("Messages") and 'class="nav-badge">3<' in tabs
    assert 'class="nav-badge">3<' in part(page, 'class="phone-only nav-group"', "</div>")


# --- The phone menu ---------------------------------------------------------------------------

def test_the_phone_menu_in_admin_has_a_website_group_and_an_administration_group(app):
    menu = part(admin_client(app).get("/admin/courses").data.decode(), 'id="site-nav"', "</nav>")
    assert 'class="nav-heading phone-only">Website<' in menu and 'class="nav-heading phone-only">Administration<' in menu
    group = part(menu, 'class="phone-only nav-group"', "</div>")
    assert link_texts(group) == ADMIN_TABS
    website = menu.split("Administration<")[0]
    assert {"Home", "Browse courses", "Contact"} <= set(link_texts(website))
    assert 'class="nav-single-admin"' in menu                              # hidden on phones by CSS, kept for desktop


def test_a_teachers_phone_menu_lists_only_their_two_links(app):
    menu = part(teacher_client(app).get("/admin/").data.decode(), 'id="site-nav"', "</nav>")
    assert link_texts(part(menu, 'class="phone-only nav-group"', "</div>")) == ["Overview", "Manage courses"]


def test_public_pages_do_not_get_the_administration_group_even_for_staff(app):
    menu = part(admin_client(app).get("/courses").data.decode(), 'id="site-nav"', "</nav>")
    assert "Administration" not in menu and "phone-only" not in menu and "Admin<" in menu


def test_students_and_guests_never_see_admin_links(app):
    for client in (app.test_client(), student_client(app)):
        page = client.get("/courses").data.decode()
        assert "Manage courses" not in page and "Administration" not in page


# --- The marker and the bottom bar inside admin ------------------------------------------------

def test_admin_pages_show_the_administration_line_with_a_link_to_the_website(app):
    page = admin_client(app).get("/admin/courses").data.decode()
    assert re.search(r'<div class="admin-bar"><div class="container">Administration · <a href="/">View website</a>', page)
    assert 'class="admin-bar"' not in admin_client(app).get("/courses").data.decode()
    assert 'class="admin-bar"' not in app.test_client().get("/").data.decode()


def test_the_bottom_bar_inside_admin_is_the_admin_bar(app):
    bar = part(admin_client(app).get("/admin/payments").data.decode(), '<nav class="tabbar"', "</nav>")
    assert link_texts(bar) == ["Overview", "Manage courses", "Payments", "Website"]
    assert re.findall(r'aria-current="page"[^>]*>(?:<svg.*?</svg>)?([^<]+)', bar) == ["Payments"]
    teacher_bar = part(teacher_client(app).get("/admin/").data.decode(), '<nav class="tabbar"', "</nav>")
    assert link_texts(teacher_bar) == ["Overview", "Manage courses", "Website"]
    assert 'href="/"' in bar and 'aria-label="Administration"' in bar


# --- Language, content and style stay as agreed -------------------------------------------------

def test_admin_labels_stay_english_while_public_items_follow_the_language(app):
    page = admin_client(app, "am").get("/admin/courses").data.decode()
    menu = part(page, 'id="site-nav"', "</nav>")
    assert "ኮርሶችን ይመልከቱ" in menu and "Manage courses" in menu and "Administration" in menu
    assert "Manage students" in part(page, 'class="tabs admin-tabs"', "</nav>")


def test_the_overview_is_unchanged(app):
    page = admin_client(app).get("/admin/").data.decode()
    for text in ("Students</div>", "Staff</div>", "Courses</div>", "Active enrollments</div>", "Revenue (ETB)</div>",
                 "Receipts to review</div>", "Unread messages</div>", "Certificates issued</div>", "New students", "Recent payments"):
        assert text in page, text
    assert "<h1>Admin</h1>" in page and "<a " not in part(page, 'class="stats"', "New students")      # tiles stay plain numbers
    teacher = teacher_client(app).get("/admin/").data.decode()
    assert "<h1>My courses</h1>" in teacher and "Open <a href=\"/admin/courses\">Manage courses</a>" in teacher


def test_the_new_styles_use_only_existing_colour_variables():
    css = open("academy/static/style.css").read()
    block = css.split("/* Admin navigation: the tab strip")[1]
    assert "var(--" in block and not re.search(r"#[0-9a-fA-F]{3,6}\b", block)
    assert re.search(r"@media \(max-width: 899px\) \{ \.admin-tabs \{ display: none; \}", block)
    assert re.search(r"@media \(min-width: 900px\) \{ \.phone-only \{ display: none !important; \}", block)
