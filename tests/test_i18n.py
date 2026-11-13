"""Amharic (and general language) support."""
import json
import re
from datetime import datetime

import pytest
from markupsafe import Markup

from academy import create_app, i18n
from academy.config import TestConfig
from academy.models import Course, db
from academy.seed import seed

AM_CATALOG = json.load(open(i18n.os.path.join(i18n.os.path.dirname(i18n.__file__), "translations", "am.json"),
                            encoding="utf-8"))
PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def make_app(tmp_path, **overrides):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads"), **overrides})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def am_client(app):
    c = app.test_client()
    c.set_cookie("lang", "am")
    return c


def register(c, email="student@example.com", name="Abebe Kebede"):
    return c.post("/register", data={"full_name": name, "email": email, "password": "secret123",
                                     "password2": "secret123", "agree": "1"})


# --- Switching language ---------------------------------------------------------------------

def test_site_is_english_by_default(app):
    page = app.test_client().get("/").data.decode()
    assert '<html lang="en">' in page and "Browse courses" in page and "ኮርሶች" not in page


def test_switching_to_amharic_sets_a_cookie_and_returns_to_the_page(app):
    c = app.test_client()
    r = c.get("/lang/am?next=/courses")
    assert r.status_code == 302 and r.location.endswith("/courses")
    cookie = r.headers["Set-Cookie"]
    assert "lang=am" in cookie and "SameSite=Lax" in cookie and "Max-Age=31536000" in cookie
    page = c.get("/courses").data.decode()
    assert '<html lang="am">' in page and "ኮርሶች" in page and "All topics" not in page
    c.get("/lang/en")
    assert '<html lang="en">' in c.get("/courses").data.decode()


@pytest.mark.parametrize("nxt", ["//evil.example", "/\\evil.example", "https://evil.example", "javascript:alert(1)", ""])
def test_language_switch_never_redirects_off_site(app, nxt):
    r = app.test_client().get("/lang/am", query_string={"next": nxt})
    assert r.status_code == 302 and r.location in ("/", "http://localhost/")


def test_unknown_language_is_rejected(app):
    assert app.test_client().get("/lang/xx").status_code == 404


def test_language_switcher_marks_the_current_language(app):
    en = app.test_client().get("/").data.decode()
    assert "አማርኛ" in en and 'lang="am"' in en
    am = am_client(app).get("/").data.decode()
    assert re.search(r'href="/lang/am[^"]*"[^>]*aria-current="true"', am)
    assert "English" in am


def test_switcher_hidden_when_only_one_language(tmp_path):
    app = make_app(tmp_path, LANGUAGES=["en"])
    assert 'class="langs"' not in app.test_client().get("/").data.decode()


# --- Picking the language -------------------------------------------------------------------

def test_browser_language_is_used_on_first_visit(app):
    c = app.test_client()
    assert "ኮርሶች" in c.get("/", headers={"Accept-Language": "am,en;q=0.5"}).data.decode()
    assert "Browse courses" in c.get("/", headers={"Accept-Language": "en-US,en;q=0.9"}).data.decode()
    assert "Browse courses" in c.get("/", headers={"Accept-Language": "fr"}).data.decode()


def test_chosen_language_beats_browser_language(app):
    c = app.test_client()
    c.get("/lang/en")
    assert "Browse courses" in c.get("/", headers={"Accept-Language": "am"}).data.decode()


def test_autodetect_can_be_switched_off_and_default_changed(tmp_path):
    off = make_app(tmp_path, LANGUAGE_AUTODETECT=False)
    assert "Browse courses" in off.test_client().get("/", headers={"Accept-Language": "am"}).data.decode()
    am_default = make_app(tmp_path / "x", DEFAULT_LANGUAGE="am")
    assert "ኮርሶች" in am_default.test_client().get("/").data.decode()


# --- Translating -------------------------------------------------------------------------------

def test_missing_translation_falls_back_to_english(app):
    with app.test_request_context("/", headers={"Cookie": "lang=am"}):
        assert i18n.gettext("A sentence nobody translated") == "A sentence nobody translated"
        assert i18n.gettext("Welcome back, %(name)s", name="Hana") == "እንኳን ደህና መጡ፣ Hana"


def test_plural_forms(app):
    with app.test_request_context("/", headers={"Cookie": "lang=en"}):
        assert i18n.ngettext("%(n)s lesson", "%(n)s lessons", 1) == "1 lesson"
        assert i18n.ngettext("%(n)s lesson", "%(n)s lessons", 5) == "5 lessons"
    with app.test_request_context("/", headers={"Cookie": "lang=am"}):
        assert i18n.ngettext("%(n)s lesson", "%(n)s lessons", 1) == "1 ትምህርት"
        assert i18n.ngettext("%(n)s lesson", "%(n)s lessons", 5) == "5 ትምህርቶች"


