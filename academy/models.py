"""Database models."""
import json
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def _tx_ref():
    from flask import current_app

    return f"{current_app.config.get('TX_PREFIX', 'MEK')}-{secrets.token_hex(8)}"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _default_is_student(context):
    """Accounts are students unless they were created as staff (teacher or admin) without the student role."""
    return context.get_current_parameters().get("role") in (None, "student")


class User(UserMixin, db.Model):
    # `role` is the staff level: "student" (none), "instructor" (shown as Teacher) or "admin".
    # `is_student` is a separate switch, so one email can be a teacher and a student at once.
    ROLES = ("student", "instructor", "admin")
    ROLE_LABELS = {"student": "Student", "instructor": "Teacher", "admin": "Admin"}

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(32))
    city = db.Column(db.String(80))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")
    is_student = db.Column(db.Boolean, nullable=False, default=_default_is_student, server_default=db.true())
    # Shown on course pages for instructors.
    headline = db.Column(db.String(160))
    bio = db.Column(db.Text)
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    enrollments = db.relationship("Enrollment", foreign_keys="Enrollment.user_id", back_populates="user",
                                  cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):  # used by Flask-Login
        return self.is_active_account

    @property
    def is_staff(self):
        return self.role in ("instructor", "admin")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_study(self):
        """May take courses (has the student role). Everyone without a staff level is a student."""
        return bool(self.is_student or self.role == "student")

    @property
    def role_names(self):
        """Roles for display, e.g. ["Teacher", "Student"]."""
        names = [self.ROLE_LABELS[self.role]] if self.role != "student" else []
        if self.can_study:
            names.append("Student")
        return names

    def enrollment_for(self, course):
        return Enrollment.query.filter_by(user_id=self.id, course_id=course.id).first()

    def has_access(self, course):
        if self.is_staff:
            return True
        e = self.enrollment_for(course)
        return bool(e and e.status == "active")


class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.String(300))
    description = db.Column(db.Text)
    level = db.Column(db.String(40), default="Beginner")
    category = db.Column(db.String(60), index=True)
    hours = db.Column(db.Numeric(5, 1))  # training hours, printed on certificates
    price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    # Shown on the course page. One item per line (learn_points), or free text (the others).
    learn_points = db.Column(db.Text)  # "What you'll learn"
    audience = db.Column(db.Text)  # "Who this course is for"
    prerequisites = db.Column(db.Text)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    instructor = db.relationship("User")
    lessons = db.relationship(
        "Lesson", back_populates="course", order_by="Lesson.position", cascade="all, delete-orphan"
    )
    assessments = db.relationship("Assessment", back_populates="course", cascade="all, delete-orphan")
    enrollments = db.relationship("Enrollment", back_populates="course", cascade="all, delete-orphan")
    reviews = db.relationship("Review", back_populates="course", order_by="Review.created_at.desc()",
                              cascade="all, delete-orphan")
    announcements = db.relationship(
        "Announcement", back_populates="course", order_by="Announcement.created_at.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def is_free(self):
        return float(self.price or 0) == 0

    @property
    def learn_list(self):
        """The "what you'll learn" items, without list bullets."""
        items = [line.strip().lstrip("-*\u2022 ").strip() for line in (self.learn_points or "").splitlines()]
        return [i for i in items if i]

    @property
    def total_minutes(self):
        return sum(l.duration_min or 0 for l in self.lessons)

    @property
    def length_hours(self):
        """Stated training hours, or the sum of lesson durations if none were stated."""
        if self.hours:
            return float(self.hours)
        return self.total_minutes / 60 if self.total_minutes else None

    @property
    def length_band(self):
        """short (under 2 h), medium (2-6 h), long (over 6 h), or None if unknown."""
        h = self.length_hours
        if h is None:
            return None
        return "short" if h < 2 else ("medium" if h <= 6 else "long")

    @property
    def file_count(self):
        return sum(1 for l in self.lessons for a in l.attachments if not a.is_link)


class Lesson(db.Model):
    TYPES = ("text", "video", "link")  # link = an external resource (another site, a free course...)

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default="text")
    body = db.Column(db.Text)  # Markdown notes; shown for both text and video lessons
    video_file = db.Column(db.String(255))  # stored filename inside UPLOAD_DIR/videos
    # "local" = file in UPLOAD_DIR streamed by us; otherwise the key of a hosted provider
    # (see videohosts/), with video_ref holding that provider's video ID.
    video_provider = db.Column(db.String(20), nullable=False, default="local", server_default="local")
    video_ref = db.Column(db.String(64))
    duration_min = db.Column(db.Integer)
    position = db.Column(db.Integer, default=0)
    is_preview = db.Column(db.Boolean, default=False)  # free sample visible to anyone
    created_at = db.Column(db.DateTime, default=utcnow)

    course = db.relationship("Course", back_populates="lessons")

    @property
    def has_video(self):
        if self.video_provider == "local":
            return bool(self.video_file)
        return bool(self.video_ref)

    comments = db.relationship(
        "Comment", back_populates="lesson", order_by="Comment.created_at", cascade="all, delete-orphan"
    )
    attachments = db.relationship(
        "Attachment", back_populates="lesson", order_by="Attachment.id", cascade="all, delete-orphan"
    )


