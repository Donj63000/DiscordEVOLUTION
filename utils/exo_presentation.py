"""Donnees de presentation testables sans Discord ; jets et historique pagines."""

from __future__ import annotations

from dataclasses import dataclass, field as data_field
from html import unescape
import math
import re

from utils.exo_engine import (
    DISCLAIMER, PROFILE, STATS, rates_for, recommended_rune, simulation_blocker, surplus,
)
from utils.exo_feedback import amount, result_lines, signed
from utils.exo_math import geometric_quantile, no_success, success_within


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="replace")) // 2


def truncate_text(value: str, maximum: int) -> str:
    if utf16_length(value) <= maximum:
        return value
    encoded = value.encode("utf-16-le", errors="replace")[:max(0, maximum - 1) * 2]
    return encoded.decode("utf-16-le", errors="ignore") + "…"


def display_text(value: object, limit: int = 900) -> str:
    text = " ".join(unescape(str(value or "")).split())
    text = re.sub(r"([\\`*_~|>\[\]])", r"\\\1", text).replace("@", "@\u200b")
    return truncate_text(text, limit)


@dataclass
class EmbedPayload:
    """Structure JSON d'un embed, sans objet ou doublure de bibliotheque Discord."""

    title: str
    description: str
    color: int
    fields: list[dict] = data_field(default_factory=list)
    footer: str = DISCLAIMER

    def add_field(self, *, name: str, value: str, inline: bool = False) -> None:
        self.fields.append({"name": name, "value": value, "inline": inline})

    @property
    def text_length(self) -> int:
        return sum(utf16_length(value) for value in (
            self.title, self.description, self.footer,
            *(part for entry in self.fields for part in (entry["name"], entry["value"])),
        ))

    def to_dict(self) -> dict:
        if self.text_length > 6000 or len(self.fields) > 25:
            raise ValueError("Présentation trop volumineuse pour un message Discord.")
        return {
            "type": "rich", "title": self.title, "description": self.description,
            "color": self.color, "fields": self.fields, "footer": {"text": self.footer},
        }


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


def field(embed: EmbedPayload, name: str, text: str, inline: bool = False) -> None:
    embed.add_field(name=truncate_text(name, 256), value=truncate_text(text or "—", 1024), inline=inline)


def sections(embed: EmbedPayload, title: str, lines: list[str]) -> None:
    """Decoupe aux limites Discord sans supprimer la fin des jets ou des bilans."""
    chunks, current = [], ""
    for line in lines:
        while utf16_length(line) > 950:
            cut = truncate_text(line, 950)
            consumed = len(cut) - 1
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:consumed])
            line = line[consumed:]
        if current and utf16_length(current + "\n" + line) > 950:
            chunks.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    for index, text in enumerate(chunks or ["—"]):
        field(embed, title if index == 0 else title + " · suite", text)


def guidance(session) -> str:
    s = session
    if s.mode == "observation":
        return (
            "Déclarez le jet réel avec « Modifier le jet », puis « Noter un résultat »."
            if not s.observation_ready else
            "Saisissez SC, SN ou EC et toutes les pertes constatées. Aucun tirage n'est effectué."
        )
    blocker = simulation_blocker(s.item, s.state, s.rune)
    if not s.item.automatic or s.state.sink is None or any(value < 0 for value in s.state.jets.values()):
        return "⛔ " + blocker + " Ajustez le scénario dans « Réglages » ou utilisez le suivi manuel."
    missing = [(key, value) for key, value in s.requirements.items()
               if s.state.jets.get(key, 0) < value]
    if s.goal_met and missing:
        text = ", ".join(f"{STATS[key].name} ≥ {value}" for key, value in missing[:6])
        return f"Bonus principal obtenu, mais objet inachevé : remontez {text}. Le bonus peut retomber."
    if s.reached:
        return "✅ Tous vos seuils sont atteints. Exportez pour conserver ce jet ; une nouvelle rune peut l'abîmer."
    if blocker:
        return "⛔ " + blocker + " Changez de rune ou ajustez le scénario dans « Réglages »."
    current = s.state.jets.get(s.rune.stat, 0)
    if current + s.rune.gain > s.rune_target:
        return (
            "Les lots sont arrêtés au seuil de cette ligne. Choisissez une rune plus petite, "
            "une autre ligne ou un objectif plus haut. ×1 autorise un over volontaire admissible."
        )
    return (
        "Choisissez une caractéristique puis « Poser ×1 ». Les lots conservent toutes les pertes "
        "et s'arrêtent avant de dépasser le seuil de la ligne, sans remontage automatique."
    )


