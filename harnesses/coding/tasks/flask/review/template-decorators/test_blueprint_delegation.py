"""Post-hoc compatibility diagnostic prompted by Qwen's source changes.

Existing parenthesized decorators delegated to public add_app_template_*
methods. Check whether subclasses still observe those registrations.
This was not model feedback or part of the frozen behavioral oracle.
"""
import pytest
from flask import Blueprint, Flask


@pytest.mark.parametrize("kind", ["filter", "test", "global"])
def test_existing_decorator_uses_add_registration_method(kind):
    calls = []

    class RecordingBlueprint(Blueprint):
        def add_app_template_filter(self, func, name=None):
            calls.append(("filter", func, name))
            return super().add_app_template_filter(func, name)

        def add_app_template_test(self, func, name=None):
            calls.append(("test", func, name))
            return super().add_app_template_test(func, name)

        def add_app_template_global(self, func, name=None):
            calls.append(("global", func, name))
            return super().add_app_template_global(func, name)

    bp = RecordingBlueprint("example", __name__)

    def marker(value):
        return value

    returned = getattr(bp, "app_template_" + kind)("public_marker")(marker)
    app = Flask(__name__)
    app.register_blueprint(bp)
    assert returned is marker
    assert calls == [(kind, marker, "public_marker")]
