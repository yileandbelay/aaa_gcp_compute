"""Mek Online Academy - an online learning platform for professional training."""
import os

import click
from jinja2 import ChoiceLoader, FileSystemLoader
from flask import Flask, render_template, send_from_directory
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
    try:
        # Holds the default SQLite database for local development. On a server the app
        # directory is usually read-only for the app user, and DATABASE_URL points elsewhere.
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        app.logger.debug("Could not create %s; continuing without it.", app.instance_path)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    if not app.testing and app.config["SECRET_KEY"] == "dev-only-change-me" and not app.debug:
        app.logger.warning("SECRET_KEY is not set - using an insecure development key.")
    if app.config["PAYMENT_DEMO_MODE"] and not app.testing:
        app.logger.warning("PAYMENT_DEMO_MODE is ON - payments are simulated. Do not use in production.")

    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    _install_theme(app)

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


def _install_theme(app):
    """Let a theme folder override templates (THEME_DIR/templates) and add
    static files (THEME_DIR/static, served at /theme/...). theme.css, if present,
    is loaded after the built-in stylesheet."""
    theme = app.config.get("THEME_DIR")
    app.jinja_env.globals["theme_css"] = False
    if not theme:
        return
    theme = os.path.abspath(theme)
    tpl = os.path.join(theme, "templates")
    if os.path.isdir(tpl):
        app.jinja_loader = ChoiceLoader([FileSystemLoader(tpl), app.jinja_loader])
    static = os.path.join(theme, "static")
    if os.path.isdir(static):
        app.add_url_rule("/theme/<path:filename>", "theme_static",
                         lambda filename: send_from_directory(static, filename))
        app.jinja_env.globals["theme_css"] = os.path.exists(os.path.join(static, "theme.css"))


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
    @click.option("--file", "path", type=click.Path(exists=True, dir_okay=False),
                  help="JSON file of courses (default: sample_data/demo_courses.json).")
    def seed_demo(path):
        """Load demo accounts and sample courses. For trying the platform, not for production."""
        from .seed import DEMO_ADMIN, seed

        seed(path)
        click.echo(f"Demo data loaded. Admin: {DEMO_ADMIN[0]} / {DEMO_ADMIN[1]} "
                   "(delete or change this account before going live).")
