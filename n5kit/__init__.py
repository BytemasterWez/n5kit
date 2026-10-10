"""Small, task-owned host and container run primitives."""
import re


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def token_ok(token):
    require(re.fullmatch(r'[a-f0-9]{32}', token) is not None, 'invalid_token')
