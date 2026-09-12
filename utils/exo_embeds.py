"""Presentation Discord bornee du simulateur et de ses limites."""

from __future__ import annotations

import math

import discord

from utils.exo_engine import DISCLAIMER, PROFILE, STATS, eligibility, rates_for, surplus
from utils.exo_math import (
    geometric_quantile, no_success, success_within,
)
from utils.wiki_embeds import display_text, truncate_text


def number(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "∞ / non défini"
    if type(value) is int:
        return f"{value:,}".replace(",", " ")
    if not math.isfinite(float(value)):
        return "∞"
    if int(value) == value:
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def percent(value: float) -> str:
    return number(value * 100, 3) + " %"


def field(embed: discord.Embed, name: str, text: str, inline: bool = False) -> None:
    embed.add_field(name=truncate_text(name, 256), value=truncate_text(text or "—", 1024), inline=inline)


def build_embed(session) -> discord.Embed:
    item, state, rune = session.item, session.state, session.rune
    mode = "BAC À SABLE" if session.mode == "simulation" else "SUIVI DÉCLARATIF"
    names = {
        "atelier": "Atelier", "maths": "Probabilités & coûts",
        "journal": "Journal", "aide": "Guide & hypothèses",
    }
    embed = discord.Embed(
        title=truncate_text(f"{names.get(session.tab, 'Atelier')} · {item.name}", 240),
        description=(
            f"**{mode}** · Objectif : **{STATS[session.goal_stat].name} ≥ {session.goal_value}**\n"
            f"{display_text(item.source, 220)} · [Données Xixou.io](https://xixou.io)\n"
            "Aucun objet ni kama réel n'est modifié."
        ),
        color=0x306E83,
    )
    if session.tab == "maths":
        p, n, budget = session.p, session.cap, session.budget
        field(
            embed, "Modèle indépendant : paramètres saisis, pas un taux calculé sur ce jet",
            f"p = **{percent(p)}** par essai · plafond **{number(n)}** · "
            f"campagnes **{number(session.experiments)}**\n"
            "Essais indépendants, arrêt à la première réussite. Aucun « compteur de chance ».\n"
            "À appliquer à un exo admissible ; le bouton de calcul ne certifie pas son admissibilité.",
        )
        field(
            embed, "Probabilités exactes sous cette hypothèse",
            f"Au moins une réussite : **{percent(success_within(p, n))}**\n"
            f"Aucune réussite : **{percent(no_success(p, n))}**\n"
            f"Moyenne sans plafond : **{number(None if p == 0 else 1 / p)} essais**\n"
            f"Médiane : **{number(geometric_quantile(p, .5))}** · "
            f"95 % : **{number(geometric_quantile(p, .95))}**\n"
            f"Seuil {percent(session.confidence)} : "
            f"**{number(geometric_quantile(p, session.confidence))} essais**",
        )
        affordable = budget.affordable()
        field(
            embed, "Kamas : prix déclarés, jamais récupérés d'un hôtel de vente",
            f"Rune exo **{number(budget.rune)}** · remontage entre essais **{number(budget.rebuild)}**\n"
            f"Objet **{number(budget.item)}** · préparation initiale **{number(budget.preparation)}**\n"
            f"Coût au plafond : **{number(budget.cost(n))}**\n"
            f"Espérance jusqu'au succès : **{number(budget.expected_until_success(p))}**\n"
            f"Espérance avec plafond : **{number(budget.expected_capped_cost(p, n))}**\n"
            f"Budget **{number(budget.limit)}** → **{number(affordable)}** essais finançables "
            f"(bornés à 1 000 000) → **{percent(success_within(p, affordable))}**",
        )
        field(
            embed, "Formules",
            "P(≥ 1) = 1 − (1 − p)ⁿ ; E[T] = 1/p.\n"
            "Coût(n ≥ 1) = objet + préparation + n × rune + (n − 1) × remontage.\n"
            "Le remontage est un forfait saisi : il n'est pas simulé rune par rune dans les campagnes.",
        )
    elif session.tab == "journal":
        rows = []
        for row in state.journal[-12:]:
            losses = ", ".join(f"{key} −{n}" for key, n in row["losses"].items() if n) or "aucune"
            rows.append(
                f"#{row['n']} {display_text(row['rune'], 35)} **{row['outcome']}** · "
                f"{display_text(losses, 95)} · puits {row['sink_before']} → {row['sink_after']}"
            )
        field(embed, "12 derniers événements du mode courant", "\n".join(rows) or "Aucune tentative.")
        field(
            embed, "Compteurs descriptifs",
            f"{number(state.attempts)} tentatives · {number(state.successes)} passages SC/SN · "
            f"{number(state.spent)} kamas de runes déclarés.\n"
            "Des runes ou jets différents ne constituent pas un échantillon à taux constant.\n"
            "Ne pas interpréter ce taux observé comme le taux de réussite du serveur.",
        )
        field(
            embed, "Historique exportable",
            "L'export conserve les 100 derniers événements de chaque mode et les compteurs cumulés. "
            "Les observations et simulations sont toujours séparées. "
            "Une modification manuelle du jet redémarre les compteurs de ce mode.",
        )
    elif session.tab == "aide":
        field(
            embed, "Comment démarrer",
            "1. Choisir un objet avec /exo objet:… ou « Rechercher un objet ».\n"
            "2. Saisir le jet, le puits supposé et l'objectif avec « Jet / objectif ».\n"
            "3. Choisir la caractéristique puis la rune ; simuler ou consigner une observation.\n"
            "Le jet initial est au maximum théorique, pas votre objet en jeu. Le puits initial simulé vaut 0 par hypothèse.",
        )
        field(
            embed, "Exo, over et remontage",
            "Exo : bonus absent de la fiche naturelle. Over : bonus au-delà du maximum naturel. "
            "Remettre le PA natif d'un Gelano n'est pas un exo PA.\n"
            "Le preset exo PA/PM/PO retient 1 % SC, 0 % SN. Les autres taux sont à saisir, "
            "jamais déduits du catalogue. Aucun effet de pitié : 100 essais ne garantissent rien.",
        )
        field(
            embed, "Puits et pertes : convention nominale",
            "SC : gain sans perte ni débit de puits. SN : gain et paiement du poids. EC : aucun gain et paiement.\n"
            "Puits nominal suivant = max(0, puits précédent + poids perdu − poids de la rune), hors SC.\n"
            "Il faut un historique et un point de départ déclaré ; un jet seul ne révèle pas le puits.\n"
            "Le modèle retire d'abord les surplus des autres lignes, puis le puits, puis des lignes positives "
            "tirées uniformément. Cette répartition n'est PAS le calcul serveur.",
        )
        field(
            embed, "Référentiel et limites",
            f"Profil **{PROFILE}** : poids nominaux historiques, Vi 0,25 / So 20. "
            "Certaines tables publiques, dont Xixou, diffèrent : aucun profil n'est présenté comme certifié.\n"
            "Plafond nominal over/exo 101 ; plafond de ligne appliqué au jet total hors maximum naturel.\n"
            "Malus, effets inconnus, conversion élémentaire et arrondis internes Rétro : non simulés fidèlement. "
            "Les effets inconnus bloquent l'atelier automatique plutôt que d'être ignorés.",
        )
        field(
            embed, "Sources et sauvegarde",
            "[API Xixou](https://xixou.io/les-outils/api/) · "
            "[Guide Xixou](https://xixou.io/guides/poids-des-runes/) · "
            "[Guide communautaire Rétro](https://www.dofus-retro.com/fr/forum/11-aide-communautaire/1516-guide-forgemagie-retro)\n"
            "Session privée, 10 min d'inactivité, 14 min maximum. Exporter avant expiration ou redémarrage ; "
            "reprendre avec /exo reprise:<fichier.json>. Rien n'est publié dans #console.",
        )
    else:
        lines = []
        keys = list(dict.fromkeys([*item.bounds, *state.jets]))
        for key in keys:
            current = state.jets.get(key, 0)
            low, high = item.bounds.get(key, (0, 0))
            nature = "EXO" if key not in item.bounds else "OVER" if current > high else ""
            lines.append(f"`{key:5}` **{current}** / {low}–{high} {STATS[key].name} {nature}")
        field(embed, "Jet courant / intervalle naturel", "\n".join(lines[:15]) or "Aucune ligne reconnue.")
        if len(lines) > 15:
            field(embed, "Suite du jet", "\n".join(lines[15:]))
        sink = "inconnu — non déductible du jet" if state.sink is None else str(state.sink)
        allowed, why = eligibility(item, state, rune)
        try:
            rates = rates_for(item, state, rune, session.rates)
            rate_text = f"SC {percent(rates.sc)} · SN {percent(rates.sn)} · EC {percent(rates.ec)}\n{rates.source}"
        except ValueError:
            rate_text = "Taux non connus : renseignez des hypothèses avec « Taux / prix rune »."
        field(
            embed, f"Rune {rune.name} · +{rune.gain} {STATS[rune.stat].name}",
            f"Poids nominal **{rune.weight}** · puits **{sink}**\n"
            f"Surplus over/exo nominal **{surplus(item, state.jets)} / 101**\n"
            f"{'Admissible dans le modèle' if allowed else 'BLOQUÉ'} : {why}\n{rate_text}",
        )
        field(
            embed, "Atelier courant",
            f"Objectif {'atteint' if session.reached else 'non atteint'} · "
            f"{number(state.attempts)} runes passées · {number(state.spent)} kamas déclarés.\n"
            f"Prix de cette rune : **{number(session.price)}** · graine **{session.seed}**.\n"
            "×10/×100 gardent le jet abîmé et s'arrêtent à l'objectif : aucun remontage automatique.",
        )
        if session.mode == "observation":
            field(
                embed, "Suivi réel, saisies manuelles seulement",
                "Déclarez d'abord votre jet avec « Jet / objectif »." if not session.observation_ready
                else "Le jet et le puits sont déclaratifs. Consignez le résultat et les pertes constatées en jeu.",
            )
        if item.unsupported or not item.automatic:
            field(
                embed, "Atelier automatique indisponible pour cette fiche",
                "\n".join(display_text(line, 180) for line in item.unsupported[:4])
                or "Malus ou absence de lignes reconnues ; seules les lignes prises en charge sont suivies.",
            )
    if session.notice:
        field(embed, "Dernière action", session.notice)
    embed.set_footer(text=DISCLAIMER)
    return embed
