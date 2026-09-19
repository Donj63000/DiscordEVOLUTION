"""PNG déterministe et texte accessible à partir du même Report."""
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .models import STAT_LABELS, SLOTS
from .editor import SLOT_LABELS

NATURE_LABELS = {"natural": "Jets naturels", "declared": "Profil / jets déclarés — simulation"}
COMPLETENESS_LABELS = {"complete": "Données couvertes", "partial": "Données partielles"}
EQUIPABILITY_LABELS = {"valid": "Contrôles couverts respectés", "invalid": "Non équipable", "unknown": "À vérifier"}
ROLL_LABELS = {"natural_best": "meilleurs jets naturels", "natural_custom": "jets naturels saisis", "declared_fm": "FM déclarée"}

KEY_STATS = ("pa", "pm", "po", "vi", "fo", "ine", "cha", "age", "sa", "pp", "do", "so", "cc", "invo")


def metric_text(report, stat):
    metric = next(m for m in report.metrics if m.stat == stat)
    if metric.status == "unknown":
        return "?"
    return str(metric.value) + (" *" if metric.status != "known" else "")


def text_report(build, report, catalog, durable):
    rows = [f"EVOLUTION BUILD — {build.name}", f"{build.profile.classe.capitalize()} · niveau {build.profile.level} · révision {build.revision}",
            "Sauvegarde dans #console" if durable else "ESSAI EN MÉMOIRE — perdu au redémarrage, exporte le JSON",
            f"Équipabilité : {EQUIPABILITY_LABELS[report.equipability]} · {COMPLETENESS_LABELS[report.completeness]} · {NATURE_LABELS[report.nature]}", ""]
    rows.extend(f"{STAT_LABELS[s]} : {metric_text(report, s)}" for s in STAT_LABELS)
    rows += ["", "ÉQUIPEMENTS"]
    for slot in SLOTS:
        instance = build.in_slot(slot)
        rows.append(f"{SLOT_LABELS[slot]} : " + (f"{catalog.resolve(instance).name} ({ROLL_LABELS[instance.mode]})" if instance else "—"))
    rows += ["", "LIGNES D'EFFETS (références pour /build jets)"]
    for slot_item in build.slots:
        template = catalog.resolve(slot_item.item)
        rows.append(f"{slot_item.slot} — {template.name} — {template.ref}")
        finals = {value.ref: value.value for value in slot_item.item.final_values}
        for effect in template.effects:
            current = f" -> valeur utilisée {finals.get(effect.ref, effect.high)}" if effect.kind == "stat" else ""
            rows.append(f"  {effect.ref}: {effect.kind} {effect.stat or effect.element or '?'} "
                        f"[{effect.low}, {effect.high}]{current} {effect.text}")
        rows.extend(f"  Exo déclaré : {STAT_LABELS[value.stat]} {value.value:+d}" for value in slot_item.item.extras)
    rows += ["", "* = somme partielle, PAS une valeur finale certifiée.", "ERREURS / RÉSERVES"]
    rows.extend(f"[{d.code}] {d.origin} {d.text}" for d in report.errors + report.warnings)
    rows += ["", "CONTRIBUTIONS"]
    rows.extend(f"{c.origin} : {STAT_LABELS[c.stat]} {c.value:+d}" for c in report.contributions if c.value)
    rows += ["", f"Catalogue {build.catalog_id}", f"Règles {build.rules_id}", "Données Xixou : https://xixou.io/ ; identités wiki Moon."]
    return "\n".join(rows)


def render(build, report, catalog, durable, icons=None):
    """Images déjà vérifiées par WikiImageClient. Aucun téléchargement dans ce module."""
    icons = icons or {}
    image = Image.new("RGB", (1200, 1480), "#111c2e")
    draw = ImageDraw.Draw(image)
    def font(size):
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except OSError:
            try:
                return ImageFont.load_default(size=size)
            except TypeError:  # Pillow 10.0 ne propose pas encore size.
                return ImageFont.load_default()
    large, medium, small = font(31), font(21), font(17)
    def fit(text, width, used_font):
        text = str(text).replace("\n", " ")
        while draw.textlength(text, font=used_font) > width and len(text) > 1:
            text = text[:-2]
        return text
    draw.text((40, 32), "EVOLUTION / BUILD", font=medium, fill="#8dbdf8")
    draw.text((40, 70), fit(build.name, 1110, large), font=large, fill="white")
    draw.text((40, 121), f"{build.profile.classe.capitalize()} · Niveau {build.profile.level} · Révision {build.revision}", font=medium, fill="#d6e4f4")
    status = {"valid": "Contrôles couverts respectés", "invalid": "Non équipable : voir les erreurs", "unknown": "Équipabilité à vérifier"}[report.equipability]
    draw.text((40, 164), status, font=medium, fill="#ffcd83")
    draw.text((40, 201), "Conservé dans #console" if durable else "ESSAI NON DURABLE — export JSON conseillé", font=small, fill="#ffcd83")
    for n, stat in enumerate(KEY_STATS):
        col, row = n % 7, n // 7
        x, y = 40 + col * 160, 255 + row * 110
        draw.rounded_rectangle((x, y, x + 145, y + 92), 12, fill="#22334d")
        draw.text((x + 10, y + 10), fit("Critiques +" if stat == "cc" else STAT_LABELS[stat], 127, small), font=small, fill="#b9cce3")
        draw.text((x + 10, y + 40), metric_text(report, stat), font=large, fill="white")
    for n, slot in enumerate(SLOTS):
        col, row = n % 2, n // 2
        x, y = 40 + col * 575, 500 + row * 74
        draw.rounded_rectangle((x, y, x + 555, y + 64), 9, fill="#1c2c43")
        instance = build.in_slot(slot)
        name = "Emplacement vide"
        if instance:
            template = catalog.resolve(instance)
            name = template.name
            raw = icons.get(template.ref)
            if raw and len(raw) <= 1024 * 1024:
                try:
                    with Image.open(BytesIO(raw)) as original:
                        if original.width * original.height <= 4_000_000:
                            icon = ImageOps.contain(original.convert("RGBA"), (50, 50))
                            image.paste(icon, (x + 7, y + 7), icon)
                except (OSError, ValueError, Image.DecompressionBombError):
                    pass
        draw.text((x + 68, y + 6), SLOT_LABELS[slot], font=small, fill="#8dbdf8")
        draw.text((x + 68, y + 30), fit(name, 470, medium), font=medium, fill="white")
    y = 1110
    draw.text((40, y), "* Sommes partielles : données manquantes, ne pas lire comme un total certifié.", font=small, fill="#ffcd83")
    draw.text((40, y + 30), f"{len(report.errors)} erreur(s), {len(report.warnings)} réserve(s) — détails dans le rapport texte.", font=small, fill="#ffcd83")
    for i, d in enumerate((report.errors + report.warnings)[:4]):
        draw.text((40, y + 65 + i * 29), fit(d.text, 1110, small), font=small, fill="#d6e4f4")
    draw.text((40, 1330), f"{NATURE_LABELS[report.nature]} · Catalogue {build.catalog_id[:14]} · Règles {build.rules_id[:14]}", font=small, fill="#b9cce3")
    draw.text((40, 1370), "Données : xixou.io · Identités : wiki.moon-bot.io", font=medium, fill="#8dbdf8")
    draw.text((40, 1410), "Simulation hors jeu · Aucune possession, réalisation FM ou optimalité garantie", font=small, fill="#b9cce3")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
