"""Keep lesson order and section order consistent.

A course shows its lessons grouped by section, lessons without a section first. The stored
``Lesson.position`` is always renumbered to match that order, so everything that walks
``course.lessons`` (progress, next lesson, certificates) sees the same order the student does.
A course without sections is never reordered by this.
"""
from .models import Lesson, Section, db


def normalize(course_id):
    """Renumber sections 1..n and lessons 1..m in displayed order. The caller commits."""
    db.session.flush()
    sections = Section.query.filter_by(course_id=course_id).order_by(Section.position, Section.id).all()
    for number, section in enumerate(sections, start=1):
        section.position = number
    rank = {section.id: section.position for section in sections}
    lessons = Lesson.query.filter_by(course_id=course_id).all()
    lessons.sort(key=lambda l: (rank.get(l.section_id, 0), l.position or 0, l.id))
    for number, lesson in enumerate(lessons, start=1):
        lesson.position = number
    db.session.flush()
    db.session.expire_all()  # relationships (course.lessons, course.sections) must reload in the new order
