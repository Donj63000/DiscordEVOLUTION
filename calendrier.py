import calendar
import io
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

def wrap_text(tx, mx=25):
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

def gen_cal(data_events, bg, annee, mois):
    evs = []
    for _, e in data_events.items():
        if e.cancelled:
            continue
        if e.date_obj.year == annee and e.date_obj.month == mois:
            hs = e.date_obj.strftime("%Hh")
            tx = e.titre if e.titre else "SansTitre"
            line = "• " + hs + " " + tx
            line = wrap_text(line, LIMITE_WRAP_TEXTE)
            evs.append((e.date_obj.day, line))

    dct = {}
    for jour, txt in evs:
        dct.setdefault(jour, []).append(txt)

    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(annee, mois)

    table_txt = []
    for row in weeks:
        row_cells = []
        for d in row:
            if d == 0:
                row_cells.append("")
            else:
                tcell = str(d)
                if d in dct:
                    for event_line in dct[d]:
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
        colLabels=labs,
        loc='center',
        cellLoc='center',
        zorder=1,
        bbox=BOITE_TABLEAU
    )
    table.auto_set_font_size(False)
    table.set_fontsize(TAILLE_POLICE)
    table.scale(ECHELLE_CELLULE_X, ECHELLE_CELLULE_Y)
    for _, cell in table.get_celld().items():
        cell.set_facecolor("none")
        cell.set_edgecolor("lightgray")
        cell.set_linewidth(0.5)
        txt = cell.get_text()
        txt.set_clip_on(True)
        txt.set_wrap(True)

    ax.set_title(
        calendar.month_name[mois] + " " + str(annee),
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
