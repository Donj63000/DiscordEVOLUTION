from dataclasses import replace

from utils.dofus_wiki import WIKI_ORIGIN, WikiDetail, parse_entries
from utils.wiki_embeds import display_text, enriched_item_sections, utf16_length
from utils.xixou_api import DropSource, Harvest, ItemEnrichment, Zone


def detail_for(name="Gelano", category="Anneau", **changes):
    entry = parse_entries([
        {"id": 1, "name": name, "type": category, "level": 60,
         "url": WIKI_ORIGIN + "/items/gelano/"},
    ], "item")[0]
    data = {"name": name, "description": "Une bague confortable.", "level": 60,
            "weight": 5, "stats": ["+1 PA"], "recipe": [], **changes}
    return WikiDetail(entry, data, None, False)


def text_of(pages):
    return "\n".join(
        "\n".join([page.embed.title or "", page.embed.description or "",
                    *[field.name + "\n" + field.value for field in page.embed.fields],
                    page.embed.footer.text or ""])
        for page in pages
    )


def assert_valid_embed(embed):
    assert utf16_length(embed.title or "") <= 256
    assert utf16_length(embed.description or "") <= 4096
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert 1 <= utf16_length(field.name) <= 256
        assert 1 <= utf16_length(field.value) <= 1024
    total = sum(utf16_length(value) for value in (
        embed.title or "", embed.description or "", embed.author.name or "",
        embed.footer.text or "",
        *[piece for field in embed.fields for piece in (field.name, field.value)],
    ))
    assert total <= 6000


def test_all_six_sections_have_attribution_and_explicit_missing_information():
    sections = enriched_item_sections(detail_for(stats=[]), ItemEnrichment())
    assert list(sections) == ["summary", "details", "drops", "zones", "harvest", "uses"]
    for pages in sections.values():
        assert pages
        text = text_of(pages)
        assert "non renseign" in text.lower()
        assert "[Xixou](https://xixou.io/)" in text
        assert "impossible" not in text.lower()
        assert all(page.map_spec is None for page in pages)
        for page in pages:
            assert_valid_embed(page.embed)


def test_gelano_shows_ordinary_and_archmonster_rates_at_every_level():
    enrichment = ItemEnrichment(
        effects=("+1 PA",),
        details=(("Panoplie", ("Panoplie Gelax",)),),
        dates=("2026-09-01T10:30:07+00:00",),
        drops=(
            DropSource("Gelée Fraise", rate="0.07%", pp="1000", maximum="1",
                       level_rates=(("24", "0.03%"), ("26", "0.04%"), ("28", "0.05%"),
                                    ("29", "0.06%"), ("30", "0.07%")),
                       zones=("La péninsule des gelées",)),
            DropSource("Gelaviv le Glaçon", rate="0.14%", pp="1000", maximum="1",
                       level_rates=(("24", "0.06%"), ("30", "0.14%")),
                       zones=("La péninsule des gelées",)),
        ),
    )
    sections = enriched_item_sections(detail_for(), enrichment)
    drops = text_of(sections["drops"])
    assert "0.03% à 0.07%" in drops
    assert "0.06% à 0.14%" in drops
    assert "Niveau 28 : 0.05%" in drops
    assert "Gelée Fraise" in drops and "Gelaviv le Glaçon" in drops
    assert "1000" in drops and "Quantité maximale" in drops
    assert "probabilité personnelle" in drops
    assert "Panoplie Gelax" in text_of(sections["details"])
    for pages in sections.values():
        assert "2026-09-01" in text_of(pages)


def test_tiny_zero_and_unknown_drop_values_remain_distinct():
    enrichment = ItemEnrichment(drops=(
        DropSource("Crocabulia", rate="0.001%", pp="1000", maximum="1"),
        DropSource("Taux nul", rate="0%", pp=0, maximum=0),
        DropSource("Taux absent"),
    ))
    text = text_of(enriched_item_sections(detail_for(), enrichment)["drops"])
    assert "0.001%" in text
    assert "**Taux de base :** 0%" in text
    assert "**Prospection requise :** 0" in text
    assert "**Quantité maximale :** 0" in text
    assert "**Taux de base :** Non renseigné" in text


def test_summary_fills_missing_fields_without_overwriting_existing_values():
    enrichment = ItemEnrichment(description="La description Xixou.", level=61, weight=6,
                               effects=("+2 PA",))
    original = detail_for()
    sections = enriched_item_sections(original, enrichment)
    summary = text_of(sections["summary"])
    details = text_of(sections["details"])
    assert "Une bague confortable." in summary
    assert "**Niveau :** 60" in summary
    assert "**Poids :** 5 pods" in summary
    assert "+1 PA" in summary
    assert "Niveau · Xixou\n61" in details
    assert "Poids (pods) · Xixou\n6" in details
    assert "+1 PA" in details and "+2 PA" in details
    assert "La description Xixou." in details
    missing = detail_for(description="", level=None, weight=None, stats=[])
    fallback = text_of(enriched_item_sections(missing, enrichment)["summary"])
    assert "**Niveau :** 61 (Xixou)" in fallback
    assert "**Poids :** 6 pods (Xixou)" in fallback
    assert "La description Xixou." in fallback
    assert original.data["level"] == 60
    assert original.data["weight"] == 5