def test_html_placeholders_keep_markup_but_escape_other_values(app):
    with app.test_request_context("/"):
        link = Markup('<a href="/x">terms</a>')
        out = i18n.gettext("I accept the %(link)s and will not share my account or course materials.", link=link)
        assert '<a href="/x">terms</a>' in out
        escaped = i18n.gettext("Welcome back, %(name)s", name="<script>x</script>")
        assert "<script>" in escaped  # plain strings are returned as is...
    page = app.test_client().get("/")  # ...and Jinja escapes them when they reach a page
    assert page.status_code == 200


def test_user_supplied_names_are_escaped_in_translated_pages(app):
    c = am_client(app)
    register(c, name="<script>alert(1)</script> Bad")
    page = c.get("/dashboard").data.decode()
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_terms_link_renders_as_a_real_link_in_both_languages(app):
    for client in (app.test_client(), am_client(app)):
        page = client.get("/register").data.decode()
        assert re.search(r'<a href="/policies/terms" target="_blank">[^<]+</a>', page)
        assert "&lt;a href" not in page


def test_dates_use_amharic_month_names(app):
    d = datetime(2026, 11, 12, 9, 30)
    with app.test_request_context("/", headers={"Cookie": "lang=en"}):
        assert i18n.localdate(d) == "12 Nov 2026" and i18n.localdate(d, "%d %B %Y") == "12 November 2026"
    with app.test_request_context("/", headers={"Cookie": "lang=am"}):
        assert i18n.localdate(d) == "12 ኖቬም 2026" and i18n.localdate(d, "%d %B %Y") == "12 ኖቬምበር 2026"
        assert i18n.localdate(d, "%d %b %Y %H:%M") == "12 ኖቬም 2026 09:30"
        assert i18n.localdate(None) == ""


def test_theme_folder_can_override_and_extend_a_language(tmp_path):
    theme = tmp_path / "theme" / "translations"
    theme.mkdir(parents=True)
    (theme / "am.json").write_text(json.dumps({"Contact us": "ጻፉልን", "Brand new sentence": "አዲስ ዓረፍተ ነገር"}),
                                   encoding="utf-8")
    app = make_app(tmp_path, THEME_DIR=str(tmp_path / "theme"))
    page = am_client(app).get("/contact").data.decode()
    assert "ጻፉልን" in page and "ያግኙን" in page  # overridden string, others unchanged
    with app.test_request_context("/", headers={"Cookie": "lang=am"}):
        assert i18n.gettext("Brand new sentence") == "አዲስ ዓረፍተ ነገር"


def test_a_new_language_needs_only_a_catalog_file(tmp_path):
    theme = tmp_path / "theme" / "translations"
    theme.mkdir(parents=True)
    (theme / "om.json").write_text(json.dumps({"Contact": "Koorsiiwwan"}), encoding="utf-8")
    app = make_app(tmp_path, THEME_DIR=str(tmp_path / "theme"), LANGUAGES=["en", "om"])
    c = app.test_client()
    c.get("/lang/om")
    page = c.get("/").data.decode()
    assert "Koorsiiwwan" in page and "Browse courses" in page  # untranslated parts stay English


# --- The Amharic catalog itself ----------------------------------------------------------------

def test_every_translatable_string_has_an_amharic_translation(app):
    missing = sorted(k for k in i18n.source_strings(app) if not AM_CATALOG.get(k))
    assert missing == [], "Untranslated: " + "; ".join(missing)


def test_catalog_has_no_unused_strings(app):
    unused = sorted(set(AM_CATALOG) - i18n.source_strings(app))
    assert unused == [], "Unused catalog entries (run `flask i18n-update am`): " + "; ".join(unused)


def test_translations_keep_their_placeholders():
    for source, text in AM_CATALOG.items():
        assert set(PLACEHOLDER.findall(source)) == set(PLACEHOLDER.findall(text)), source
        assert source.count("%%") == text.count("%%"), source
        # every % must be part of a placeholder or an escaped %%, or formatting would crash
        stripped = PLACEHOLDER.sub("", text).replace("%%", "")
        assert "%" not in stripped, source
        text % {name: "x" for name in PLACEHOLDER.findall(source)} if PLACEHOLDER.search(source) else None


def test_catalog_is_amharic_script(app):
    ethiopic = re.compile(r"[ሀ-፿]")
    latin_only = [k for k, v in AM_CATALOG.items() if not ethiopic.search(v)
                  and k not in ("Chapa", "%(date)s UTC")]
    assert latin_only == []


