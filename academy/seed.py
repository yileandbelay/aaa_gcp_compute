"""Load sample courses from a JSON file (flask seed-demo [--file path.json]).

Each academy built on this code can keep its own JSON file of demo or starter
courses; see sample_data/demo_courses.json for the format.
"""
import json
import os
from decimal import Decimal

from .models import Assessment, Course, Lesson, Question, User, db

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), "sample_data", "demo_courses.json")
DEMO_ADMIN = ("admin@example.com", "admin12345")


def _user(email, full_name, password, role, **extra):
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, full_name=full_name, role=role, **extra)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
    return user


def seed(path=None):
    with open(path or DEFAULT_FILE, encoding="utf-8") as fh:
        data = json.load(fh)

    admin = _user(DEMO_ADMIN[0], "Academy Admin", DEMO_ADMIN[1], "admin")
    teacher = admin
    if data.get("instructor"):
        i = data["instructor"]
        teacher = _user(i["email"], i["full_name"], i["password"], "instructor",
                        headline=i.get("headline"), bio=i.get("bio"))

    for c in data.get("courses", []):
        if Course.query.filter_by(slug=c["slug"]).first():
            continue
        course = Course(
            slug=c["slug"], title=c["title"], summary=c.get("summary"), description=c.get("description"),
            category=c.get("category"), level=c.get("level", "Beginner"),
            hours=Decimal(str(c["hours"])) if c.get("hours") else None,
            price=Decimal(str(c.get("price", "0"))), is_published=c.get("published", True),
            instructor_id=teacher.id,
        )
        db.session.add(course)
        db.session.flush()
        for pos, l in enumerate(c.get("lessons", []), start=1):
            db.session.add(Lesson(course_id=course.id, position=pos, title=l["title"], kind=l.get("kind", "text"),
                                  body=l.get("body", ""), duration_min=l.get("minutes"),
                                  is_preview=l.get("preview", False)))
        for a in c.get("assessments", []):
            assessment = Assessment(course_id=course.id, title=a["title"], instructions=a.get("instructions"),
                                    pass_mark=a.get("pass_mark", 70), max_attempts=a.get("attempts", 3),
                                    time_limit_min=a.get("minutes", 0), is_final=a.get("final", False))
            db.session.add(assessment)
            db.session.flush()
            for pos, q in enumerate(a.get("questions", []), start=1):
                question = Question(assessment_id=assessment.id, position=pos, text=q["text"],
                                    correct_index=q["answer"] - 1, explanation=q.get("explanation"))
                question.options = q["options"]
                db.session.add(question)
    db.session.commit()
