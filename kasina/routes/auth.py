"""Student registration, login and profile."""
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..models import User, db

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
            errors.append("Please enter your full name.")
        if not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address.")
        elif User.query.filter_by(email=email).first():
            errors.append("An account with that email already exists.")
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters.")
        if pw != pw2:
            errors.append("Passwords do not match.")
        if not form.get("agree"):
            errors.append("You must accept the terms to register.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html", form=form), 400
        user = User(full_name=name, email=email, phone=phone, city=city, role="student")
        user.set_password(pw)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(f"Welcome to Kasina Cakery Academy, {name}!", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("main.catalog"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form.get("password", "")):
            if not user.is_active_account:
                flash("This account has been suspended. Please contact us.", "error")
            else:
                login_user(user, remember=bool(request.form.get("remember")))
                return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
        else:
            flash("Incorrect email or password.", "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.full_name = request.form.get("full_name", current_user.full_name).strip()
        current_user.phone = request.form.get("phone", "").strip()
        current_user.city = request.form.get("city", "").strip()
        new_pw = request.form.get("new_password", "")
        if new_pw:
            if not current_user.check_password(request.form.get("current_password", "")):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("auth.profile"))
            if len(new_pw) < 8:
                flash("New password must be at least 8 characters.", "error")
                return redirect(url_for("auth.profile"))
            current_user.set_password(new_pw)
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")
