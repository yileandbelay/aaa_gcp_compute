"""Kasina Cakery Academy - an online learning platform for cake & pastry training."""
import os

import click
from flask import Flask, render_template
from flask_login import LoginManager
from flask_migrate import Migrate

from .config import Config
from .models import User, db
from .utils import check_csrf, csrf_token, render_markdown

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"
migrate = Migrate()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def create_app(config_object=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    if not app.testing and app.config["SECRET_KEY"] == "dev-only-change-me" and not app.debug:
        app.logger.warning("SECRET_KEY is not set - using an insecure development key.")
    if app.config["PAYMENT_DEMO_MODE"] and not app.testing:
        app.logger.warning("PAYMENT_DEMO_MODE is ON - payments are simulated. Do not use in production.")

    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    # render_as_batch lets schema changes work on SQLite too.
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(__file__), "migrations"),
                     render_as_batch=True)
    login_manager.init_app(app)

    from .routes import admin, assess, auth, learn, main, pay

    for bp in (main.bp, auth.bp, learn.bp, assess.bp, pay.bp, admin.bp):
        app.register_blueprint(bp)

    app.before_request(check_csrf)
    app.jinja_env.globals.update(csrf_token=csrf_token, site_name=app.config["SITE_NAME"],
                                 currency=app.config["CURRENCY"])
    app.jinja_env.filters["markdown"] = render_markdown
    app.jinja_env.filters["money"] = lambda v: f"{float(v or 0):,.2f}"

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    for code in (400, 403, 404, 413):
        app.register_error_handler(
            code, lambda e, code=code: (render_template("error.html", error=e, code=code), code)
        )

    if app.testing:
        with app.app_context():
            db.create_all()
    # Otherwise the schema is managed by migrations: run `flask db upgrade`.

    register_cli(app)
    return app


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt="Full name")
    @click.password_option()
    def create_admin(email, name, password):
        """Create (or promote) an admin account."""
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            user = User(email=email.lower(), full_name=name)
            db.session.add(user)
        user.role = "admin"
        user.set_password(password)
        db.session.commit()
        click.echo(f"Admin {email} ready.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Load a sample course with lessons and a quiz."""
        from .seed import seed

        seed()
        click.echo("Demo data loaded. Admin: admin@kasina.local / admin12345")