def build_payload(session) -> dict:
    item, state, rune = session.item, session.state, session.rune
    mode = "SIMULATION ESTIMATIVE" if session.mode == "simulation" else "SUIVI DÉCLARATIF"
    names = {
        "atelier": "Atelier FM", "maths": "Probabilités & coûts",
        "journal": "Historique détaillé", "aide": "Guide", "settings": "Réglages",
    }
    color = 0x306E83
    if session.tab == "atelier" and state.journal:
        color = {"SC": 0x299D65, "SN": 0xD79526, "EC": 0xD45151}[state.journal[-1]["outcome"]]
    embed = EmbedPayload(
        title=truncate_text(f"{names.get(session.tab, 'Atelier')} · {display_text(item.name, 200)}", 240),
        description=(
            f"**{mode}** · objectif principal : **{STATS[session.goal_stat].name} ≥ {session.goal_value}**\n"
            f"{display_text(item.source, 180)} · [Catalogue Xixou.io](https://xixou.io)"
        ),
        color=color,
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
        if state.journal:
            page = min(session.journal_page, len(state.journal) - 1)
            row = state.journal[-1 - page]
            safe_row = {**row, "rune": display_text(row["rune"], 60)}
            if row.get("rates"):
                safe_row["rates"] = {**row["rates"], "source": display_text(row["rates"]["source"], 160)}
            sections(embed, f"Essai #{row['n']} · {page + 1}/{len(state.journal)} (récent → ancien)",
                     result_lines(safe_row))
            if not row.get("changes"):
                field(embed, "Ancien journal importé", "Les jets avant/après n'étaient pas enregistrés par la v1.")
        else:
            field(embed, "Historique", "Aucune rune utilisée dans ce mode.")
        field(
            embed, "Session",
            f"**{number(state.attempts)} runes utilisées** · **{number(state.successes)} passages SC/SN**\n"
            f"**{number(state.spent)} kamas** déclarés. Les 100 derniers essais de chaque mode sont conservés.\n"
            "Les boutons donnent accès à chaque essai, avec l'ensemble de ses pertes. "
            "Des runes différentes ne forment pas un échantillon de probabilité unique.",
        )
    elif session.tab == "settings":
        field(
            embed, "Recommencer ou corriger",
            "Le menu de jet propose un départ aléatoire, minimum ou parfait (simulation seulement). "
            "Ces actions et « Modifier le jet » remettent les compteurs du mode courant à zéro ; "
            "« Annuler » restaure l'étape précédente.\n"
            "« Objectifs » change seulement les seuils : les jets, dépenses et essais sont conservés.",
        )
        field(
            embed, "Taux / prix de la rune sélectionnée",
            f"Rune : **{rune.name}** · prix : **{number(session.price)} kamas**.\n"
            "SC/SN vides : estimation automatique dépendant du jet et de la taille de rune. "
            "SC/SN saisis : taux fixes de scénario, pour cette rune et cette taille uniquement. "
            "Le preset exo PA/PM/PO absent de l'objet reste à 1 % SC, 0 % SN.\n"
            "Prix 0 : coût non renseigné ou scénario gratuit, pas un prix HDV vérifié.",
        )
        field(
            embed, "Suivi réel et sauvegarde",
            "Simulation et suivi possèdent des jets, dépenses et journaux distincts. "
            "En suivi, déclarez le jet réel et le puits connu, ou « ? ».\n"
            "Atelier privé : 10 minutes d'inactivité, 14 minutes maximum. "
            "Une sauvegarde est jointe à la fermeture/expiration si Discord accepte l'envoi. "
            "Exportez régulièrement : un arrêt brutal du bot ne permet pas cet envoi.",
        )
    elif session.tab == "aide":
        field(
            embed, "Une première séance",
            "1. /exo objet:… ouvre un jet théorique parfait, puits 0. Aucun kama réel n'est engagé.\n"
            "2. Gardez ce départ ou choisissez un jet aléatoire dans « Réglages ».\n"
            "3. Choisissez une caractéristique, une rune puis « Poser ×1 ». "
            "Les gains, pertes, variations nettes et le puits sont affichés.\n"
            "4. Remontez les lignes tombées ; consultez l'historique et exportez votre séance.",
        )
        field(
            embed, "Finir l'objet, pas seulement obtenir un bonus",
            "Les seuils naturels minimums sont conservés par défaut, plus l'objectif principal. "
            "Un Gelano PM sans PA n'est pas terminé si les objectifs sont pm=1 ; pa=1.\n"
            "« Objectifs » accepte plusieurs minimums, le principal en premier : "
            "pm=1 ; pa=1 ; force=45. La réussite signifie que tous ces seuils sont atteints, "
            "pas forcément un jet parfait. Les lots ne dépassent pas leur seuil de ligne.",
        )
        field(
            embed, "Comprendre les résultats",
            "SC : gain sans perte ni consommation de puits. SN : gain et compensation en poids. "
            "EC : pas de gain, compensation en poids. Une ligne peut gagner puis perdre des points au même essai.\n"
            "Puits suivant hors SC = max(0, puits précédent + poids perdu − poids de la rune). "
            "Un jet seul ne permet pas de connaître le puits. "
            "En suivi, une compensation incohérente rend le puits inconnu au lieu d'inventer un zéro.",
        )
        field(
            embed, "Moteur et limites",
            f"**{PROFILE}** : référentiel nominal Rétro (Vi 0,25 ; So 20), pas Dofus Unity/Touch. "
            "Hors preset lourd, les taux automatiques sont une formule pédagogique non calibrée. "
            "Les pertes retirent les surplus tiers, puis le puits, puis des lignes positives tirées au sort ; "
            "la ligne travaillée peut perdre ses points existants.\n"
            "Plafonds nominaux over/exo 101. Malus, effets inconnus, magie élémentaire d'arme, "
            "arrondis et exceptions du serveur ne sont pas fidèlement simulés. "
            "Une fiche non couverte bloque la simulation, pas le carnet manuel.",
        )
        field(
            embed, "Sources et reprise",
            "[Guide Xixou](https://xixou.io/guides/poids-des-runes/) · "
            "[Guide communautaire Rétro](https://www.dofus-retro.com/fr/forum/11-aide-communautaire/1516-guide-forgemagie-retro)\n"
            "Ces guides ne sont pas une spécification du serveur Ankama. "
            "Export JSON v2 : /exo reprise:<fichier.json>, 512 Kio maximum. "
            "La v1 est migrée avec avertissement ; les futurs tirages utilisent la v2.",
        )
    else:
        field(embed, "Prochaine action", guidance(session))
        lines = []
        keys = list(dict.fromkeys([*item.bounds, *state.jets, session.goal_stat]))
        for key in keys:
            current = state.jets.get(key, 0)
            low, high = item.bounds.get(key, (0, 0))
            native = key in item.bounds
            nature = " · EXO" if not native and current > 0 else " · OVER" if native and current > high else ""
            marker = "⚠" if native and current < low else "◆" if key == rune.stat else "•"
            changes = session.display_changes.get(key)
            delta = f" ({signed(changes[1] - changes[0])})" if changes and changes[1] != changes[0] else ""
            natural = f"{low}–{high}" if native else "absent"
            lines.append(
                f"{marker} **{STATS[key].name} : {current}**{delta} · naturel {natural}{nature}"
            )
        sections(embed, "Jet actuel · variations de la dernière action", lines)
        rates = rates_for(item, state, rune, session.rates)
        recommended = recommended_rune(item, state, rune.stat, session.rune_target)
        field(
            embed, f"Rune {rune.name} · +{rune.gain} {STATS[rune.stat].name}",
            f"Poids **{amount(rune.weight)}** · puits **{amount(state.sink)}** · "
            f"over/exo **{amount(surplus(item, state.jets))}/101**\n"
            f"SC **{percent(rates.sc)}** · SN **{percent(rates.sn)}** · EC **{percent(rates.ec)}**\n"
            f"{display_text(rates.source, 160)}\n"
            f"Conseil : {recommended.name} · seuil des lots : {session.rune_target}.",
        )
        requirements = [
            f"{'✅' if state.jets.get(key, 0) >= value else '▫'} {STATS[key].name} ≥ {value}"
            for key, value in session.requirements.items()
        ]
        sections(embed, "Objectifs · tous les minimums doivent être atteints", [" ; ".join(requirements)])
        field(
            embed, "Consommation de la séance",
            f"**{number(state.attempts)} runes utilisées** · **{number(state.successes)} passages SC/SN** · "
            f"**{number(state.spent)} kamas** déclarés\n"
            f"Prix de cette rune : {number(session.price)} kamas. "
            "L'historique détaille chaque essai ; aucun remontage gratuit n'est effectué.",
        )
        if item.immutable:
            field(embed, "Effets d'arme inchangés",
                  "\n".join(display_text(line, 120) for line in item.immutable[:4]))
        if item.unsupported or not item.automatic:
            field(
                embed, "Fiche partiellement couverte : simulation bloquée",
                "\n".join(display_text(line, 140) for line in item.unsupported[:4])
                or "Malus ou absence de lignes reconnues. Le suivi manuel reste disponible.",
            )
    if session.notice and session.tab != "journal":
        available = max(0, min(1600, 6000 - embed.text_length - 180))
        notice = truncate_text(session.notice, available)
        if notice != session.notice:
            notice += "\nBilan abrégé : les essais complets restent dans l'historique et l'export."
        sections(embed, "Bilan de l'action", notice.splitlines())
    return embed.to_dict()