class Enrollment(db.Model):
    """status: pending (awaiting payment) | active | revoked"""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    # How the student got access: purchase (paid), assigned (added by staff), free (self-enrolled)
    source = db.Column(db.String(20), nullable=False, default="purchase", server_default="purchase")
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow)
    activated_at = db.Column(db.DateTime)

    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])
    user = db.relationship("User", foreign_keys=[user_id], back_populates="enrollments")
    course = db.relationship("Course", back_populates="enrollments")
    __table_args__ = (db.UniqueConstraint("user_id", "course_id"),)

    SOURCE_LABELS = {"purchase": "Paid", "assigned": "Assigned", "free": "Free enrollment"}

    @property
    def source_label(self):
        return self.SOURCE_LABELS.get(self.source, self.source)

    def activate(self, source=None, by=None):
        self.status = "active"
        self.activated_at = utcnow()
        if source:
            self.source = source
        if by is not None:
            self.assigned_by_id = by.id


class Payment(db.Model):
    """status: initiated | submitted (receipt awaiting review) | paid | failed | rejected

    method: chapa | manual | demo. A "manual" payment is money received outside the website; an
    admin may enter it directly (see ledger.record_payment), in which case ``channel`` says how it
    arrived and ``recorded_by`` who entered it."""

    CHANNELS = (("cash", "Cash"), ("telebirr", "Telebirr"), ("bank", "Bank transfer"), ("other", "Other"))

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(8), nullable=False)
    method = db.Column(db.String(20), nullable=False)  # chapa | manual | demo
    status = db.Column(db.String(20), nullable=False, default="initiated")
    tx_ref = db.Column(db.String(64), unique=True, nullable=False, default=lambda: _tx_ref())
    provider_ref = db.Column(db.String(128))
    receipt_file = db.Column(db.String(255))
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow)
    paid_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    channel = db.Column(db.String(20))  # cash | telebirr | bank | other (payments entered by an admin)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    user = db.relationship("User", foreign_keys=[user_id])
    course = db.relationship("Course")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])

    @property
    def channel_label(self):
        return dict(self.CHANNELS).get(self.channel)

    def mark_paid(self, provider_ref=None):
        """Mark paid and activate the matching enrollment. Idempotent."""
        if self.status == "paid":
            return
        self.status = "paid"
        self.paid_at = utcnow()
        if provider_ref:
            self.provider_ref = provider_ref
        enr = Enrollment.query.filter_by(user_id=self.user_id, course_id=self.course_id).first()
        if not enr:
            enr = Enrollment(user_id=self.user_id, course_id=self.course_id)
            db.session.add(enr)
        enr.activate(source="purchase")


class LessonProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False)
    completed_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "lesson_id"),)


class Assessment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    instructions = db.Column(db.Text)
    pass_mark = db.Column(db.Integer, default=70)  # percent
    max_attempts = db.Column(db.Integer, default=3)  # 0 = unlimited
    time_limit_min = db.Column(db.Integer, default=0)  # 0 = no limit
    is_final = db.Column(db.Boolean, default=False)

    course = db.relationship("Course", back_populates="assessments")
    questions = db.relationship(
        "Question", back_populates="assessment", order_by="Question.position", cascade="all, delete-orphan"
    )
    attempts = db.relationship("Attempt", back_populates="assessment", cascade="all, delete-orphan")


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessment.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.Text, nullable=False)  # JSON list of strings
    correct_index = db.Column(db.Integer, nullable=False)
    explanation = db.Column(db.Text)
    position = db.Column(db.Integer, default=0)

    assessment = db.relationship("Assessment", back_populates="questions")

    @property
    def options(self):
        return json.loads(self.options_json)

    @options.setter
    def options(self, value):
        self.options_json = json.dumps(list(value))


class Attempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessment.id"), nullable=False)
    started_at = db.Column(db.DateTime, default=utcnow)
    submitted_at = db.Column(db.DateTime)
    score = db.Column(db.Integer)  # percent
    passed = db.Column(db.Boolean)
    answers_json = db.Column(db.Text)  # {question_id: chosen_index}

    user = db.relationship("User")
    assessment = db.relationship("Assessment", back_populates="attempts")

    @property
    def answers(self):
        return {int(k): v for k, v in json.loads(self.answers_json or "{}").items()}


class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    code = db.Column(db.String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(6).upper())
    issued_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User")
    course = db.relationship("Course")
    __table_args__ = (db.UniqueConstraint("user_id", "course_id"),)


class Comment(db.Model):
    """Per-lesson Q&A between students and instructors."""

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    lesson = db.relationship("Lesson", back_populates="comments")
    user = db.relationship("User")


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    course = db.relationship("Course", back_populates="announcements")


class LoginAttempt(db.Model):
    """Failed logins, used to throttle password guessing (shared across workers)."""

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), index=True)
    ip = db.Column(db.String(64), index=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)


class Attachment(db.Model):
    """Downloadable course material (handouts, slides, templates) attached to a lesson."""

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False)
    # A resource is either an uploaded file (stored_name) or a link to another site (url).
    stored_name = db.Column(db.String(255))  # random name inside UPLOAD_DIR/materials
    url = db.Column(db.String(500))
    original_name = db.Column(db.String(255), nullable=False)  # file name, or the link's title
    size_bytes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    lesson = db.relationship("Lesson", back_populates="attachments")

    @property
    def is_link(self):
        return bool(self.url)

    @property
    def domain(self):
        from urllib.parse import urlparse

        return (urlparse(self.url or "").hostname or "").removeprefix("www.")

    @property
    def size_label(self):
        if self.is_link:
            return self.domain
        size = self.size_bytes or 0
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024


class Invitation(db.Model):
    """A course assigned to an email address that has no account yet. It is applied
    automatically when someone registers with that address."""

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    invited_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    course = db.relationship("Course")
    invited_by = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("email", "course_id"),)


class ContactMessage(db.Model):
    """A message sent through the public contact form."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(200))
    message = db.Column(db.Text, nullable=False)
    ip = db.Column(db.String(64), index=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)


class Review(db.Model):
    """A student's rating (1-5 stars) and optional feedback for a course they take."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    is_hidden = db.Column(db.Boolean, default=False, nullable=False)  # hidden by a course admin
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    user = db.relationship("User")
    course = db.relationship("Course", back_populates="reviews")
    __table_args__ = (db.UniqueConstraint("user_id", "course_id"),)

    @property
    def author(self):
        """First name and last initial, e.g. "Hana T." - full names are not shown publicly."""
        parts = (self.user.full_name or "Student").split()
        return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."
