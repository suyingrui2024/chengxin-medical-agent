"""Regression tests for domain/sub-domain information in ``flask routes``.

Feature request: ``flask routes`` listed the endpoint, methods and rule only,
so there was no way to tell which (sub)domain a route is registered for.
"""

from click.testing import CliRunner

from flask import Blueprint
from flask import Flask
from flask.cli import FlaskGroup


def create_app(server_name=None, register_subdomains=True):
    app = Flask(__name__)
    app.testing = True
    app.subdomain_matching = True

    if server_name is not None:
        app.config["SERVER_NAME"] = server_name

    @app.route("/home")
    def home():
        return ""

    if register_subdomains:
        admin_blueprint = Blueprint("admin_blueprint", __name__)

        @admin_blueprint.route("/home")
        def admin_home():
            return ""

        test_subdomain_blueprint = Blueprint("test_subdomain_blueprint", __name__)

        @test_subdomain_blueprint.route("/home")
        def test_home():
            return ""

        app.register_blueprint(admin_blueprint, url_prefix="", subdomain="admin")
        app.register_blueprint(
            test_subdomain_blueprint, url_prefix="", subdomain="test"
        )

    return app


def invoke_routes(app, *args):
    cli = FlaskGroup(create_app=lambda: app)
    return CliRunner().invoke(cli, ["routes", *args])


def parse_rows(output):
    """Map endpoint to the other columns of each printed row."""
    rows = {}

    for line in output.splitlines()[2:]:
        parts = line.split()

        if parts:
            rows[parts[1]] = parts[0]

    return rows


def test_domain_column_shows_subdomain_and_server_name():
    app = create_app(server_name="test.local")
    result = invoke_routes(app)

    assert result.exit_code == 0
    assert result.output.splitlines()[0].split() == [
        "Domain",
        "Endpoint",
        "Methods",
        "Rule",
    ]

    rows = parse_rows(result.output)
    assert rows["admin_blueprint.admin_home"] == "admin.test.local"
    assert rows["test_subdomain_blueprint.test_home"] == "test.test.local"
    assert rows["home"] == "test.local"


def test_subdomain_shown_without_server_name():
    app = create_app(server_name=None)
    result = invoke_routes(app)

    assert result.exit_code == 0
    assert result.output.splitlines()[0].split()[0] == "Domain"
    assert parse_rows(result.output)["admin_blueprint.admin_home"] == "admin"


def test_historic_columns_kept_without_subdomain_information():
    app = create_app(server_name=None, register_subdomains=False)
    result = invoke_routes(app)

    assert result.exit_code == 0
    assert result.output.splitlines()[0].split() == ["Endpoint", "Methods", "Rule"]
    assert "Domain" not in result.output
    assert "static" in result.output


def test_domain_column_keeps_methods_and_rule_columns():
    app = create_app(server_name="test.local")
    result = invoke_routes(app)

    assert result.exit_code == 0
    assert "/static/<path:filename>" in result.output
    assert "GET" in result.output

    all_methods = invoke_routes(app, "--all-methods")
    assert "GET, HEAD, OPTIONS" in all_methods.output


def test_domain_column_with_sort_options():
    app = create_app(server_name="test.local")

    for sort in ("endpoint", "rule", "methods", "match"):
        result = invoke_routes(app, "-s", sort)
        assert result.exit_code == 0
        assert result.output.splitlines()[0].split()[0] == "Domain"
