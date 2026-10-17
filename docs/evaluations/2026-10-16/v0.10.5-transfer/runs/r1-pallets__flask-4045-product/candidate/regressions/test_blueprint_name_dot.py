"""Regression tests: a blueprint name may not contain a dot.

Dots in blueprint names are significant since blueprints can be nested,
so constructing a blueprint with a dotted name must fail fast.
"""
import pytest

import flask


def test_dotted_blueprint_name_not_allowed():
    with pytest.raises(ValueError):
        flask.Blueprint("app.ui", __name__)


def test_dotted_blueprint_name_error_message():
    with pytest.raises(ValueError) as info:
        flask.Blueprint("myapp.frontend", __name__)

    assert "may not contain a dot" in str(info.value)


def test_empty_blueprint_name_not_allowed():
    with pytest.raises(ValueError) as info:
        flask.Blueprint("", __name__)

    assert "may not be empty" in str(info.value)


def test_valid_blueprint_name_still_registers():
    app = flask.Flask(__name__)
    bp = flask.Blueprint("bp", __name__)

    @bp.route("/")
    def index():
        return "ok"

    app.register_blueprint(bp)
    assert "bp.index" in app.view_functions


def test_nested_blueprints_use_dotted_endpoints():
    app = flask.Flask(__name__)
    parent = flask.Blueprint("parent", __name__, url_prefix="/parent")
    child = flask.Blueprint("child", __name__, url_prefix="/child")

    @child.route("/")
    def index():
        return flask.request.endpoint

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    assert "parent.child.index" in app.view_functions
    assert "/" in {rule.rule for rule in app.url_map.iter_rules()}
