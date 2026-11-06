from types import SimpleNamespace

import pytest

from api.auth import AuthService


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), super().get(key, default))


def make_handler(*, host="hsx.example", origin=None, authorization=None, path="/api/health"):
    headers = {"host": host}
    if origin is not None:
        headers["Origin"] = origin
    if authorization is not None:
        headers["Authorization"] = authorization
    return SimpleNamespace(headers=Headers(headers), path=path)


def check(service, handler):
    errors = []
    return service.allowed(handler, lambda payload, status: errors.append((payload, status))), errors


@pytest.mark.parametrize(
    "origin",
    [
        "https://hsx.example.evil.com",
        "https://hsx.example:8444",
        "http://hsx.example",
        "null",
        "https://hsx.example/",
    ],
)
def test_disallowed_origins_are_rejected(origin):
    service = AuthService(("https://hsx.example",), ("hsx.example",))
    allowed, errors = check(service, make_handler(origin=origin))
    assert not allowed
    assert errors[0][1] == 403


def test_uppercase_host_is_normalized_and_allowed():
    service = AuthService(("https://hsx.example",), ("hsx.example",))
    allowed, errors = check(
        service,
        make_handler(host="HSX.EXAMPLE", origin="https://HSX.EXAMPLE"),
    )
    assert allowed
    assert not errors


def test_no_origin_api_request_requires_bearer_token():
    service = AuthService(("https://hsx.example",), ("hsx.example",), "long-test-token")
    denied, errors = check(service, make_handler())
    assert not denied
    assert errors[0][1] == 401
    allowed, errors = check(
        service,
        make_handler(authorization="Bearer long-test-token"),
    )
    assert allowed
    assert not errors


def test_host_must_be_allowlisted_even_for_an_allowed_origin():
    service = AuthService(("https://hsx.example",), ("hsx.example",))
    allowed, errors = check(
        service,
        make_handler(host="hsx.example.evil.com", origin="https://hsx.example"),
    )
    assert not allowed
    assert errors[0][1] == 403
