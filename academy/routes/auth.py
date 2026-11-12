"""Student registration, login and profile."""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..enrollment import apply_invitations
from ..mail import send_email
from ..i18n import gettext as _
from ..models import LoginAttempt, User, db, utcnow

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = request.form
    if request.method == "POST":
        name = form.get("full_name", "").strip()
        email = form.get("email", "").strip().lower()
        phone = form.get("phone", "").strip()
        city = form.get("city", "").strip()
        pw, pw2 = form.get("password", ""), form.get("password2", "")
        errors = []
        if len(name) < 2:
            errors.append(_("Please enter your full name."))
        if not EMAIL_RE.match(email):
            errors.append(_("Please enter a valid email address."))
        elif User.query.filter_by(email=email).first():
            errors.append(_("An account with that email already exists."))
        if len(pw) < 8:
            errors.append(_("Password must be at least 8 characters."))
        if pw != pw2:
            errors.append(_("Passwords do not match."))
        if not form.get("agree"):
            errors.append(_("You must accept the terms to register."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html", form=form), 400
        user = User(full_name=name, email=email, phone=phone, city=city, role="student")
        user.set_password(pw)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(_("Welcome to %(site)s, %(name)s!", site=current_app.config["SITE_NAME"], name=name), "success")
        for course in apply_invitations(user):
            flash(_("You have been enrolled in %(course)s.", course=course.title), "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("main.catalog"))
    return render_template("auth/register.html", form=form)


def _client_ip():
    return (request.remote_addr or "unknown")[:64]


def _too_many_failures(email):
    cfg = current_app.config
    since = utcnow() - timedelta(minutes=cfg["LOGIN_WINDOW_MIN"])
    recent = LoginAttempt.query.filter(LoginAttempt.created_at >= since)
    by_email = recent.filter(LoginAttempt.email == email).count()
    by_ip = recent.filter(LoginAttempt.ip == _client_ip()).count()
    # Per-IP limit is looser so several students behind one router are not blocked.
    return by_email >= cfg["LOGIN_MAX_FAILURES"] or by_ip >= cfg["LOGIN_MAX_FAILURES"] * 4


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if _too_many_failures(email):
            flash(_("Too many failed attempts. Wait %(n)s minutes or reset your password.",
                    n=current_app.config["LOGIN_WINDOW_MIN"]), "error")
            return render_template("auth/login.html"), 429
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form.get("password", "")):
            if not user.is_active_account:
                flash(_("This account has been suspended. Please contact us."), "error")
            else:
                LoginAttempt.query.filter_by(email=email).delete()
                db.session.commit()
                login_user(user, remember=bool(request.form.get("remember")))
                return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
        else:
            db.session.add(LoginAttempt(email=email[:255], ip=_client_ip()))
            # Keep the table small: drop entries older than a day.
            LoginAttempt.query.filter(LoginAttempt.created_at < utcnow() - timedelta(days=1)).delete()
            db.session.commit()
            flash(_("Incorrect email or password."), "error")
    return render_template("auth/login.html")


# --- Password reset -------------------------------------------------------------

def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def make_reset_token(user, invite=False):
    # Including part of the current hash makes the link single-use: once the
    # password changes, the old link stops working. Invitation links (new staff
    # accounts) stay valid for longer.
    data = {"u": user.id, "h": user.password_hash[-16:]}
    if invite:
        data["i"] = 1
    return _reset_serializer().dumps(data)


def user_from_reset_token(token):
    cfg = current_app.config
    try:
        data, issued = _reset_serializer().loads(token, max_age=cfg["INVITE_TTL"], return_timestamp=True)
    except (BadSignature, SignatureExpired):
        return None
    ttl = cfg["INVITE_TTL"] if data.get("i") else cfg["PASSWORD_RESET_TTL"]
    if (datetime.now(timezone.utc) - issued).total_seconds() > ttl:
        return None
    user = db.session.get(User, data.get("u"))
    if not user or user.password_hash[-16:] != data.get("h"):
        return None
    return user


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.is_active_account:
            link = url_for("auth.reset_password", token=make_reset_token(user), _external=True)
            minutes = current_app.config["PASSWORD_RESET_TTL"] // 60
            send_email(
                user.email, f"Reset your {current_app.config['SITE_NAME']} password",
                f"Hello {user.full_name},\n\nTo choose a new password, open this link within {minutes} minutes:\n"
                f"{link}\n\nIf you did not ask for this, you can ignore this email.\n",
            )
        # Same message either way so the form cannot be used to discover accounts.
        flash(_("If an account exists for that email, we have sent a reset link."), "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = user_from_reset_token(token)
    if not user:
        flash(_("This reset link is invalid or has expired. Please request a new one."), "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < 8 or pw != pw2:
            flash(_("Passwords must match and be at least 8 characters."), "error")
            return render_template("auth/reset.html"), 400
        user.set_password(pw)
        LoginAttempt.query.filter_by(email=user.email).delete()
        db.session.commit()
        flash(_("Your password has been changed. You can log in now."), "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash(_("You have been logged out."), "info")
    return redirect(url_for("main.index"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.full_name = request.form.get("full_name", current_user.full_name).strip()
        current_user.phone = request.form.get("phone", "").strip()
        current_user.city = request.form.get("city", "").strip()
        if current_user.is_staff:
            current_user.headline = request.form.get("headline", "").strip()[:160] or None
            current_user.bio = request.form.get("bio", "").strip() or None
        new_pw = request.form.get("new_password", "")
        if new_pw:
            if not current_user.check_password(request.form.get("current_password", "")):
                flash(_("Current password is incorrect."), "error")
                return redirect(url_for("auth.profile"))
            if len(new_pw) < 8:
                flash(_("New password must be at least 8 characters."), "error")
                return redirect(url_for("auth.profile"))
            current_user.set_password(new_pw)
        db.session.commit()
        flash(_("Profile updated."), "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")
