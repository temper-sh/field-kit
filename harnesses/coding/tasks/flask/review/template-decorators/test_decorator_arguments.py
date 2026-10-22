"""Post-hoc source-review diagnostic, never model feedback.

Muse's first decorator proposal replaced explicit signatures with unchecked
*args/**kwargs. Verify the resulting suspected loss of existing argument
validation against both the unchanged checkout and retained submissions.
This supplements the frozen oracle; it is not a predeclared benchmark case.
"""
import pytest
from flask import Blueprint, Flask


@pytest.mark.parametrize("owner_kind", ["app", "blueprint"])
@pytest.mark.parametrize("kind", ["filter", "test", "global"])
@pytest.mark.parametrize("misuse", ["unknown-keyword", "extra-positional", "duplicate-name"])
def test_existing_argument_errors_remain(owner_kind, kind, misuse):
    owner = Flask(__name__) if owner_kind == "app" else Blueprint("example", __name__)
    prefix = "template_" if owner_kind == "app" else "app_template_"
    method = getattr(owner, prefix + kind)
    with pytest.raises(TypeError):
        if misuse == "unknown-keyword":
            method(unrecognized="value")
        elif misuse == "extra-positional":
            method("first", "second")
        else:
            method("first", name="second")
