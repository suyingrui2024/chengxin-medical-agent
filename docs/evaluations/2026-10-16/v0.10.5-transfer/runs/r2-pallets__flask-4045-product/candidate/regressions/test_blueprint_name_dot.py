import pytest

from flask import Blueprint
from flask import Flask


def test_blueprint_name_with_dot_raises():
    with pytest.raises(ValueError, match="may not contain a dot"):
        Blueprint("bp.name", __name__)


def test_blueprint_empty_name_raises():
    with pytest.raises(ValueError, match="may not be empty"):
        Blueprint("", __name__)


def test_valid_blueprint_name_still_works():
    app = Flask(__name__)
    bp = Blueprint("bp", __name__)

    @bp.route("/")
    def index():
        return "ok"

    app.register_blueprint(bp)
    assert bp.name == "bp"
    assert app.url_map.bind("localhost").build("bp.index") == "/"


def test_nested_blueprint_name_prefix_has_single_dot():
    app = Flask(__name__)
    parent = Blueprint("parent", __name__)
    child = Blueprint("child", __name__)

    @child.route("/")
    def index():
        return "ok"

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    assert app.url_map.bind("localhost").build("parent.child.index") == "/"
