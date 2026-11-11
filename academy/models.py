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


class User(UserMixin, db.Model):
    ROLES = ("student", "instructor", "admin")

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(32))
    city = db.Column(db.String(80))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")
    # Shown on course pages for instructors.
    headline = db.Column(db.String(160))
    bio = db.Column(db.Text)
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    enrollments = db.relationship("Enrollment", back_populates="user", cascade="all, delete-orphan")

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
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    instructor = db.relationship("User")
    lessons = db.relationship(
        "Lesson", back_populates="course", order_by="Lesson.position", cascade="all, delete-orphan"
    )
    assessments = db.relationship("Assessment", back_populates="course", cascade="all, delete-orphan")
    enrollments = db.relationship("Enrollment", back_populates="course", cascade="all, delete-orphan")
    announcements = db.relationship(
        "Announcement", back_populates="course", order_by="Announcement.created_at.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def is_free(self):
        return float(self.price or 0) == 0


class Lesson(db.Model):
    TYPES = ("text", "video")

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default="text")
    body = db.Column(db.Text)  # Markdown notes; shown for both text and video lessons
    video_file = db.Column(db.String(255))  # stored filename inside UPLOAD_DIR/videos
    # "local" = file in UPLOAD_DIR streamed by us; "vdocipher" = DRM-protected video hosted by VdoCipher
    video_provider = db.Column(db.String(20), nullable=False, default="local", server_default="local")
    vdocipher_id = db.Column(db.String(64))
    duration_min = db.Column(db.Integer)
    position = db.Column(db.Integer, default=0)
    is_preview = db.Column(db.Boolean, default=False)  # free sample visible to anyone
    created_at = db.Column(db.DateTime, default=utcnow)

    course = db.relationship("Course", back_populates="lessons")

    @property
    def has_video(self):
        if self.video_provider == "vdocipher":
            return bool(self.vdocipher_id)
        return bool(self.video_file)

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
    created_at = db.Column(db.DateTime, default=utcnow)
    activated_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="enrollments")
    course = db.relationship("Course", back_populates="enrollments")
    __table_args__ = (db.UniqueConstraint("user_id", "course_id"),)

    def activate(self):
        self.status = "active"
        self.activated_at = utcnow()


class Payment(db.Model):
    """status: initiated | submitted (receipt awaiting review) | paid | failed | rejected"""

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

    user = db.relationship("User", foreign_keys=[user_id])
    course = db.relationship("Course")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

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
        enr.activate()


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
    stored_name = db.Column(db.String(255), nullable=False)  # random name inside UPLOAD_DIR/materials
    original_name = db.Column(db.String(255), nullable=False)
    size_bytes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    lesson = db.relationship("Lesson", back_populates="attachments")

    @property
    def size_label(self):
        size = self.size_bytes or 0
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
