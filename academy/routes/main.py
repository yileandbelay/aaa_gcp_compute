"""Public pages, course catalog and student dashboard."""
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..enrollment import EMAIL_RE
from ..mail import send_email
from ..models import Certificate, ContactMessage, Course, Enrollment, Payment, Review, db, utcnow
from ..reviews import can_review, distribution, popularity, rating_summary
from ..utils import course_progress

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    courses = Course.query.filter_by(is_published=True).order_by(Course.created_at.desc()).limit(6).all()
    return render_template("index.html", courses=courses, ratings=rating_summary([c.id for c in courses]))


LENGTHS = (("short", "Short (under 2 hours)"), ("medium", "Medium (2 to 6 hours)"), ("long", "Long (over 6 hours)"))
SORTS = (("newest", "Newest"), ("popular", "Most popular"), ("rating", "Highest rated"),
         ("shortest", "Shortest first"), ("title", "Title A-Z"))


@bp.route("/courses")
def catalog():
    published = Course.query.filter_by(is_published=True).all()
    categories = sorted({c.category for c in published if c.category})
    levels = sorted({c.level for c in published if c.level})
    category = request.args.get("category", "")
    level = request.args.get("level", "")
    length = request.args.get("length", "")
    price = request.args.get("price", "")
    sort = request.args.get("sort", "newest")
    q = request.args.get("q", "").strip()

    courses = published
    if category:
        courses = [c for c in courses if c.category == category]
    if level:
        courses = [c for c in courses if c.level == level]
    if length in dict(LENGTHS):
        courses = [c for c in courses if c.length_band == length]
    if price == "free":
        courses = [c for c in courses if c.is_free]
    elif price == "paid":
        courses = [c for c in courses if not c.is_free]
    if q:
        needle = q.lower()
        courses = [c for c in courses if needle in " ".join(
            filter(None, [c.title, c.summary, c.category, c.description])).lower()]

    ratings = rating_summary([c.id for c in courses])
    if sort == "popular":
        students = popularity([c.id for c in courses])
        courses.sort(key=lambda c: (-students.get(c.id, 0), c.title.lower()))
    elif sort == "rating":
        courses.sort(key=lambda c: (-ratings.get(c.id, (0, 0))[0], -ratings.get(c.id, (0, 0))[1], c.title.lower()))
    elif sort == "shortest":
        courses.sort(key=lambda c: (c.length_hours is None, c.length_hours or 0, c.title.lower()))
    elif sort == "title":
        courses.sort(key=lambda c: c.title.lower())
    else:
        sort = "newest"
        courses.sort(key=lambda c: c.created_at, reverse=True)
    filtered = bool(category or level or length or price or q)
    return render_template(
        "courses/catalog.html", courses=courses, ratings=ratings, categories=categories, levels=levels,
        lengths=LENGTHS, sorts=SORTS, category=category, level=level, length=length, price=price, sort=sort,
        q=q, filtered=filtered)


@bp.route("/healthz")
def healthz():
    """Uptime check for the hosting provider / monitoring."""
    db.session.execute(db.text("SELECT 1"))
    return {"status": "ok"}


@bp.route("/courses/<slug>")
def course_detail(slug):
    course = Course.query.filter_by(slug=slug).first_or_404()
    if not course.is_published and not (current_user.is_authenticated and current_user.is_staff):
        abort(404)
    enrollment = progress = pending_payment = None
    if current_user.is_authenticated:
        enrollment = current_user.enrollment_for(course)
        if current_user.has_access(course):
            progress = course_progress(current_user, course)
        pending_payment = Payment.query.filter_by(
            user_id=current_user.id, course_id=course.id, status="submitted"
        ).first()
    ctx = {}
    if current_app.config["FEATURE_REVIEWS"]:
        visible = [r for r in course.reviews if not r.is_hidden]
        summary = rating_summary([course.id]).get(course.id, (0.0, 0))
        mine = None
        if current_user.is_authenticated:
            mine = Review.query.filter_by(user_id=current_user.id, course_id=course.id).first()
        ctx = dict(reviews=[r for r in visible if r.comment][:20], rating=summary, dist=distribution(course),
                   can_review=can_review(current_user, course), my_review=mine)
    return render_template(
        "courses/detail.html", course=course, enrollment=enrollment,
        progress=progress, pending_payment=pending_payment, **ctx,
    )


