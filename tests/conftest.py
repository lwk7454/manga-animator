"""The default suite must not reach model servers or download weights."""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_live_connections(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("live network access in an offline test; inject a fake")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