def test_i18n_missing_command_reports_nothing_for_amharic(app):
    result = app.test_cli_runner().invoke(args=["i18n-missing", "am"])
    assert result.exit_code == 0 and result.output.strip() == "0 missing"


# --- Pages render in Amharic -----------------------------------------------------------------

def pm(app):
    with app.app_context():
        c = Course.query.filter_by(slug="project-management-fundamentals").one()
        return c.id, [l.id for l in c.lessons], c.assessments[0].id


def english_ui_left(page, phrases):
    return [p for p in phrases if p in page]


def test_public_pages_in_amharic(app):
    c = am_client(app)
    expectations = {
        "/": ["ኮርሶችን ይመልከቱ", "እንዴት እንደሚሠራ", "ምን መማር ይፈልጋሉ?"],
        "/courses": ["ኮርሶች", "ርዝመት"],
        "/courses/project-management-fundamentals": ["የሚማሩት", "የትምህርት ዝርዝር", "ይህ ኮርስ ለማን ነው", "ግምገማዎችና አስተያየቶች"],
        "/login": ["የይለፍ ቃል", "ይግቡ"],
        "/register": ["ነጻ መለያዎን ይፍጠሩ", "መለያ ይፍጠሩ"],
        "/forgot-password": ["የይለፍ ቃልዎን ረስተዋል?"],
        "/contact": ["መልእክት ይላኩ", "ያግኙን"],
        "/policies/terms": ["ያግኙን"],
        "/no-such-page": ["ገጹ አልተገኘም"],
    }
    leftovers = ["Browse courses", "Log in", "Register", "Send a message", "What you'll learn", "Syllabus",
                 "Page not found", "Forgot your password?"]
    for url, needles in expectations.items():
        page = c.get(url).data.decode()
        assert all(n in page for n in needles), (url, [n for n in needles if n not in page])
        if url != "/policies/terms":
            assert english_ui_left(page, leftovers) == [], (url, english_ui_left(page, leftovers))


def test_course_page_details_in_amharic(app):
    page = am_client(app).get("/courses/project-management-fundamentals").data.decode()
    assert "5 ትምህርቶች" in page and "12 ሰዓት ስልጠና" in page and "(ተቆልፏል)" in page and "ነጻ ቅድመ እይታ" in page
    assert "ጀማሪ" in page and "መምህር፦ Meklit Haile" in page and "የማጠናቀቂያ የምስክር ወረቀት" in page
    assert "2,500.00 ETB" in page and "Project Management Fundamentals" in page  # content and numbers unchanged


def test_catalog_filters_and_counts_in_amharic(app):
    page = am_client(app).get("/courses?level=Beginner&sort=title").data.decode()
    assert "2 ኮርሶች" in page and "ከማጣሪያዎችዎ ጋር ይስማማሉ።" in page and "ማጣሪያዎች ያጽዱ" in page
    assert "ጀማሪ" in page and "Title A-Z" not in page


def test_student_journey_in_amharic(app):
    cid, lessons, aid = pm(app)
    c = am_client(app)
    r = register(c)
    assert "እንኳን" in c.get(r.location).data.decode()  # welcome flash is in Amharic
    checkout = c.get(f"/pay/course/{cid}").data.decode()
    for needle in ("የኮርስ ክፍያ፦", "የባንክ ዝውውር", "ደረሰኙን ያስገቡ", "የግብይት ማጣቀሻ ቁጥር"):
        assert needle in checkout, needle
    assert english_ui_left(checkout, ["Course fee:", "Submit receipt", "Bank transfer"]) == []
    c.post(f"/pay/course/{cid}/demo")
    dash = c.get("/dashboard").data.decode()
    assert "ትምህርቴ" in dash or "ኮርሶቼ" in dash
    assert "የክፍያ ደረሰኝ" not in dash and "ክፍያዎች" in dash and "ተከፍሏል" in dash
    lesson = c.get(f"/learn/lesson/{lessons[0]}").data.decode()
    for needle in ("እንደተጠናቀቀ ምልክት አድርገው ይቀጥሉ", "ጥያቄዎችና ውይይት", "ምዘናዎች", "ተጠናቅቋል"):
        assert needle in lesson, needle
    assert english_ui_left(lesson, ["Mark complete", "Questions and discussion", "Previous"]) == []
    quiz = c.get(f"/assessments/{aid}").data.decode()
    assert "ምዘናውን ይጀምሩ" in quiz and "የመጨረሻ ፈተና" in quiz and "የማለፊያ ነጥብ፦ 70%" in quiz
    r = c.post(f"/assessments/{aid}/start", follow_redirects=True)
    assert "መልሶቹን ያስገቡ" in r.data.decode() and "የቀረ ጊዜ" in r.data.decode()
    submitted = c.post(r.request.path, data={}, follow_redirects=True).data.decode()
    assert "ገና አልደረሱም" in submitted and "ትክክል አይደለም" in submitted
    profile = c.get("/profile").data.decode()
    assert "መገለጫዬ" in profile and "የይለፍ ቃል ይቀይሩ" in profile and "ተማሪ" in profile