@bp.route("/courses/<slug>/review", methods=["POST"])
@login_required
def review_save(slug):
    course = Course.query.filter_by(slug=slug, is_published=True).first_or_404()
    if not current_app.config["FEATURE_REVIEWS"]:
        abort(404)
    if not can_review(current_user, course):
        flash("You can rate a course once you are enrolled and have completed a lesson.", "error")
        return redirect(url_for("main.course_detail", slug=slug) + "#reviews")
    try:
        rating = int(request.form.get("rating", ""))
    except ValueError:
        rating = 0
    if not 1 <= rating <= 5:
        flash("Please choose a rating from 1 to 5 stars.", "error")
        return redirect(url_for("main.course_detail", slug=slug) + "#reviews")
    review = Review.query.filter_by(user_id=current_user.id, course_id=course.id).first()
    if not review:
        review = Review(user_id=current_user.id, course_id=course.id)
        db.session.add(review)
    review.rating = rating
    review.comment = request.form.get("comment", "").strip()[:2000] or None
    db.session.commit()
    flash("Thank you for your feedback!", "success")
    return redirect(url_for("main.course_detail", slug=slug) + "#reviews")


@bp.route("/courses/<slug>/review/delete", methods=["POST"])
@login_required
def review_delete(slug):
    course = Course.query.filter_by(slug=slug).first_or_404()
    review = Review.query.filter_by(user_id=current_user.id, course_id=course.id).first()
    if review:
        db.session.delete(review)
        db.session.commit()
        flash("Your review was removed.", "info")
    return redirect(url_for("main.course_detail", slug=slug) + "#reviews")


@bp.route("/dashboard")
@login_required
def dashboard():
    enrollments = (
        Enrollment.query.filter_by(user_id=current_user.id, status="active")
        .order_by(Enrollment.activated_at.desc()).all()
    )
    rows = [(e.course, course_progress(current_user, e.course)) for e in enrollments]
    certificates = Certificate.query.filter_by(user_id=current_user.id).all()
    payments = Payment.query.filter_by(user_id=current_user.id).order_by(Payment.created_at.desc()).all()
    return render_template("dashboard.html", rows=rows, certificates=certificates, payments=payments)


@bp.route("/certificates/<code>")
def certificate(code):
    """Public, printable certificate; also serves as a verification page."""
    if not current_app.config["FEATURE_CERTIFICATES"]:
        abort(404)
    cert = Certificate.query.filter_by(code=code.upper()).first_or_404()
    return render_template("certificate.html", cert=cert)


POLICY_PAGES = {"terms": "Terms of use", "privacy": "Privacy policy", "refunds": "Refund policy"}


@bp.route("/policies/<page>")
def policy(page):
    if page not in POLICY_PAGES:
        abort(404)
    return render_template(f"policies/{page}.html", title=POLICY_PAGES[page])


# --- Contact -------------------------------------------------------------------

PLACEHOLDER_EMAILS = {"", "info@example.com"}


def _client_ip():
    return (request.remote_addr or "unknown")[:64]


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    cfg = current_app.config
    form = request.form
    if request.method == "POST" and cfg["CONTACT_FORM"]:
        # Hidden field that real visitors leave empty; bots fill it in. Pretend it worked.
        if form.get("website"):
            flash("Thank you - your message has been sent.", "success")
            return redirect(url_for("main.contact"))
        name = form.get("name", "").strip()[:120]
        email = form.get("email", "").strip()[:255]
        subject = form.get("subject", "").strip()[:200]
        message = form.get("message", "").strip()[:4000]
        errors = []
        if len(name) < 2:
            errors.append("Please tell us your name.")
        if not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address so we can reply.")
        if len(message) < 10:
            errors.append("Please write a message of at least a few words.")
        recent = ContactMessage.query.filter(
            ContactMessage.ip == _client_ip(),
            ContactMessage.created_at >= utcnow() - timedelta(hours=1)).count()
        if recent >= cfg["CONTACT_MAX_PER_HOUR"]:
            errors.append("You have sent several messages already. Please try again later.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("contact.html", form=form), 400
        db.session.add(ContactMessage(name=name, email=email, subject=subject or None, message=message,
                                      ip=_client_ip()))
        db.session.commit()
        inbox = cfg["CONTACT_EMAIL"]
        if inbox not in PLACEHOLDER_EMAILS:
            send_email(inbox, f"[{cfg['SITE_NAME']}] {subject or 'New message'} - from {name}",
                       f"From: {name} <{email}>\n\n{message}\n", reply_to=email)
        flash("Thank you - your message has been sent. We will reply by email.", "success")
        return redirect(url_for("main.contact"))
    return render_template("contact.html", form=form)
