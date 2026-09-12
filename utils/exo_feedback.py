"""Messages de FM complets : gains bruts, pertes et variations nettes distincts."""

from __future__ import annotations

from decimal import Decimal

from utils.exo_engine import STATS

OUTCOMES = {
    "SC": ("🟢", "Succès critique"),
    "SN": ("🟠", "Succès neutre"),
    "EC": ("🔴", "Échec"),
}


def amount(value) -> str:
    if value is None:
        return "inconnu"
    number = Decimal(str(value))
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def signed(value: int | Decimal) -> str:
    return ("+" if value > 0 else "−" if value < 0 else "±") + amount(abs(value))


def sink_line(before, after) -> str:
    delta = ""
    if before is not None and after is not None:
        delta = f" ({signed(Decimal(str(after)) - Decimal(str(before)))})"
    return f"Puits : **{amount(before)} → {amount(after)}**{delta}."


def result_lines(row: dict, *, detailed: bool = True) -> list[str]:
    emoji, title = OUTCOMES[row["outcome"]]
    lines = [f"{emoji} **{title} ({row['outcome']})** · {row['rune']} · essai #{row['n']}"]
    gained = row["gain"] if row["outcome"] != "EC" else 0
    lines.append(
        f"Gain de la rune : **+{gained} {STATS[row['stat']].name}**."
        if gained else "La rune ne passe pas : **aucun gain**."
    )
    losses = [f"−{value} {STATS[key].name}" for key, value in row["losses"].items() if value]
    lines.append("Pertes : " + (" ; ".join(losses) if losses else "**aucune caractéristique perdue**") + ".")
    if detailed and row.get("changes"):
        lines.append("**Évolution du jet (gain et pertes cumulés)**")
        for key, (before, after) in row["changes"].items():
            lines.append(f"{STATS[key].name} : **{before} → {after}** ({signed(after - before)})")
    lines.append(sink_line(row["sink_before"], row["sink_after"]))
    lines.append(f"Coût déclaré : {row['price']:,} kamas.".replace(",", " "))
    if Decimal(row["unexplained_weight"]) > 0:
        if row["mode"] == "observation":
            lines.append(
                "⚠ Pertes et puits déclarés insuffisants pour expliquer ce résultat : "
                "puits désormais inconnu. Vérifiez votre saisie."
            )
        else:
            lines.append(
                f"⚠ Poids non compensé : {amount(row['unexplained_weight'])}. "
                "Plus assez de caractéristiques disponibles ; aucun poids négatif n'est inventé."
            )
    if detailed and row.get("rates"):
        rates = row["rates"]
        lines.append(
            f"Taux utilisés : SC {amount(round(rates['sc'] * 100, 6))} % ; "
            f"SN {amount(round(rates['sn'] * 100, 6))} %. {rates['source']}."
        )
    return lines


def batch_text(result) -> str:
    if len(result.rows) == 1:
        return "\n".join(result_lines(result.rows[0], detailed=False) + [result.stop_reason])
    counts = {key: sum(row["outcome"] == key for row in result.rows) for key in OUTCOMES}
    gain = sum(row["gain"] for row in result.rows if row["outcome"] != "EC")
    stat = STATS[result.rows[0]["stat"]].name
    losses = " ; ".join(f"−{value} {STATS[key].name}" for key, value in result.losses.items())
    cost = sum(row["price"] for row in result.rows)
    uncompensated = sum(Decimal(row["unexplained_weight"]) > 0 for row in result.rows)
    lines = [
        f"**{len(result.rows)}/{result.requested} runes utilisées** · "
        f"{counts['SC']} SC · {counts['SN']} SN · {counts['EC']} EC",
        f"Gains bruts : **+{gain} {stat}**. Pertes cumulées : {losses or 'aucune'}.",
        sink_line(result.sink_before, result.sink_after),
        f"Coût du lot : **{cost:,} kamas**.".replace(",", " "),
        result.stop_reason,
        "Les variations du jet affichées sont le bilan de tout le lot, pas seulement de la dernière rune.",
    ]
    if uncompensated:
        lines.append(f"⚠ {uncompensated} essai(s) sans assez de poids disponible pour tout compenser.")
    return "\n".join(lines)
