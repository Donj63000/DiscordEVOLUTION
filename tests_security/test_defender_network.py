"""Real Defender/aiohttp classes with synthetic DNS and no external traffic."""

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import defender as defender_module
from defender import (
    AnalysisResult,
    DefenderCog,
    HttpProbeResult,
    PublicAddressResolver,
    ThreatLevel,
    UnsafeProbeError,
    is_public_probe_address,
    validate_probe_url,
)


@pytest.fixture
def cog(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
    monkeypatch.setenv("DEFENDER_HISTORY_ENABLED", "0")
    monkeypatch.setenv("DEFENDER_EXPAND_SHORTLINKS", "1")
    # The network guard must remain mandatory even with the legacy flag off.
    monkeypatch.setenv("DEFENDER_BLOCK_PRIVATE_IPS", "0")
    for name in ("GSB_API_KEY", "VT_API_KEY", "PHISHTANK_APP_KEY"):
        monkeypatch.setenv(name, "")
    return DefenderCog(SimpleNamespace())


def record(address, *, hostname="audit.invalid", port=443):
    return {
        "hostname": hostname,
        "host": address,
        "port": port,
        "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
        "proto": 0,
        "flags": socket.AI_NUMERICHOST,
    }


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
    "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1",
    "192.0.2.1", "::1", "::", "fc00::1", "fe80::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:100.64.0.1",
    "64:ff9b::a00:1", "2002:a00:1::", "ff02::1", "2001:db8::1",
    "fe80::1%eth0", "not-an-ip",
])
def test_forbidden_addresses_are_never_public(address):
    assert not is_public_probe_address(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_unicast_is_accepted(address):
    assert is_public_probe_address(address)


@pytest.mark.parametrize("url", [
    "file:///etc/hosts", "ftp://public.example/a", "https://user:secret@public.example/",
    "https://@public.example/", "http://public.example:8080/",
    "https://public.example:8443/", "http://127.0.0.1/",
    "http://169.254.169.254/", "https://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[fe80::1%25eth0]/", "https:///", "https://public.example:invalid/",
])
def test_url_policy_handles_literals_and_ports(url):
    with pytest.raises(UnsafeProbeError):
        validate_probe_url(url)


@pytest.mark.parametrize("url", [
    "https://public.example/path?a=b", "http://public.example:80/",
    "https://93.184.216.34/", "https://[2606:4700:4700::1111]/",
])
def test_standard_public_urls_are_supported(url):
    assert validate_probe_url(url).scheme in {"http", "https"}


@pytest.mark.asyncio
@pytest.mark.parametrize("records", [
    [], [record("127.0.0.1")], [record("93.184.216.34"), record("10.0.0.1")],
    [record("2606:4700:4700::1111"), record("::ffff:192.168.0.1")],
])
async def test_connector_resolver_rejects_empty_private_and_mixed_records(records):
    upstream = SimpleNamespace(resolve=AsyncMock(return_value=records), close=AsyncMock())
    resolver = PublicAddressResolver(upstream)
    with pytest.raises(UnsafeProbeError):
        await resolver.resolve("audit.invalid", 443)
    await resolver.close()
    upstream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connector_receives_the_same_validated_addresses(cog, monkeypatch):
    addresses = [record("93.184.216.34"), record("2606:4700:4700::1111")]
    upstream = SimpleNamespace(resolve=AsyncMock(return_value=addresses), close=AsyncMock())
    monkeypatch.setattr(defender_module.aiohttp.resolver, "DefaultResolver", lambda: upstream)
    await cog.cog_load()
    try:
        result = await cog.http_session.connector._resolve_host("audit.invalid", 443)
        assert result == addresses
    finally:
        await cog.cog_unload()
    upstream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_dns_cannot_use_a_prior_public_check(cog, monkeypatch):
    upstream = SimpleNamespace(resolve=AsyncMock(return_value=[record("127.0.0.1")]), close=AsyncMock())
    monkeypatch.setattr(defender_module.aiohttp.resolver, "DefaultResolver", lambda: upstream)
    # This is the old independent preflight/cache. It no longer authorizes I/O.
    monkeypatch.setattr(cog, "_host_resolves_to_forbidden", AsyncMock(return_value=(False, True)))
    await cog.cog_load()
    connector = cog.http_session.connector
    connect = AsyncMock(side_effect=AssertionError("No socket may be created for a forbidden result"))
    monkeypatch.setattr(connector, "_wrap_create_connection", connect)
    try:
        assert await cog.expand_url("https://audit.invalid/") is None
        connect.assert_not_awaited()
        assert upstream.resolve.await_count >= 1
    finally:
        await cog.cog_unload()


@pytest.mark.asyncio
async def test_dns_cache_refresh_is_filtered_before_connection(cog, monkeypatch):
    upstream = SimpleNamespace(resolve=AsyncMock(side_effect=[
        [record("93.184.216.34")], [record("::ffff:10.0.0.1")],
    ]), close=AsyncMock())
    monkeypatch.setattr(defender_module.aiohttp.resolver, "DefaultResolver", lambda: upstream)
    await cog.cog_load()
    connector = cog.http_session.connector
    connect = AsyncMock(side_effect=AssertionError("Unexpected connection"))
    monkeypatch.setattr(connector, "_wrap_create_connection", connect)
    try:
        records = await connector._resolve_host("audit.invalid", 443)
        assert records[0]["host"] == "93.184.216.34"
        connector.clear_dns_cache()
        assert await cog.head_or_get("https://audit.invalid/") is None
        connect.assert_not_awaited()
        assert upstream.resolve.await_count == 2
    finally:
        await cog.cog_unload()


@pytest.mark.asyncio
async def test_dns_failure_fails_closed_before_connect(cog, monkeypatch):
    upstream = SimpleNamespace(resolve=AsyncMock(side_effect=OSError("synthetic DNS failure")), close=AsyncMock())
    monkeypatch.setattr(defender_module.aiohttp.resolver, "DefaultResolver", lambda: upstream)
    await cog.cog_load()
    connect = AsyncMock(side_effect=AssertionError("Unexpected network I/O"))
    monkeypatch.setattr(cog.http_session.connector, "_wrap_create_connection", connect)
    try:
        assert await cog.head_or_get("https://audit.invalid/") is None
        connect.assert_not_awaited()
    finally:
        await cog.cog_unload()


@pytest.mark.asyncio
async def test_literal_never_reaches_http_session_even_with_legacy_flag_off(cog):
    cog.http_session = SimpleNamespace(request=Mock(side_effect=AssertionError("Unexpected request")))
    assert await cog.head_or_get("http://127.0.0.1/") is None
    cog.http_session.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("location", [
    "http://127.0.0.1/", "https://[::1]/", "https://public.example:8443/",
    "https://user:secret@public.example/", "file:///etc/hosts",
])
async def test_each_redirect_is_validated_before_request(cog, monkeypatch, location):
    probe = AsyncMock(return_value=HttpProbeResult(302, {"Location": location}))
    monkeypatch.setattr(cog, "head_or_get", probe)
    assert await cog.expand_url("https://bit.ly/a") is None
    probe.assert_awaited_once_with("https://bit.ly/a", method="HEAD")


@pytest.mark.asyncio
async def test_public_redirect_and_head_get_fallback_still_work(cog, monkeypatch):
    probe = AsyncMock(side_effect=[
        HttpProbeResult(302, {"location": "https://public.example/destination"}),
        HttpProbeResult(405, {}), HttpProbeResult(200, {}),
    ])
    monkeypatch.setattr(cog, "head_or_get", probe)
    assert await cog.expand_url("https://bit.ly/a") == "https://public.example/destination"
    assert probe.await_args_list[-1].kwargs == {"method": "GET"}


@pytest.mark.asyncio
async def test_redirect_limit_returns_unverified_instead_of_a_destination(cog, monkeypatch):
    probe = AsyncMock(return_value=HttpProbeResult(302, {"Location": "/again"}))
    monkeypatch.setattr(cog, "head_or_get", probe)
    assert await cog.expand_url("https://bit.ly/a") is None
    assert probe.await_count == 5


@pytest.mark.asyncio
async def test_failed_expansion_is_not_clean_even_with_cached_clean_analysis(cog, monkeypatch):
    url = "https://bit.ly/a"
    cached = AnalysisResult(url, url, "bit.ly", ThreatLevel.CLEAN, 0, [], [])
    cog._cache_set(cog._analysis_cache, url, cached)
    monkeypatch.setattr(cog, "expand_url", AsyncMock(return_value=None))
    monkeypatch.setattr(cog, "_host_resolves_to_forbidden", AsyncMock(return_value=(False, True)))
    monkeypatch.setattr(cog, "_run_providers", AsyncMock(return_value=[]))
    result = await cog.analyser_url(url)
    assert result.level == ThreatLevel.UNKNOWN
    assert any("non vérifiée" in reason for reason in result.reasons)


@pytest.mark.asyncio
async def test_unresolved_domain_is_not_clean(cog, monkeypatch):
    monkeypatch.setattr(cog, "_host_resolves_to_forbidden", AsyncMock(return_value=(False, False)))
    monkeypatch.setattr(cog, "_run_providers", AsyncMock(return_value=[]))
    result = await cog.analyser_url("https://public.example/")
    assert result.level == ThreatLevel.UNKNOWN


@pytest.mark.asyncio
async def test_foreign_guild_message_never_starts_a_scan(cog, monkeypatch):
    scan = AsyncMock(side_effect=AssertionError("A foreign guild must not trigger scans"))
    monkeypatch.setattr(cog, "analyser_url", scan)
    message = SimpleNamespace(author=SimpleNamespace(bot=False), guild=SimpleNamespace(id=2), content="https://bit.ly/a")
    await cog.on_message(message)
    scan.assert_not_awaited()
