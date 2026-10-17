import pytest

import flask


def test_blueprint_name_with_dot_raises():
    with pytest.raises(ValueError):
        flask.Blueprint("myapp.frontend", __name__)


def test_blueprint_name_with_leading_dot_raises():
    with pytest.raises(ValueError):
        flask.Blueprint(".frontend", __name__)


def test_blueprint_name_with_trailing_dot_raises():
    with pytest.raises(ValueError):
        flask.Blueprint("frontend.", __name__)


def test_blueprint_name_without_dot_is_allowed():
    bp = flask.Blueprint("frontend", __name__)

    @bp.route("/")
    def index():
        return "index"

    app = flask.Flask(__name__)
    app.register_blueprint(bp)

    assert app.blueprints["frontend"] is bp
    assert "frontend.index" in app.view_functions


def test_nested_blueprint_names_do_not_contain_dots():
    parent = flask.Blueprint("parent", __name__)
    child = flask.Blueprint("child", __name__)

    @child.route("/")
    def child_index():
        return "child"

    parent.register_blueprint(child, url_prefix="/child")

    @parent.route("/")
    def parent_index():
        return "parent"

    app = flask.Flask(__name__)
    app.register_blueprint(parent, url_prefix="/parent")

    assert "parent.parent_index" in app.view_functions
    assert "parent.child.child_index" in app.view_functions
