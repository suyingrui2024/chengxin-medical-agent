"""Regression tests for showing the domain/subdomain of each route in
``flask routes``.

These tests are self-contained (they do not rely on ``tests/conftest.py``)
so that they can run straight from the ``regressions`` directory.
"""

from __future__ import annotations

from click.testing import CliRunner

from flask import Blueprint
from flask import Flask
from flask.cli import FlaskGroup


def make_runner(app):
    cli = FlaskGroup(create_app=lambda: app)
    return CliRunner().invoke(cli, ["routes"])


def test_domain_column_shows_subdomains():
    app = Flask(__name__)
    app.config["SERVER_NAME"] = "test.local"

    admin = Blueprint("admin", __name__)

    @admin.route("/home")
    def admin_home():
        return ""

    test = Blueprint("test", __name__)

    @test.route("/home")
    def test_home():
        return ""

    app.register_blueprint(admin, url_prefix="", subdomain="admin")
    app.register_blueprint(test, url_prefix="", subdomain="test")

    result = make_runner(app)
    assert result.exit_code == 0

    lines = result.output.splitlines()
    assert lines[0].split()[:2] == ["Domain", "Endpoint"]

    rows = {line.split()[0]: line for line in lines[2:] if line.split()}
    assert "admin.test.local" in rows
    assert "test.test.local" in rows

    admin_row = rows["admin.test.local"]
    assert "admin.admin_home" in admin_row
    assert "/home" in admin_row

    test_row = rows["test.test.local"]
    assert "test.test_home" in test_row
    assert "/home" in test_row


def test_domain_column_shown_for_server_name_without_subdomain():
    app = Flask(__name__, static_folder=None)
    app.config["SERVER_NAME"] = "test.local"
    app.testing = True

    @app.route("/home")
    def home():
        return ""

    result = make_runner(app)
    assert result.exit_code == 0
    assert result.output.splitlines()[0].split()[:2] == ["Domain", "Endpoint"]
    assert "test.local" in result.output


def test_output_unchanged_when_no_domain_information():
    app = Flask(__name__)
    app.testing = True

    @app.route("/home")
    def home():
        return ""

    result = make_runner(app)
    assert result.exit_code == 0

    lines = result.output.splitlines()
    assert lines[0] == "Endpoint  Methods  Rule"
    assert "Domain" not in lines[0]
    for line in lines[2:]:
        assert not line.startswith(" ")


def test_sort_by_domain():
    app = Flask(__name__)
    app.config["SERVER_NAME"] = "test.local"

    b = Blueprint("b", __name__)

    @b.route("/b")
    def b_index():
        return ""

    a = Blueprint("a", __name__)

    @a.route("/a")
    def a_index():
        return ""

    app.register_blueprint(b, subdomain="zulu")
    app.register_blueprint(a, subdomain="alpha")

    cli = FlaskGroup(create_app=lambda: app)
    result = CliRunner().invoke(cli, ["routes", "-s", "domain"])
    assert result.exit_code == 0

    domains = [line.split()[0] for line in result.output.splitlines()[2:] if line.split()]
    assert domains.index("alpha.test.local") < domains.index("zulu.test.local")
