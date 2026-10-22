"""Preserved-behavior review check, frozen before the new Flask outputs.

A prior investigation found a patch could remove Flask's wrong-context-pop
diagnostic while passing the ordinary async-stream oracle. This checks that
specific regression; it is final review evidence, never model feedback.
"""
from contextvars import Context

import pytest
from flask import Flask


@pytest.mark.parametrize("kind", ["request", "app"])
def test_wrong_context_pop_is_rejected(kind):
    def exercise():
        app = Flask(__name__)
        # Keep the outer application lifetime independent of request cleanup.
        if kind == "request":
            app.app_context().push()
            first, second = app.test_request_context("/first"), app.test_request_context("/second")
        else:
            first, second = app.app_context(), app.app_context()
        first.push()
        second.push()
        with pytest.raises(AssertionError, match="Popped wrong " + kind + " context"):
            first.pop()

    # Deliberately malformed stacks are discarded with their isolated Context.
    Context().run(exercise)
