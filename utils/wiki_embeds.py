"""Je présente les fiches du wiki dans des messages Discord lisibles et bornés."""

from __future__ import annotations

import discord

from utils.dofus_wiki import WikiDetail, WikiError, clean_text


ELEMENTS = (("neutral", "Neutre"), ("earth", "Terre"), ("fire", "Feu"),
            ("water", "Eau"), ("air", "Air"))
MAX_DISPLAY_INTEGER = 10 ** 30


def display_text(value: object, limit: int = 900) -> str:
    text = discord.utils.escape_markdown(clean_text(value)).replace("@", "@\u200b")
    return text if len(text) <= limit else text[:limit - 1] + "…"


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
    embed = discord.Embed(title=title[:256], url=detail.entry.url, color=0x2479DB)
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
