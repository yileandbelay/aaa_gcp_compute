"""Course ratings: summaries for course cards and pages, and who may review."""
from sqlalchemy import func

from .models import Enrollment, LessonProgress, Review, db


def rating_summary(course_ids):
    """{course_id: (average, count)} over visible reviews, for the given courses."""
    if not course_ids:
        return {}
    rows = (db.session.query(Review.course_id, func.avg(Review.rating), func.count(Review.id))
            .filter(Review.course_id.in_(course_ids), Review.is_hidden.is_(False))
            .group_by(Review.course_id).all())
    return {cid: (float(avg), int(n)) for cid, avg, n in rows}


def distribution(course):
    """{5: n, 4: n, ... 1: n} of visible reviews."""
    counts = dict(db.session.query(Review.rating, func.count(Review.id))
                  .filter(Review.course_id == course.id, Review.is_hidden.is_(False))
                  .group_by(Review.rating).all())
    return {star: int(counts.get(star, 0)) for star in (5, 4, 3, 2, 1)}


def popularity(course_ids):
    """{course_id: number of active students}."""
    if not course_ids:
        return {}
    rows = (db.session.query(Enrollment.course_id, func.count(Enrollment.id))
            .filter(Enrollment.course_id.in_(course_ids), Enrollment.status == "active")
            .group_by(Enrollment.course_id).all())
    return {cid: int(n) for cid, n in rows}


def can_review(user, course):
    """Enrolled students who have completed at least one lesson (so ratings come from people
    who actually tried the course). Staff do not rate courses."""
    if not user.is_authenticated or user.is_staff:
        return False
    enr = user.enrollment_for(course)
    if not enr or enr.status != "active":
        return False
    lesson_ids = [l.id for l in course.lessons]
    return bool(lesson_ids) and LessonProgress.query.filter(
        LessonProgress.user_id == user.id, LessonProgress.lesson_id.in_(lesson_ids)).first() is not None
