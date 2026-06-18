"""Shared ESI gateway helpers (#22)."""

import httpx

from app.plugins.esi_common import ESI_BASE, scope_missing


def test_esi_base_is_unversioned():
    assert ESI_BASE == "https://esi.evetech.net"
    assert "/latest" not in ESI_BASE  # versioning is via X-Compatibility-Date


def test_scope_missing_only_for_401_403():
    assert scope_missing(httpx.Response(401))
    assert scope_missing(httpx.Response(403))
    # 404 is a missing entity, not a scope problem — checked separately by callers.
    assert not scope_missing(httpx.Response(404))
    assert not scope_missing(httpx.Response(200))
