import pytest

import flask


def test_blueprint_name_with_dot_raises():
    with pytest.raises(ValueError, match=r"'name' may not contain a dot"):
        flask.Blueprint("myapp.frontend", __name__)


def test_blueprint_name_without_dot_is_allowed():
    bp = flask.Blueprint("frontend", __name__)
    assert bp.name == "frontend"


def test_nested_blueprint_endpoint_prefixes_still_work():
    parent = flask.Blueprint("parent", __name__)
    child = flask.Blueprint("child", __name__)

    @child.route("/child")
    def index():
        return "child"

    parent.register_blueprint(child)
    app = flask.Flask(__name__)
    app.register_blueprint(parent, url_prefix="/parent")

    assert "parent.child.index" in app.view_functions
