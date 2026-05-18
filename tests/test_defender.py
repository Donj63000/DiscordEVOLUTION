from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from defender import DefenderCog, ThreatLevel


@pytest.fixture
def defender(monkeypatch):
    monkeypatch.setenv("DEFENDER_HISTORY_ENABLED", "0")
    monkeypatch.setenv("DEFENDER_EXPAND_SHORTLINKS", "0")
    return DefenderCog(bot=SimpleNamespace())


async def _resolved_public(_host: str) -> tuple[bool, bool]:
    return False, True


def test_extracts_bare_domain_and_userinfo_url(defender):
    candidates = defender.extraire_urls(
        "Regarde discord-nitro-free-gift.xyz et paypal.com@evil-domain.com"
    )

    urls = {candidate.normalized for candidate in candidates}
    userinfo = [candidate for candidate in candidates if candidate.userinfo_present]

    assert "https://discord-nitro-free-gift.xyz/" in urls
    assert userinfo
    assert userinfo[0].host == "evil-domain.com"


def test_extracts_obfuscated_idn_url(defender):
    candidates = defender.extraire_urls("hxxp://díscord-login[.]com")

    assert len(candidates) == 1
    assert candidates[0].scheme == "http"
    assert candidates[0].obfuscated is True
    assert candidates[0].host.startswith("xn--")


def test_extracts_bare_idn_url(defender):
    candidates = defender.extraire_urls("Attention à díscord-login.com")

    assert len(candidates) == 1
    assert candidates[0].scheme == "https"
    assert candidates[0].host.startswith("xn--")


@pytest.mark.asyncio
async def test_official_domain_stays_clean(defender):
    result = await defender.analyser_url("https://discord.com")

    assert result is not None
    assert result.level == ThreatLevel.CLEAN
    assert result.score == 0


@pytest.mark.asyncio
async def test_typosquatted_discord_domain_is_dangerous(defender, monkeypatch):
    monkeypatch.setattr(defender, "_host_resolves_to_forbidden", _resolved_public)

    result = await defender.analyser_url("discord-nitro-free-gift.xyz")

    assert result is not None
    assert result.level == ThreatLevel.DANGEROUS
    assert result.score >= 75
    assert any("discord" in reason.lower() for reason in result.reasons)


@pytest.mark.asyncio
async def test_obfuscated_idn_brand_is_dangerous(defender, monkeypatch):
    monkeypatch.setattr(defender, "_host_resolves_to_forbidden", _resolved_public)

    result = await defender.analyser_url("hxxp://díscord-login[.]com")

    assert result is not None
    assert result.level == ThreatLevel.DANGEROUS
    assert any("homographe" in reason.lower() for reason in result.reasons)
    assert any("discord" in reason.lower() for reason in result.reasons)


@pytest.mark.asyncio
async def test_bare_idn_brand_is_dangerous(defender, monkeypatch):
    monkeypatch.setattr(defender, "_host_resolves_to_forbidden", _resolved_public)

    result = await defender.analyser_url("díscord-login.com")

    assert result is not None
    assert result.level == ThreatLevel.DANGEROUS
    assert any("discord" in reason.lower() for reason in result.reasons)


@pytest.mark.asyncio
async def test_local_ip_is_dangerous(defender):
    result = await defender.analyser_url("http://127.0.0.1:8080")

    assert result is not None
    assert result.level == ThreatLevel.DANGEROUS
    assert any("locale" in reason.lower() or "privée" in reason.lower() for reason in result.reasons)


@pytest.mark.asyncio
async def test_on_message_keeps_clean_links_silent(defender, monkeypatch):
    bot = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=False, command=None)))
    defender.bot = bot
    monkeypatch.setattr(defender, "_log_results", AsyncMock())
    monkeypatch.setattr(defender, "_handle_dangerous_message", AsyncMock())
    monkeypatch.setattr(defender, "_send_public_suspicious_notice", AsyncMock())

    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        guild=SimpleNamespace(id=1, text_channels=[]),
        content="Lien officiel https://discord.com",
    )

    await defender.on_message(message)

    defender._log_results.assert_not_awaited()
    defender._handle_dangerous_message.assert_not_awaited()
    defender._send_public_suspicious_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_logs_suspicious_links_without_public_alert(defender, monkeypatch):
    bot = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=False, command=None)))
    defender.bot = bot
    monkeypatch.setattr(defender, "_host_resolves_to_forbidden", _resolved_public)
    monkeypatch.setattr(defender, "_log_results", AsyncMock())
    monkeypatch.setattr(defender, "_handle_dangerous_message", AsyncMock())
    monkeypatch.setattr(defender, "_send_public_suspicious_notice", AsyncMock())

    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        guild=SimpleNamespace(id=1, text_channels=[]),
        content="Lien douteux paypal-secure-login.xyz",
    )

    await defender.on_message(message)

    defender._log_results.assert_awaited_once()
    defender._handle_dangerous_message.assert_not_awaited()
    defender._send_public_suspicious_notice.assert_not_awaited()
