"""Assigning courses to students: enroll, invite by email, apply invitations on sign-up."""
import re

from flask import current_app, url_for

from .mail import send_email
from .models import Enrollment, Invitation, db

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def parse_emails(text):
    """Split pasted text (one per line, or separated by commas/semicolons/spaces) into a
    de-duplicated list of lowercase email addresses, plus the entries that were not emails."""
    seen, good, bad = set(), [], []
    for token in re.split(r"[\s,;]+", text or ""):
        token = token.strip().strip("<>").lower()
        if not token:
            continue
        if EMAIL_RE.match(token):
            if token not in seen:
                seen.add(token)
                good.append(token)
        else:
            bad.append(token)
    return good, bad


def enroll_student(user, course, by=None, source="assigned"):
    """Give ``user`` access to ``course``. Returns True if access is new (it wasn't active before)."""
    enr = user.enrollment_for(course)
    if not enr:
        enr = Enrollment(user_id=user.id, course_id=course.id)
        db.session.add(enr)
    if enr.status == "active":
        return False
    enr.activate(source=source, by=by)
    return True


def notify_enrolled(user, course):
    link = url_for("main.course_detail", slug=course.slug, _external=True)
    site = current_app.config["SITE_NAME"]
    send_email(user.email, f"You have been enrolled: {course.title}",
               f"Hello {user.full_name},\n\nYou now have access to {course.title} on {site}:\n{link}\n\n"
               f"Log in to start learning.\n{site}\n")


def create_invitation(email, course, by=None, notify=True):
    """Returns True if a new invitation was created."""
    if Invitation.query.filter_by(email=email, course_id=course.id).first():
        return False
    db.session.add(Invitation(email=email, course_id=course.id, invited_by_id=by.id if by else None))
    if notify:
        site = current_app.config["SITE_NAME"]
        link = url_for("auth.register", _external=True)
        send_email(email, f"You have been invited to {course.title}",
                   f"Hello,\n\nYou have been given access to {course.title} on {site}.\n\n"
                   f"Create your account with this email address ({email}) and the course will be "
                   f"waiting for you:\n{link}\n\n{site}\n")
    return True


def apply_invitations(user):
    """Enroll a newly registered student in every course they were invited to. Returns the courses."""
    invites = Invitation.query.filter_by(email=user.email.lower()).all()
    courses = []
    for inv in invites:
        if enroll_student(user, inv.course, by=inv.invited_by, source="assigned"):
            courses.append(inv.course)
        db.session.delete(inv)
    if invites:
        db.session.commit()
    return courses
