"""Admin report: confirmed money and new enrolments per course per month."""
import calendar
from collections import defaultdict
from decimal import Decimal

from .models import Course, Enrollment, Payment, db, utcnow


def report_years():
    """Years that have any confirmed payment or enrolment, newest first (always includes this year)."""
    years = {utcnow().year}
    for (when,) in db.session.query(Payment.paid_at).filter(Payment.status == "paid", Payment.paid_at.isnot(None)):
        years.add(when.year)
    for (when,) in db.session.query(Enrollment.activated_at).filter(Enrollment.activated_at.isnot(None)):
        years.add(when.year)
    return sorted(years, reverse=True)


def monthly_report(year):
    """Rows ``{month, month_name, course, payments, revenue, enrolments}`` for one year, plus totals.

    Revenue counts only confirmed ("paid") payments, in the month they were received. New
    enrolments are the enrolments that became active in that month.
    """
    start, end = f"{year}-01-01", f"{year + 1}-01-01"
    money = defaultdict(lambda: [0, Decimal("0")])       # (month, course_id) -> [count, amount]
    for p in (Payment.query.filter(Payment.status == "paid", Payment.paid_at >= start, Payment.paid_at < end)):
        cell = money[(p.paid_at.month, p.course_id)]
        cell[0] += 1
        cell[1] += Decimal(str(p.amount))
    joined = defaultdict(int)
    for e in Enrollment.query.filter(Enrollment.activated_at >= start, Enrollment.activated_at < end):
        joined[(e.activated_at.month, e.course_id)] += 1
    titles = {c.id: c.title for c in Course.query.all()}
    rows = []
    for key in set(money) | set(joined):
        month, course_id = key
        count, amount = money.get(key, [0, Decimal("0")])
        rows.append({"month": month, "month_name": calendar.month_name[month], "course": titles.get(course_id, "(deleted course)"),
                     "course_id": course_id, "payments": count, "revenue": amount, "enrolments": joined.get(key, 0)})
    rows.sort(key=lambda r: (-r["month"], r["course"].lower()))
    totals = {"payments": sum(r["payments"] for r in rows), "revenue": sum((r["revenue"] for r in rows), Decimal("0")),
              "enrolments": sum(r["enrolments"] for r in rows)}
    return rows, totals
