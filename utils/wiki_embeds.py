"""Je présente les fiches du wiki dans des messages Discord lisibles et bornés."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import discord

from utils.dofus_wiki import WikiDetail, WikiError, clean_text

if TYPE_CHECKING:
    from utils.xixou_api import DropSource, ItemEnrichment
    from utils.xixou_maps import MapSpec


ELEMENTS = (("neutral", "Neutre"), ("earth", "Terre"), ("fire", "Feu"),
            ("water", "Eau"), ("air", "Air"))
MAX_DISPLAY_INTEGER = 10 ** 30


def utf16_length(value: str) -> int:
    """Je compte les unités UTF-16 retenues par les limites de Discord."""
    return len(value.encode("utf-16-le", errors="replace")) // 2


def truncate_text(value: str, maximum: int) -> str:
    if utf16_length(value) <= maximum:
        return value
    encoded = value.encode("utf-16-le", errors="replace")[:max(0, maximum - 1) * 2]
    return encoded.decode("utf-16-le", errors="ignore") + "…"


def display_text(value: object, limit: int = 900) -> str:
    text = discord.utils.escape_markdown(clean_text(value)).replace("@", "@\u200b")
    return truncate_text(text, limit)


def number(value: object, *, positive=False) -> str:
    if type(value) is int and value >= (1 if positive else 0):
        if value >= MAX_DISPLAY_INTEGER:
            raise WikiError("Une valeur numérique de cette fiche est incohérente. Consulte son lien wiki.")
        return f"{value:,}".replace(",", " ")
    return "Non renseigné"


def chunks(lines: list[str], maximum: int = 1000) -> list[str]:
    pages, current = [], ""
    for line in lines:
        line = line[:maximum]
        if current and len(current) + len(line) + 1 > maximum:
            pages.append(current)
            current = ""
        current += ("\n" if current else "") + line
    return pages + ([current] if current else [])


def base_embed(detail: WikiDetail, title: str) -> discord.Embed:
    embed = discord.Embed(title=truncate_text(title, 256), url=detail.entry.url, color=0x2479DB)
    embed.set_author(name="Evolution BOT · Dofus Rétro")
    if detail.icon:
        embed.set_thumbnail(url=detail.icon)
    return embed


def finish_pages(pages: list[discord.Embed], detail: WikiDetail) -> list[discord.Embed]:
    for index, embed in enumerate(pages, 1):
        footer = "Source : Wiki Dofus Rétro communautaire"
        if len(pages) > 1:
            footer += f" · Page {index}/{len(pages)}"
        if detail.stale:
            footer += " · Copie en cache, actualisation indisponible"
        embed.set_footer(text=footer)
    return pages


def item_embeds(detail: WikiDetail) -> list[discord.Embed]:
    data = detail.data
    raw_stats = data.get("stats")
    stats = (
        [display_text(stat) for stat in raw_stats if isinstance(stat, str)]
        if isinstance(raw_stats, list) else []
    )
    if len(stats) > 1000:
        raise WikiError("Cette fiche contient trop de caractéristiques. Consulte son lien wiki.")
    sections = chunks(stats) or ["Caractéristiques non renseignées dans le wiki."]
    pages = []
    for section in sections:
        embed = base_embed(detail, f"Objet · {display_text(detail.entry.name, 220)}")
        embed.description = display_text(data.get("description"), 1800) or None
        embed.add_field(name="Type", value=display_text(detail.entry.category, 100))
        embed.add_field(name="Niveau", value=number(data.get("level")))
        embed.add_field(name="Poids (pods)", value=number(data.get("weight")))
        embed.add_field(name="Caractéristiques", value=section, inline=False)
        pages.append(embed)
    return finish_pages(pages, detail)


def recipe_embeds(detail: WikiDetail, quantity: int) -> list[discord.Embed]:
    if not 1 <= quantity <= 10000:
        raise WikiError("La quantité doit être comprise entre 1 et 10 000.")
    raw_recipe = detail.data.get("recipe")
    totals: dict[int, tuple[str, int]] = {}
    if raw_recipe is not None and not isinstance(raw_recipe, list):
        raise WikiError("La recette reçue est incomplète. Consulte la fiche wiki en attendant.")
    if raw_recipe and len(raw_recipe) > 1000:
        raise WikiError("Cette recette contient trop d'ingrédients. Consulte son lien wiki.")
    for ingredient in raw_recipe or []:
        if not isinstance(ingredient, dict):
            raise WikiError("La recette reçue contient un ingrédient illisible.")
        identifier, amount = ingredient.get("item_id"), ingredient.get("qty")
        name = clean_text(ingredient.get("name"))
        if type(identifier) is not int or identifier <= 0 or type(amount) is not int or amount <= 0 or not name:
            raise WikiError("Une quantité ou un ingrédient manque dans la recette du wiki.")
        previous_name, previous_amount = totals.get(identifier, (name, 0))
        if previous_name != name:
            raise WikiError("La recette du wiki contient des ingrédients incohérents.")
        totals[identifier] = (name, previous_amount + amount)
    lines = [f"**{number(amount * quantity)} ×** {display_text(name, 350)}"
             for name, amount in totals.values()]
    sections = chunks(lines) or ["Aucune recette renseignée pour cet objet dans le wiki."]
    pages = []
    for section in sections:
        embed = base_embed(detail, f"Recette · {display_text(detail.entry.name, 210)}")
        embed.description = f"Ingrédients pour **{number(quantity)} exemplaire(s)**."
        embed.add_field(name="Ressources nécessaires", value=section, inline=False)
        pages.append(embed)
    return finish_pages(pages, detail)


def monster_embeds(detail: WikiDetail) -> list[discord.Embed]:
    grades = detail.data.get("grades")
    if not isinstance(grades, list) or not grades:
        embed = base_embed(detail, f"Monstre · {display_text(detail.entry.name, 210)}")
        embed.description = "Statistiques par niveau non renseignées dans le wiki."
        return finish_pages([embed], detail)
    if len(grades) > 100:
        raise WikiError("Cette fiche contient trop de niveaux. Consulte son lien wiki.")
    pages = []
    for grade in grades:
        if not isinstance(grade, dict):
            raise WikiError("Les statistiques de ce monstre sont incomplètes dans le wiki.")
        embed = base_embed(detail, f"Monstre · {display_text(detail.entry.name, 210)}")
        embed.description = f"**Niveau {number(grade.get('level'), positive=True)}**"
        vital_stats_missing = all(grade.get(key) in (None, 0) for key in ("hp", "ap", "mp"))
        missing = []
        for key, label in (("hp", "PV"), ("ap", "PA"), ("mp", "PM")):
            value = None if vital_stats_missing else grade.get(key)
            rendered = number(value, positive=key == "hp")
            if rendered == "Non renseigné":
                missing.append(label)
            embed.add_field(name=label, value=rendered)
        resist = grade.get("resist")
        resist = resist if isinstance(resist, dict) else {}
        known = {label: resist[key] for key, label in ELEMENTS if type(resist.get(key)) is int}
        if any(abs(value) >= MAX_DISPLAY_INTEGER for value in known.values()):
            raise WikiError("Une résistance de cette fiche est incohérente. Consulte son lien wiki.")
        lines = [f"**{label} :** {resist[key]} %" if label in known
                 else f"**{label} :** non renseigné" for key, label in ELEMENTS]
        embed.add_field(name="Résistances", value="\n".join(lines), inline=False)
        if known:
            minimum = min(known.values())
            weakest = ", ".join(label for label, value in known.items() if value == minimum)
            embed.add_field(
                name=("Résistance connue la plus basse" if len(known) < len(ELEMENTS)
                      else "Faiblesse" if minimum < 0 else "Résistance la plus basse"),
                value=f"{weakest} ({minimum} %)", inline=False,
            )
        if missing:
            embed.add_field(
                name="Données manquantes",
                value=f"Non renseignés par cette source : {', '.join(missing)}.",
                inline=False,
            )
        pages.append(embed)
    return finish_pages(pages, detail)


@dataclass(frozen=True)
class ItemPage:
    """Je lie une rubrique à son aperçu facultatif sans charger la carte."""

    embed: discord.Embed
    map_spec: MapSpec | None = None
    map_url: str | None = None


def _full_text(value: object) -> str:
    return discord.utils.escape_markdown(clean_text(value)).replace("@", "@\u200b")


def _field_chunks(lines: list[str], maximum: int = 1000) -> list[str]:
    """Je répartis tout le contenu sur des champs sans perdre les longues lignes."""
    result: list[str] = []
    current = ""
    for line in lines:
        rest = line
        while utf16_length(rest) > maximum:
            if current:
                result.append(current)
                current = ""
            part = rest.encode("utf-16-le", errors="replace")[:maximum * 2].decode(
                "utf-16-le", errors="ignore",
            )
            result.append(part)
            rest = rest[len(part):]
        if not rest:
            continue
        if current and utf16_length(current) + utf16_length(rest) + 1 > maximum:
            result.append(current)
            current = ""
        current += ("\n" if current else "") + rest
    if current:
        result.append(current)
    return result


def _fields(label: str, lines: list[str]) -> list[tuple[str, str]]:
    parts = _field_chunks(lines)
    return [(display_text(label, 230) + (" · suite" if index else ""), part)
            for index, part in enumerate(parts)]


def _attribution(embed: discord.Embed, detail: WikiDetail, enrichment: ItemEnrichment,
                 index: int, total: int) -> None:
    dates = ", ".join(dict.fromkeys(str(value)[:10] for value in enrichment.dates if value))
    source = "[Wiki Dofus Rétro](https://wiki.moon-bot.io) · [Xixou](https://xixou.io/)"
    source += "\nDonnées Xixou : " + (display_text(dates, 250) or "date non renseignée")
    if detail.stale or enrichment.stale:
        source += "\nCopie en cache, actualisation indisponible."
    embed.add_field(name="Sources", value=source, inline=False)
    embed.set_footer(text=f"Evolution BOT · Dofus Rétro · Page {index}/{total}")


def _section_pages(detail: WikiDetail, enrichment: ItemEnrichment, label: str,
                   fields: list[tuple[str, str]], *, description: str = "",
                   map_spec: MapSpec | None = None,
                   map_url: str | None = None) -> list[ItemPage]:
    result = []
    groups = [fields[index:index + 3] for index in range(0, len(fields), 3)] or [[]]
    for index, group in enumerate(groups, 1):
        embed = base_embed(detail, f"{label} · {display_text(detail.entry.name, 210)}")
        embed.description = truncate_text(description, 1000) or None
        for name, value in group:
            embed.add_field(name=truncate_text(name, 256), value=value, inline=False)
        _attribution(embed, detail, enrichment, index, len(groups))
        result.append(ItemPage(embed, map_spec, map_url))
    return result


def _known_value(value: object) -> str:
    if value is None or value == "":
        return "Non renseigné"
    return display_text(str(value), 200)


def _percent_value(value: str) -> Decimal | None:
    try:
        parsed = Decimal(str(value).replace("%", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _drop_rate(drop: DropSource) -> str:
    values = [(numeric, rate) for _, rate in drop.level_rates
              if (numeric := _percent_value(rate)) is not None]
    if values:
        minimum, maximum = min(values), max(values)
        if minimum[0] == maximum[0]:
            return _known_value(minimum[1])
        return f"{_known_value(minimum[1])} à {_known_value(maximum[1])}"
    return _known_value(drop.rate)


def _summary_pages(detail: WikiDetail, enrichment: ItemEnrichment) -> list[ItemPage]:
    data = detail.data
    level = data.get("level")
    weight = data.get("weight")
    level_missing = type(level) is not int or level < 0
    weight_missing = type(weight) is not int or weight < 0
    description = clean_text(data.get("description"))
    if not description and enrichment.description:
        description = enrichment.description
    description = description or "Description non renseignée."
    metadata = [
        f"**Type :** {display_text(detail.entry.category, 200) or 'Non renseigné'}",
        f"**Niveau :** {number(enrichment.level if level_missing else level)}"
        + (" (Xixou)" if level_missing and enrichment.level is not None else ""),
        f"**Poids :** {number(enrichment.weight if weight_missing else weight)} pods"
        + (" (Xixou)" if weight_missing and enrichment.weight is not None else ""),
    ]
    obtain = []
    if enrichment.drops:
        obtain.append(f"**Drop :** {len(enrichment.drops)} monstre(s) renseigné(s). "
                      "Consulte la rubrique Drops pour les taux selon le niveau.")
    if enrichment.zones:
        names = ", ".join(display_text(zone.name, 120) for zone in enrichment.zones[:3])
        obtain.append(f"**Zones :** {names}"
                      + (f" et {len(enrichment.zones) - 3} autre(s)."
                         if len(enrichment.zones) > 3 else "."))
    if enrichment.harvests:
        names = ", ".join(dict.fromkeys(display_text(item.job, 80)
                                       for item in enrichment.harvests))
        obtain.append(f"**Récolte :** {names}.")
    if data.get("recipe"):
        obtain.append("**Fabrication :** recette consultable avec le bouton Voir la recette.")
    if not obtain:
        obtain.append("Moyens d’obtention non renseignés par les sources disponibles.")
    raw_stats = data.get("stats")
    stats = [display_text(value, 180) for value in raw_stats[:3]
             if isinstance(value, str)] if isinstance(raw_stats, list) else []
    if not stats:
        stats = [display_text(value, 180) for value in enrichment.effects[:3]]
    info_fields = _fields("Objet", metadata)
    info_fields += _fields("Principaux effets", stats or ["Non renseignés."])
    info_fields += _fields("Obtention", obtain)
    return _section_pages(detail, enrichment, "Objet", info_fields,
                          description=display_text(description, 1000))


def _detail_pages(detail: WikiDetail, enrichment: ItemEnrichment) -> list[ItemPage]:
    raw_stats = detail.data.get("stats")
    stats = [_full_text(value) for value in raw_stats if isinstance(value, str)] \
        if isinstance(raw_stats, list) else []
    fields = _fields("Caractéristiques · Wiki Dofus Rétro", stats or ["Non renseignées."])
    fields += _fields("Effets · Xixou", [_full_text(value) for value in enrichment.effects]
                      or ["Non renseignés."])
    description = clean_text(detail.data.get("description"))
    if utf16_length(_full_text(description)) > 1000:
        fields += _fields("Description · Wiki Dofus Rétro", [_full_text(description)])
    if enrichment.description and clean_text(enrichment.description) != description:
        fields += _fields("Description · Xixou", [_full_text(enrichment.description)])
    for key, label, extra in (("level", "Niveau", enrichment.level),
                              ("weight", "Poids (pods)", enrichment.weight)):
        original = detail.data.get(key)
        if extra is not None and original is not None and extra != original:
            fields += _fields(f"{label} · Xixou", [number(extra)])
    for name, values in enrichment.details:
        fields += _fields(f"{name} · Xixou", [_full_text(value) for value in values]
                          or ["Non renseigné."])
    return _section_pages(detail, enrichment, "Caractéristiques", fields)


def _drop_pages(detail: WikiDetail, enrichment: ItemEnrichment) -> list[ItemPage]:
    fields = []
    for drop in enrichment.drops:
        lines = [f"**Taux de base :** {_drop_rate(drop)}",
                 f"**Prospection requise :** {_known_value(drop.pp)}",
                 f"**Quantité maximale :** {_known_value(drop.maximum)}"]
        if drop.level_rates:
            lines.append("**Détail selon le niveau :**")
            lines.extend(f"Niveau {_known_value(level)} : {_known_value(rate)}"
                         for level, rate in drop.level_rates)
        else:
            lines.append("**Niveaux :** non renseignés.")
        lines.append("**Zones :** " + (", ".join(_full_text(name) for name in drop.zones)
                                       or "Non renseignées"))
        fields += _fields(drop.name, lines)
    fields = fields or _fields("Drops", ["Drops non renseignés dans les données disponibles."])
    return _section_pages(
        detail, enrichment, "Drops", fields,
        description="Taux de base fournis par Xixou, selon le niveau du monstre lorsqu’il "
                    "est connu. Ils ne représentent pas ta probabilité personnelle de drop.",
    )


def _zone_pages(detail: WikiDetail, enrichment: ItemEnrichment) -> list[ItemPage]:
    from utils.xixou_maps import MapSpec, zone_url

    result = []
    for zone in enrichment.zones:
        link = (zone_url(zone.name, cells=zone.cells, polygon=zone.polygon)
                if zone.cells or zone.polygon else None)
        content = _full_text(zone.name)
        if link:
            content += f"\n[Ouvrir cette zone sur la carte Xixou]({link})"
        else:
            content += "\nLocalisation cartographique non renseignée."
        spec = (MapSpec(title=zone.name, cells=zone.cells, polygon=zone.polygon)
                if zone.cells or zone.polygon else None)
        result.extend(_section_pages(
            detail, enrichment, "Zones et carte", _fields("Zone", [content]),
            description="Zone associée aux monstres. L’aperçu indique la zone, "
                        "sans garantir leur présence sur une case précise.",
            map_spec=spec, map_url=link,
        ))
    if not result:
        result = _section_pages(detail, enrichment, "Zones et carte",
                                _fields("Zones", ["Zones de drop non renseignées."]))
    return result


def _coordinate(value: float) -> str:
    return f"{value:g}"


def _harvest_pages(detail: WikiDetail, enrichment: ItemEnrichment) -> list[ItemPage]:
    from utils.xixou_maps import MapSpec, point_url

    result = []
    for harvest in enrichment.harvests:
        groups = [harvest.positions[index:index + 10]
                  for index in range(0, len(harvest.positions), 10)] or [()]
        for positions in groups:
            information = [f"**Ressource récoltée :** {_full_text(harvest.name)}",
                           f"**Métier :** {_full_text(harvest.job)}",
                           f"**Niveau requis :** {_known_value(harvest.level)}"]
            lines = []
            for x, y, amount in positions:
                coordinate = f"[{_coordinate(x)}, {_coordinate(y)}]"
                link = point_url(x, y)
                label = f"{_coordinate(x)}, {_coordinate(y)}"
                lines.append((f"[{label}]({link})" if link else coordinate)
                             + f" · points : {_known_value(amount)}")
            fields = _fields("Récolte", information)
            fields += _fields("Positions", lines or ["Positions non renseignées."])
            first = positions[0] if positions else None
            spec = MapSpec(title=f"{harvest.name} · Récolte",
                           points=tuple((x, y) for x, y, _ in positions)) if positions else None
            result.extend(_section_pages(
                detail, enrichment, "Récolte", fields,
                description="Positions de récolte renseignées par Xixou. "
                            "Les quantités indiquent les points connus, pas leur disponibilité.",
                map_spec=spec, map_url=point_url(first[0], first[1]) if first else None,
            ))
    if not result:
        result = _section_pages(detail, enrichment, "Récolte",
                                _fields("Récolte", ["Récolte non renseignée."]))
    return result


def enriched_item_sections(detail: WikiDetail, enrichment: ItemEnrichment
                           ) -> dict[str, list[ItemPage]]:
    """Je compose six rubriques complètes sans modifier la fiche d’origine."""
    sections = {
        "summary": _summary_pages(detail, enrichment),
        "details": _detail_pages(detail, enrichment),
        "drops": _drop_pages(detail, enrichment),
        "zones": _zone_pages(detail, enrichment),
        "harvest": _harvest_pages(detail, enrichment),
        "uses": _section_pages(
            detail, enrichment, "Utilisations",
            _fields("Entre dans la fabrication de", [_full_text(name) for name in enrichment.uses]
                    or ["Utilisations non renseignées."]),
        ),
    }
    for pages in sections.values():
        for index, page in enumerate(pages, 1):
            page.embed.set_footer(text=f"Evolution BOT · Dofus Rétro · Page {index}/{len(pages)}")
    return sections
