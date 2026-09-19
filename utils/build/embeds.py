"""Présentation Discord, sans aucun calcul métier dans le transport."""
import discord
from .models import STAT_LABELS, SLOTS
from .editor import SLOT_LABELS
from .renderer import NATURE_LABELS, COMPLETENESS_LABELS
from .renderer import KEY_STATS, metric_text


def safe(value, maximum=1000):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))[:maximum]


def card(build, report, catalog, durable, *, preview=False):
    embed = discord.Embed(title=safe(("Prévisualisation — " if preview else "Evolution Build — ") + build.name, 256),
                          description=f"{safe(build.profile.classe.capitalize())} · niveau {build.profile.level} · révision {build.revision}\n"
                          + ("**Aucun changement enregistré. Confirme ci-dessous.**" if preview else "Sauvegarde PostgreSQL." if durable else "**ESSAI EN MÉMOIRE : perdu au redémarrage. Exporte le JSON.**"),
                          colour=0x2A79BB)
    labels = {"valid": "Contrôles couverts respectés", "invalid": "Non équipable", "unknown": "À vérifier"}
    embed.add_field(name="Validation", value=f"{labels[report.equipability]} · {COMPLETENESS_LABELS[report.completeness]} · {NATURE_LABELS[report.nature]}", inline=False)
    for keys in (KEY_STATS[:7], KEY_STATS[7:]):
        embed.add_field(name="Caractéristiques", value="\n".join(f"{STAT_LABELS[s]} : **{metric_text(report, s)}**" for s in keys), inline=True)
    equipment = []
    for slot in SLOTS:
        instance = build.in_slot(slot)
        name = safe(catalog.resolve(instance).name, 85) if instance else "—"
        equipment.append(f"{SLOT_LABELS[slot]} : {name}")
    # Deux champs bornés : les derniers Dofus ne disparaissent plus à 1024 caractères.
    for start in (0, 8):
        embed.add_field(name="Équipement" if start == 0 else "Équipement (suite)",
                        value="\n".join(equipment[start:start + 8]), inline=False)
    diagnostics = report.errors + report.warnings
    text = "\n".join(safe(d.text, 180) for d in diagnostics[:4])
    if len(diagnostics) > 4:
        text += f"\n… {len(diagnostics) - 4} autres réserves dans Détails."
    embed.add_field(name="Réserves", value=("**\\* = somme partielle, pas un total certifié.**\n" + text)[:1024], inline=False)
    embed.add_field(name="Sources", value="[Xixou](https://xixou.io/) · [Wiki Moon](https://wiki.moon-bot.io/)", inline=False)
    embed.set_footer(text=f"Catalogue {build.catalog_id[:12]} · Règles {build.rules_id[:12]} · /build aide")
    return embed
