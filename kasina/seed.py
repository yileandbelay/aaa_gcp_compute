"""Sample data so the platform can be explored right away (flask seed-demo)."""
from decimal import Decimal

from .models import Assessment, Course, Lesson, Question, User, db


def seed():
    admin = User.query.filter_by(email="admin@kasina.local").first()
    if not admin:
        admin = User(full_name="Kasina Admin", email="admin@kasina.local", role="admin")
        admin.set_password("admin12345")
        db.session.add(admin)
        db.session.flush()

    if Course.query.filter_by(slug="cake-baking-fundamentals").first():
        db.session.commit()
        return

    c1 = Course(
        slug="cake-baking-fundamentals", title="Cake Baking Fundamentals", level="Beginner",
        summary="From measuring flour to a perfectly level sponge - the foundations every baker needs.",
        description=(
            "In this course you will learn:\n\n"
            "- Kitchen tools, ingredients and food safety\n- The creaming and whisking methods\n"
            "- Baking vanilla, chocolate and red velvet sponges\n- Troubleshooting common cake faults\n\n"
            "Includes a final exam and a certificate of completion."
        ),
        price=Decimal("1500.00"), is_published=True, instructor_id=admin.id,
    )
    c2 = Course(
        slug="intro-to-buttercream", title="Introduction to Buttercream (Free)", level="Beginner",
        summary="A short free course on making smooth, pipeable buttercream.",
        description="Learn American and Swiss meringue buttercream in under an hour.",
        price=Decimal("0"), is_published=True, instructor_id=admin.id,
    )
    db.session.add_all([c1, c2])
    db.session.flush()

    db.session.add_all([
        Lesson(course_id=c1.id, position=1, kind="text", is_preview=True, duration_min=10,
               title="Welcome & kitchen essentials",
               body="## Welcome!\n\nBefore we bake, let's set up our station.\n\n"
                    "### Essential tools\n- Digital scale (bake by weight, not cups)\n- Two 8-inch round pans\n"
                    "- Stand or hand mixer\n- Offset spatula & cake turntable\n\n"
                    "> **Tip:** Always bring butter and eggs to room temperature."),
        Lesson(course_id=c1.id, position=2, kind="video", duration_min=18, title="The creaming method",
               body="Watch the demonstration, then read the recipe below.\n\n"
                    "### Classic vanilla sponge\n| Ingredient | Amount |\n|---|---|\n"
                    "| Butter | 225 g |\n| Sugar | 225 g |\n| Eggs | 4 |\n| Self-raising flour | 225 g |\n\n"
                    "1. Cream butter and sugar for 4-5 minutes until pale.\n2. Add eggs one at a time.\n"
                    "3. Fold in flour. Bake at 180 °C for 25 minutes."),
        Lesson(course_id=c1.id, position=3, kind="text", duration_min=12, title="Troubleshooting cake faults",
               body="| Problem | Likely cause |\n|---|---|\n| Sunk middle | Oven door opened early / underbaked |\n"
                    "| Dense crumb | Over-mixing after adding flour |\n| Domed top | Oven too hot |"),
        Lesson(course_id=c2.id, position=1, kind="text", duration_min=15, title="American buttercream",
               body="Beat 250 g soft butter for 5 minutes, add 500 g icing sugar gradually, "
                    "a splash of milk and vanilla. Beat until fluffy."),
    ])

    quiz = Assessment(course_id=c1.id, title="Final exam", is_final=True, pass_mark=70, max_attempts=3,
                      time_limit_min=15, instructions="Answer all questions. You need 70% to pass.")
    db.session.add(quiz)
    db.session.flush()
    for i, (text, opts, correct, expl) in enumerate([
        ("Why should you bake by weight rather than volume?",
         ["It is faster", "It is more accurate and consistent", "Cups are not allowed", "It uses less sugar"], 1,
         "Weighing removes the variation in how ingredients pack into cups."),
        ("What does over-mixing after adding flour usually cause?",
         ["A dense, tough crumb", "A sunken middle", "A burnt base", "Nothing"], 0,
         "Over-mixing develops gluten which makes the cake tough."),
        ("At what temperature do we bake the classic vanilla sponge?", ["150 °C", "160 °C", "180 °C", "220 °C"], 2,
         None),
    ], start=1):
        q = Question(assessment_id=quiz.id, position=i, text=text, correct_index=correct, explanation=expl)
        q.options = opts
        db.session.add(q)
    db.session.commit()