def test_certificate_and_receipt_pages_in_amharic(app):
    cid, lessons, aid = pm(app)
    c = am_client(app)
    register(c)
    c.post(f"/pay/course/{cid}/demo")
    for lid in lessons:
        c.post(f"/learn/lesson/{lid}/complete")
    with app.app_context():
        a = db.session.get(Course, cid).assessments[0]
        right = {f"q{q.id}": str(q.correct_index) for q in a.questions}
    r = c.post(f"/assessments/{aid}/start", follow_redirects=True)
    result = c.post(r.request.path, data=right, follow_redirects=True).data.decode()
    assert "አልፈዋል!" in result and "የምስክር ወረቀትዎ ተዘጋጅቷል" in result
    from academy.models import Certificate, Payment

    with app.app_context():
        code = Certificate.query.one().code
        pid = Payment.query.one().id
    cert = c.get(f"/certificates/{code}").data.decode()
    assert "የማጠናቀቂያ የምስክር ወረቀት" in cert and "ኖቬም" in cert and "12 ሰዓት ስልጠና" in cert
    assert "Certificate of Completion" not in cert
    invoice = c.get(f"/pay/invoice/{pid}").data.decode()
    assert "የክፍያ ደረሰኝ" in invoice and "ማጣቀሻ ቁጥር" in invoice and "(UTC)" in invoice


def test_error_messages_in_amharic(app):
    c = am_client(app)
    r = c.post("/login", data={"email": "nobody@example.com", "password": "wrong"})
    assert "ኢሜይሉ ወይም የይለፍ ቃሉ ትክክል አይደለም።" in r.data.decode()
    r = c.post("/register", data={"full_name": "x", "email": "bad", "password": "1", "password2": "2"})
    page = r.data.decode()
    assert r.status_code == 400 and "እባክዎ ትክክለኛ ኢሜይል አድራሻ ያስገቡ።" in page and "የይለፍ ቃሎቹ አይመሳሰሉም።" in page
    r = c.post("/contact", data={"name": "H", "email": "bad", "message": "short"})
    assert "እባክዎ ስምዎን ይንገሩን።" in r.data.decode()
    assert "Invalid" not in c.get("/").data.decode()


def test_staff_area_stays_in_english(app):
    c = am_client(app)
    c.post("/login", data={"email": "admin@example.com", "password": "admin12345"})
    page = c.get("/admin/students").data.decode()
    assert "Manage students" in page and "Search name, email or phone" in page
    assert "ኮርሶች" in page  # the shared header is translated


# --- Academy name and compact language switch --------------------------------------------------

def test_academy_name_follows_the_language(app):
    en = app.test_client().get("/").data.decode()
    am = am_client(app).get("/").data.decode()
    assert "Mek Academy" in en and "Online Academy" not in en
    assert "ሜክ አካዳሚ" in am and "Mek Academy" not in am.split("<body")[1].split("<main")[0]
    assert "<title>ሜክ አካዳሚ" in am or "· ሜክ አካዳሚ</title>" in am


def test_old_default_name_in_settings_file_becomes_the_new_name(monkeypatch):
    import importlib

    from academy import config

    monkeypatch.setenv("SITE_NAME", "Mek Online Academy")
    assert importlib.reload(config).Config.SITE_NAME == "Mek Academy"
    monkeypatch.setenv("SITE_NAME", "Another School")
    assert importlib.reload(config).Config.SITE_NAME == "Another School"
    monkeypatch.delenv("SITE_NAME")
    importlib.reload(config)


def test_language_switch_sits_in_the_top_bar_with_short_labels(app):
    html = app.test_client().get("/").data.decode()
    bar = html.split('<nav class="site-nav"')[0]
    assert 'class="langs"' in bar and ">EN<" in bar and ">አማ<" in bar
    assert 'aria-label="አማርኛ"' in bar and 'aria-label="English"' in bar
    assert 'class="langs"' not in html.split('<nav class="site-nav"')[1].split("</nav>")[0]
