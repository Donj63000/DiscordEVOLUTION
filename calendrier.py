import calendar
import io
from datetime import date
from typing import Dict, Iterable, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

LARGEUR_FIG = 14
HAUTEUR_FIG = 20
BOITE_TABLEAU = [0.05, 0.1, 0.9, 0.7]
ECHELLE_CELLULE_X = 1.2
ECHELLE_CELLULE_Y = 3.0
TAILLE_POLICE = 14
LIMITE_WRAP_TEXTE = 30
ESPACEMENT_TITRE = 5
RESOLUTION_DPI = 120

COULEUR_ENTETE = "#111827"
COULEUR_ENTETE_TEXTE = "#F9FAFB"
COULEUR_JOUR_EVENEMENT = "#DBEAFE"
COULEUR_JOUR_AUJOURDHUI = "#2563EB"

MONTH_NAMES_FR = [
    "",
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
]

def wrap_text(tx: str, mx: int = 25) -> str:
    ls = []
    for ori in tx.split('\n'):
        l = ori.strip()
        while len(l) > mx:
            ci = l.rfind(' ', 0, mx)
            if ci == -1:
                ci = mx
            ls.append(l[:ci])
            l = l[ci:].strip()
        ls.append(l)
    return "\n".join(x for x in ls if x)


def _format_event_lines(events: Iterable) -> Dict[int, list[str]]:
    """Prépare les lignes à afficher pour chaque jour."""

    def _format_hour(dt):
        return dt.strftime("%Hh") if dt.minute == 0 else dt.strftime("%Hh%M")

    grouped: Dict[int, list[str]] = {}
    for event in events:
        titre = event.titre.strip() if getattr(event, "titre", None) else "Sans titre"
        hour = _format_hour(event.date_obj)
        line = f"• {hour} {titre}"
        grouped.setdefault(event.date_obj.day, []).append(wrap_text(line, LIMITE_WRAP_TEXTE))
    return grouped


def gen_cal(data_events, bg, annee: int, mois: int, highlight_date: Optional[date] = None):
    """
    Génère une image PNG représentant un calendrier mensuel.

    Args:
        data_events: mapping d'événements `ActiviteData`.
        bg: image de fond optionnelle (numpy array).
        annee: année ciblée.
        mois: mois ciblé (1-12).
        highlight_date: date à mettre en avant (généralement la date du jour).
    """

    highlight_day = None
    if highlight_date and highlight_date.year == annee and highlight_date.month == mois:
        highlight_day = highlight_date.day

    events_in_month = [
        evt
        for evt in data_events.values()
        if not evt.cancelled and evt.date_obj.year == annee and evt.date_obj.month == mois
    ]
    events_in_month.sort(key=lambda evt: evt.date_obj)

    day_lines = _format_event_lines(events_in_month)

    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(annee, mois)

    labs = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    table_txt = [labs]
    for row in weeks:
        row_cells = []
        for d in row:
            if d == 0:
                row_cells.append("")
            else:
                tcell = str(d)
                if d in day_lines:
                    for event_line in day_lines[d]:
                        tcell += "\n" + event_line
                tcell = wrap_text(tcell, LIMITE_WRAP_TEXTE)
                row_cells.append(tcell)
        table_txt.append(row_cells)

    fig = plt.figure(figsize=(LARGEUR_FIG, HAUTEUR_FIG), facecolor="none")
    ax = fig.add_subplot(111)
    ax.set_axis_off()
    if bg is not None:
        ax.imshow(bg, extent=[0, 1, 0, 1], zorder=0)

    labs = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    table = ax.table(
        cellText=table_txt,
        loc='center',
        cellLoc='center',
        zorder=1,
        bbox=BOITE_TABLEAU
    )
    table.auto_set_font_size(False)
    table.set_fontsize(TAILLE_POLICE)
    table.scale(ECHELLE_CELLULE_X, ECHELLE_CELLULE_Y)
    # Ligne d'entête
    for idx in range(len(labs)):
        header = table[0, idx]
        header.set_facecolor(COULEUR_ENTETE)
        header.set_edgecolor("#1F2937")
        text = header.get_text()
        text.set_color(COULEUR_ENTETE_TEXTE)
        text.set_weight('bold')
        text.set_clip_on(True)
        text.set_wrap(False)

    event_days = set(day_lines)
    for row_idx, week in enumerate(weeks, start=1):
        for col_idx, day in enumerate(week):
            cell = table[row_idx, col_idx]
            cell.set_edgecolor("lightgray")
            cell.set_linewidth(0.5)
            txt = cell.get_text()
            txt.set_clip_on(True)
            txt.set_wrap(True)

            if day == 0:
                cell.set_facecolor("none")
                continue

            if highlight_day and day == highlight_day:
                cell.set_facecolor(COULEUR_JOUR_AUJOURDHUI)
                txt.set_color("#FFFFFF")
            elif day in event_days:
                cell.set_facecolor(COULEUR_JOUR_EVENEMENT)
                txt.set_color("#111827")
            else:
                cell.set_facecolor("none")
                txt.set_color("#111827")

    ax.set_title(
        MONTH_NAMES_FR[mois] + " " + str(annee),
        fontsize=16,
        fontweight='bold',
        pad=ESPACEMENT_TITRE
    )

    plt.tight_layout()
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', dpi=RESOLUTION_DPI, transparent=True)
    buffer.seek(0)
    plt.close(fig)
    return buffer