def test_harvest_pages_preserve_all_coordinates_and_quantities():
    positions = tuple((index, -index, index % 3) for index in range(23))
    enrichment = ItemEnrichment(harvests=(
        Harvest(name="Frêne", job="Bûcheron", level="1", positions=positions),
    ))
    sections = enriched_item_sections(detail_for(name="Bois de Frêne", category="Bois"),
                                      enrichment)
    pages = sections["harvest"]
    assert len(pages) == 3
    assert [len(page.map_spec.points) for page in pages] == [10, 10, 3]
    assert [point for page in pages for point in page.map_spec.points] == [
        (x, y) for x, y, _ in positions
    ]
    text = text_of(pages)
    assert "Niveau requis :** 1" in text
    assert "Bûcheron" in text
    assert "points : 0" in text
    assert "#x=22&y=-22" in text
    assert "Page 3/3" in pages[-1].embed.footer.text
    assert all(page.map_url for page in pages)
    assert all("[Xixou]" in text_of([page]) for page in pages)


def test_resource_can_show_both_drop_and_harvest():
    enrichment = ItemEnrichment(
        drops=(DropSource("Coffre au trésor", rate="1%"),),
        harvests=(Harvest("Cuivre", "Mineur", "20", ((1, 2, 3),)),),
    )
    sections = enriched_item_sections(detail_for(name="Cuivre", category="Minerai"), enrichment)
    summary = text_of(sections["summary"])
    assert "**Drop :** 1 monstre" in summary
    assert "**Récolte :** Mineur" in summary
    assert sections["harvest"][0].map_spec is not None
    assert "Coffre au trésor" in text_of(sections["drops"])


def test_zones_get_individual_maps_and_unknown_geometry_stays_explicit():
    zones = (
        Zone("La péninsule des gelées", cells=((11, 28), (11, 29))),
        Zone("Le coin des Bouftous", polygon=((0, 9), (6, 9), (6, 13), (0, 13))),
        Zone("Sanctuaire des Dragoeufs"),
    )
    pages = enriched_item_sections(detail_for(), ItemEnrichment(zones=zones))["zones"]
    assert len(pages) == 3
    assert pages[0].map_spec.cells == zones[0].cells
    assert pages[1].map_spec.polygon == zones[1].polygon
    assert pages[2].map_spec is None
    assert pages[2].map_url is None
    assert "Localisation cartographique non renseignée" in text_of([pages[2]])
    assert "sans garantir" in pages[0].embed.description
    assert "#zone=" in pages[0].map_url
    assert "Page 3/3" in pages[-1].embed.footer.text


def test_all_uses_and_monsters_are_paginated_without_omission():
    uses = tuple(f"Objet de fabrication numéro {index:03d}" for index in range(170))
    drops = tuple(DropSource(f"Monstre numéro {index:03d}", rate="1%") for index in range(58))
    sections = enriched_item_sections(detail_for(), ItemEnrichment(uses=uses, drops=drops))
    assert len(sections["uses"]) > 1
    assert len(sections["drops"]) > 1
    uses_text = text_of(sections["uses"])
    drops_text = text_of(sections["drops"])
    for name in uses:
        assert name in uses_text
    for drop in drops:
        assert drop.name in drops_text
    for pages in sections.values():
        for page in pages:
            assert_valid_embed(page.embed)


def test_unicode_long_fields_and_mentions_respect_all_discord_limits():
    value = "🧙" * 1800 + " fin @everyone **effet** <@1234>"
    enrichment = ItemEnrichment(
        description=value,
        effects=(value, value),
        details=(("🧙" * 300, (value, value)),),
        uses=(value, value, "Dernière utilisation"),
        drops=tuple(DropSource("🧙" * 300, rate="0%", zones=(value,)) for _ in range(7)),
        dates=("2026-09-01T10:30:07+00:00",),
    )
    detail = replace(detail_for(description=value), entry=replace(detail_for().entry,
                                                               name="🧙" * 300))
    sections = enriched_item_sections(detail, enrichment)
    for pages in sections.values():
        for page in pages:
            assert_valid_embed(page.embed)
            text = text_of([page])
            assert "@everyone" not in text
            assert "<@1234>" not in text
    assert "Dernière utilisation" in text_of(sections["uses"])
    assert "Description · Wiki Dofus Rétro" in text_of(sections["details"])
    assert "fin @\u200beveryone" in text_of(sections["details"])
    assert utf16_length(display_text("🧙" * 900, 900)) <= 900


def test_stale_flag_from_either_source_is_visible_on_every_page():
    for original_stale, extra_stale in ((True, False), (False, True)):
        detail = replace(detail_for(), stale=original_stale)
        sections = enriched_item_sections(detail, ItemEnrichment(stale=extra_stale))
        assert all("Copie en cache" in text_of([page])
                   for pages in sections.values() for page in pages)
