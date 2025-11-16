#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord

from avis import build_staff_embed, is_cancel_message, normalize_name, parse_id_list


def test_normalize_name_strips_symbols():
    assert normalize_name("  #Avis!!!  ") == "avis"
    fancy_name = (
        "\U0001F4D1\U0001D406\U0001D41E\u0301\U0001D427\U0001D41E\u0301"
        "\U0001D42B\U0001D41A\U0001D425\U0001F4D1"
    )
    assert normalize_name(fancy_name)


def test_parse_id_list_filters_invalid_entries():
    assert parse_id_list("123, ,abc,45") == [123, 45]


def test_is_cancel_message_variants():
    assert is_cancel_message("ANNULER")
    assert is_cancel_message(" stop ")
    assert not is_cancel_message("continuer")


def test_build_staff_embed_contains_context():
    embed = build_staff_embed("Contenu test", "AB12", "Evolution", "#avis")
    assert "Contenu test" in embed.description
    assert "Evolution" in embed.fields[0].value
    assert "#avis" in embed.fields[0].value
    assert "AB12" in embed.title
    assert isinstance(embed, discord.Embed)
