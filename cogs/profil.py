# cogs/profil.py
from __future__ import annotations
import asyncio
import json
import os
import re
import unicodedata
import io
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands
from discord.utils import get

# ============
# Configuration
# ============
STAFF_ROLE_NAME = "Staff"
PROFILE_JSON_PATH = os.getenv("PROFILE_JSON_PATH", "data/profiles.json")

# ============
# Configuration additionnelle
# ============
CHANNEL_CONSOLE_NAME = os.getenv("CHANNEL_CONSOLE", "console")
PROFILES_FILENAME = os.getenv("PROFILES_FILENAME", "profiles_data.json")
PROFILES_MARKER = os.getenv("PROFILES_MARKER", "===BOTPROFILES===")
HISTORY_SCAN_LIMIT = int(os.getenv("CONSOLE_HISTORY_LIMIT", "300"))

# ============
# Alias de classes ÉTENDUS (corrige l'erreur "Enu")
# ============
CLASSES_CANON = {
    # canons + alias courts
    "feca": "Feca", "féca": "Feca",
    "osamodas": "Osamodas", "osa": "Osamodas",
    "enutrof": "Enutrof", "enu": "Enutrof",
    "sram": "Sram",
    "xelor": "Xelor", "xel": "Xelor",
    "ecaflip": "Ecaflip", "eca": "Ecaflip",
    "eniripsa": "Eniripsa", "eni": "Eniripsa",
    "iop": "Iop",
    "cra": "Crâ", "crâ": "Crâ",
    "sadida": "Sadida", "sadi": "Sadida",
    "sacrieur": "Sacrieur", "sacri": "Sacrieur",
    "pandawa": "Pandawa", "panda": "Pandawa",
}
ALIGN_CANON = {
    "neutre": "Neutre",
    "bonta": "Bonta",
    "bonte": "Bonta",   # tolérance faute
    "brakmar": "Brâkmar", "brâkmar": "Brâkmar", "brak": "Brâkmar",
}

STAT_KEYS = ["vitalite", "sagesse", "force", "intelligence", "chance", "agilite"]

# ==================
# Helpers de normalisation
# ==================
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def slugify(name: str) -> str:
    s = strip_accents(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

def parse_int(s: str) -> int:
    return int(re.sub(r"\s+", "", s))

# ============
# Modèle
# ============
@dataclass
class StatLine:
    base: int
    bonus: int

    @property
    def total(self) -> int:
        return self.base + self.bonus

@dataclass
class Profile:
    guild_id: int
    owner_id: int
    player_name: str
    player_slug: str
    level: int
    classe: str
    alignement: str
    stats: Dict[str, StatLine]
    initiative: int
    pa: int
    pm: int
    created_at: str
    updated_at: str

    def to_json(self) -> dict:
        d = asdict(self)
        # dataclasses dans dict -> convertir StatLine
        d["stats"] = {k: {"base": v.base, "bonus": v.bonus, "total": v.total} for k, v in self.stats.items()}
        return d

    @staticmethod
    def from_json(data: dict) -> "Profile":
        stats = {k: StatLine(v.get("base", 0), v.get("bonus", 0)) for k, v in data["stats"].items()}
        return Profile(
            guild_id=data["guild_id"],
            owner_id=data["owner_id"],
            player_name=data["player_name"],
            player_slug=data["player_slug"],
            level=data["level"],
            classe=data["classe"],
            alignement=data["alignement"],
            stats=stats,
            initiative=data["initiative"],
            pa=data["pa"],
            pm=data["pm"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

# ==================
# Store JSON (simple et fiable)
# ==================
class JsonFileProfileStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"profiles": []}, f, ensure_ascii=False, indent=2)

    async def _load(self) -> dict:
        async with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)

    async def _save(self, data: dict) -> None:
        async with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    async def upsert(self, profile: Profile) -> None:
        data = await self._load()
        profiles = data.get("profiles", [])
        key = (profile.guild_id, profile.player_slug)
        found = False
        for i, p in enumerate(profiles):
            if (p["guild_id"], p["player_slug"]) == key:
                profiles[i] = profile.to_json()
                found = True
                break
        if not found:
            profiles.append(profile.to_json())
        data["profiles"] = profiles
        await self._save(data)

    async def delete(self, guild_id: int, player_slug: str) -> bool:
        data = await self._load()
        before = len(data.get("profiles", []))
        data["profiles"] = [p for p in data.get("profiles", []) if not (p["guild_id"] == guild_id and p["player_slug"] == player_slug)]
        await self._save(data)
        return len(data["profiles"]) < before

    async def get_by_slug(self, guild_id: int, slug: str) -> Optional[Profile]:
        data = await self._load()
        for p in data.get("profiles", []):
            if p["guild_id"] == guild_id and p["player_slug"] == slug:
                return Profile.from_json(p)
        return None

    async def get_by_owner(self, guild_id: int, owner_id: int) -> Optional[Profile]:
        data = await self._load()
        for p in data.get("profiles", []):
            if p["guild_id"] == guild_id and p["owner_id"] == owner_id:
                return Profile.from_json(p)
        return None

    async def search(self, guild_id: int, query: str, limit: int = 5) -> list[Profile]:
        data = await self._load()
        q = slugify(query)
        out = []
        for p in data.get("profiles", []):
            if p["guild_id"] == guild_id and q in p["player_slug"]:
                out.append(Profile.from_json(p))
                if len(out) >= limit:
                    break
        return out

# ============
# Store via #console
# ============
class ConsoleProfileStore:
    """
    Persistance auditable dans le salon #console via un fichier JSON attaché.
    Format:
    {
      "profiles": [
         { ... Profile.to_json() ... },
         ...
      ],
      "updated_at": "2025-09-05T00:12:34Z"
    }
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()

    async def _get_console_channel(self, guild_id: int) -> discord.TextChannel:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise RuntimeError(f"Guild {guild_id} introuvable.")
        ch = get(guild.text_channels, name=CHANNEL_CONSOLE_NAME)
        if not ch:
            raise RuntimeError(f"Salon #{CHANNEL_CONSOLE_NAME} introuvable dans {guild.name}.")
        return ch

    async def _load_blob(self, guild_id: int) -> dict:
        ch = await self._get_console_channel(guild_id)
        async for msg in ch.history(limit=HISTORY_SCAN_LIMIT, oldest_first=False):
            # priorité: pièce jointe du bon nom
            for att in msg.attachments:
                if att.filename == PROFILES_FILENAME:
                    data = await att.read()
                    try:
                        return json.loads(data.decode("utf-8"))
                    except Exception:
                        continue
            # fallback: message marqué + codeblock JSON
            if PROFILES_MARKER in (msg.content or "") and msg.attachments:
                for att in msg.attachments:
                    if att.filename.endswith(".json"):
                        data = await att.read()
                        try:
                            return json.loads(data.decode("utf-8"))
                        except Exception:
                            continue
        # pas trouvé -> structure vide
        return {"profiles": [], "updated_at": datetime.now(timezone.utc).isoformat()}

    async def _save_blob(self, guild_id: int, blob: dict) -> None:
        ch = await self._get_console_channel(guild_id)
        blob["updated_at"] = datetime.now(timezone.utc).isoformat()
        b = json.dumps(blob, ensure_ascii=False, indent=2).encode("utf-8")
        file = discord.File(io.BytesIO(b), filename=PROFILES_FILENAME)
        await ch.send(content=f"{PROFILES_MARKER} (fichier)", file=file)

    async def upsert(self, profile: Profile) -> None:
        async with self._lock:
            blob = await self._load_blob(profile.guild_id)
            profiles = blob.get("profiles", [])
            key = (profile.guild_id, profile.player_slug)
            found = False
            for i, p in enumerate(profiles):
                if (p["guild_id"], p["player_slug"]) == key:
                    profiles[i] = profile.to_json()
                    found = True
                    break
            if not found:
                profiles.append(profile.to_json())
            blob["profiles"] = profiles
            await self._save_blob(profile.guild_id, blob)

    async def delete(self, guild_id: int, player_slug: str) -> bool:
        async with self._lock:
            blob = await self._load_blob(guild_id)
            before = len(blob.get("profiles", []))
            blob["profiles"] = [p for p in blob.get("profiles", []) if not (p["guild_id"] == guild_id and p["player_slug"] == player_slug)]
            await self._save_blob(guild_id, blob)
            return len(blob["profiles"]) < before

    async def get_by_slug(self, guild_id: int, slug: str) -> Optional[Profile]:
        blob = await self._load_blob(guild_id)
        for p in blob.get("profiles", []):
            if p["guild_id"] == guild_id and p["player_slug"] == slug:
                return Profile.from_json(p)
        return None

    async def get_by_owner(self, guild_id: int, owner_id: int) -> Optional[Profile]:
        blob = await self._load_blob(guild_id)
        for p in blob.get("profiles", []):
            if p["guild_id"] == guild_id and p["owner_id"] == owner_id:
                return Profile.from_json(p)
        return None

    async def search(self, guild_id: int, query: str, limit: int = 5) -> list[Profile]:
        q = slugify(query)
        blob = await self._load_blob(guild_id)
        out = []
        for p in blob.get("profiles", []):
            if p["guild_id"] == guild_id and q in p["player_slug"]:
                out.append(Profile.from_json(p))
                if len(out) >= limit:
                    break
        return out

# ==================
# Parseur %stats%
# ==================
class StatsParseError(ValueError):
    pass

def parse_stats_block(text: str) -> Tuple[Dict[str, StatLine], int, int, int]:
    """
    Retourne (stats:dict, initiative:int, pa:int, pm:int)
    stats attend les 6 clés: vitalite, sagesse, force, intelligence, chance, agilite
    """
    original = text
    # Normaliser: enlever accents, lower, compacter espaces
    t = strip_accents(text).lower()
    t = re.sub(r"[–—-]", "-", t)  # tirets variés
    # tolérer virgules / points-virgules
    # Patterns pour caracs avec (base) (+bonus) ou base seul
    stat_patterns = {
        "vitalite": r"vitalite\s*(?P<base>\d[\d\s]*)\s*(?:\(\s*\+?(?P<bonus>[+-]?\d[\d\s]*)\s*\))?",
        "sagesse": r"sagesse\s*(?P<base>\d[\d\s]*)\s*(?:\(\s*\+?(?P<bonus>[+-]?\d[\d\s]*)\s*\))?",
        "force": r"force\s*(?P<base>\d[\d\s]*)\s*(?:\(\s*\+?(?P<bonus>[+-]?\d[\d\s]*)\s*\))?",
        "intelligence": r"intelligence\s*(?P<base>\d[\d\s]*)\s*(?:\(\s*\+?(?P<bonus>[+-]?\d[\d\s]*)\s*\))?",
        "chance": r"chance\s*(?P<base>\d[\d\s]*)\s*(?:\(\s*\+?(?P<bonus>[+-]?\d[\d\s]*)\s*\))?",
        "agilite": r"agilite\s*(?P<base>\d[\d\s]*)\s*(?:\(\s*\+?(?P<bonus>[+-]?\d[\d\s]*)\s*\))?",
    }

    extracted: Dict[str, StatLine] = {}
    missing = []

    for key, pat in stat_patterns.items():
        m = re.search(pat, t)
        if not m:
            missing.append(key)
            continue
        base = parse_int(m.group("base"))
        bonus_raw = m.groupdict().get("bonus")
        bonus = parse_int(bonus_raw) if bonus_raw else 0
        extracted[key] = StatLine(base=base, bonus=bonus)

    if missing:
        raise StatsParseError(f"Champs manquants dans %stats%: {', '.join(missing)}.\nTexte analysé: {original}")

    # Initiative
    m_init = re.search(r"\binitiative\s*(\d[\d\s]*)", t)
    if not m_init:
        raise StatsParseError("Initiative introuvable dans le texte.")
    initiative = parse_int(m_init.group(1))

    # PA / PM
    m_pa = re.search(r"\bpa\s*(\d+)", t)
    m_pm = re.search(r"\bpm\s*(\d+)", t)
    if not (m_pa and m_pm):
        raise StatsParseError("PA ou PM manquant(s) dans le texte.")

    pa = int(m_pa.group(1))
    pm = int(m_pm.group(1))

    return extracted, initiative, pa, pm

# ─────────────────────────────────────────────────────────────────────────────
# Rendu texte avancé (sans aucun binaire)
# ─────────────────────────────────────────────────────────────────────────────
import os

THIN_NBSP = "\u202F"  # espace fine insécable (1 400)
USE_ANSI = os.getenv("PROFILE_ANSI", "0") == "1"
BAR_WIDTH = max(10, min(30, int(os.getenv("PROFILE_BAR_WIDTH", "18"))))  # borne 10..30
COMPACT = os.getenv("PROFILE_COMPACT", "0") == "1"

# Emojis "sémantiques"
CLASS_EMOJI = {
    "Iop": "⚔️", "Feca": "🛡️", "Enutrof": "🪙", "Eniripsa": "✨",
    "Sram": "🗡️", "Xelor": "⏳", "Ecaflip": "🎲", "Osamodas": "🐾",
    "Pandawa": "🍶", "Sacrieur": "🩸", "Sadida": "🌿", "Crâ": "🏹",
}
ALIGN_EMOJI = {"Neutre": "⚪", "Bonta": "🔵", "Brâkmar": "🔴"}

# Palette d’embed par classe (hex)
CLASS_COLOR = {
    "Iop": 0xE74C3C, "Feca": 0x1ABC9C, "Enutrof": 0xF1C40F, "Eniripsa": 0xE91E63,
    "Sram": 0x95A5A6, "Xelor": 0x9B59B6, "Ecaflip": 0xD35400, "Osamodas": 0x2ECC71,
    "Pandawa": 0x34495E, "Sacrieur": 0xC0392B, "Sadida": 0x27AE60, "Crâ": 0x3498DB,
}

def fmt_int_fr(n: int) -> str:
    """Format 1400 -> '1 400' (thin nbsp)."""
    return f"{n:,}".replace(",", THIN_NBSP)

def _ansi(code: int, s: str) -> str:
    return f"\u001b[{code}m{s}\u001b[0m"

def _lbl(label: str) -> str:
    """Largeur label : 10/12 selon COMPACT."""
    w = 10 if COMPACT else 12
    return f"{label:<{w}}"

def make_bar(base: int, bonus: int, max_total: int, width: int | None = None) -> str:
    """Mini-barre Unicode (base = █, bonus = ▒, vide = ░)."""
    width = width or BAR_WIDTH
    total = max(0, base + bonus)
    max_total = max(1, max_total)
    base_w = int(round(width * max(0, base) / max_total))
    tot_w  = int(round(width * total / max_total))
    bonus_w = max(0, tot_w - base_w)
    empty_w = max(0, width - base_w - bonus_w)
    return "█" * base_w + "▒" * bonus_w + "░" * empty_w

def fmt_stat_row_plain(label: str, base: int, bonus: int, total: int, bar: str) -> str:
    # largeur des colonnes compacte / normale
    bW = 3 if COMPACT else 4   # base width
    sW = 4 if COMPACT else 5   # signed bonus width
    tW = 4 if COMPACT else 5   # total width
    return f"{_lbl(label)} {base:>{bW}} ({bonus:+{sW}}) = {total:>{tW}}   {bar}"

def fmt_stat_row_ansi(label: str, base: int, bonus: int, total: int, bar: str) -> str:
    # ANSI facultatif (label gris 37, base blanc 97, bonus vert/rouge 32/31, total cyan 36, barre gris 90)
    bW = 3 if COMPACT else 4
    sW = 4 if COMPACT else 5
    tW = 4 if COMPACT else 5
    bonus_str = _ansi(32 if bonus >= 0 else 31, f"{bonus:+{sW}}")
    return (
        _ansi(37, _lbl(label)) + " " +
        _ansi(97, f"{base:>{bW}}") + " (" + bonus_str + ") = " +
        _ansi(36, f"{total:>{tW}}") + "   " + _ansi(90, bar)
    )

def header_line(p) -> str:
    """Ex: ⭐200 • 🪙 Enutrof • 🔵 Bonta"""
    badge = "⭐200" if p.level == 200 else f"Niv. {p.level}"
    cls   = f"{CLASS_EMOJI.get(p.classe,'🎮')} {p.classe}"
    al    = f"{ALIGN_EMOJI.get(p.alignement,'⚪')} {p.alignement}"
    return f"**{badge}** • {cls} • {al}"

def color_for_profile(p) -> int:
    if p.classe in CLASS_COLOR: return CLASS_COLOR[p.classe]
    if p.alignement == "Bonta": return 0x4885ED
    if p.alignement == "Brâkmar": return 0xEA4335
    return 0x777777  # Neutre

def _field_guard(text: str, limit: int = 1024) -> str:
    """Garantit que la valeur de field ne dépasse pas la limite Discord (coupe proprement si besoin)."""
    if len(text) <= limit:
        return text
    # On coupe par lignes pour éviter de casser la mise en forme
    lines = text.splitlines()
    out = []
    curr = 0
    for ln in lines:
        if curr + len(ln) + 1 > limit - 1:  # garde 1 pour '\n' final optionnel
            break
        out.append(ln); curr += len(ln) + 1
    # Indique la coupe
    if out and not out[-1].endswith("…"):
        out[-1] = out[-1].rstrip() + " …"
    return "\n".join(out)

BAR_MODE = os.getenv("PROFILE_BAR_MODE", "local").lower()
BAR_FIXED_MAX = int(os.getenv("PROFILE_BAR_FIXED_MAX", "2000"))

async def compute_bar_scale(cog: "ProfilCog", guild_id: int, p: Profile) -> tuple[int, str]:
    """
    Calcule l'échelle des barres (max_total) + une légende lisible.
    - local : max des 6 caracs du profil courant
    - guild : max observé sur toute la guilde (toutes fiches, toutes caracs)
    - fixed : valeur fixe PROFILE_BAR_FIXED_MAX
    Retour: (scale_max, caption)
    """
    mode = BAR_MODE
    try:
        if mode == "guild":
            # On scanne la "base" #console pour récupérer le dernier snapshot
            if hasattr(cog.store, "_load_blob"):
                blob = await cog.store._load_blob(guild_id)  # ConsoleProfileStore
                maxi = 1
                for row in blob.get("profiles", []):
                    if row.get("guild_id") != guild_id:
                        continue
                    stats = row.get("stats", {})
                    for k in ("vitalite","sagesse","force","intelligence","chance","agilite"):
                        s = stats.get(k, {})
                        total = int(s.get("total", int(s.get("base",0))+int(s.get("bonus",0))))
                        if total > maxi:
                            maxi = total
                return maxi, f"Échelle: max guilde {maxi}"
            # si pas de _load_blob, fallback local
            mode = "local"

        if mode == "fixed":
            m = max(1, BAR_FIXED_MAX)
            return m, f"Échelle: fixe {m}"

        # défaut: local
        m = max((sl.total for sl in p.stats.values()), default=1)
        return m, f"Échelle: ta carac la plus haute ({m})"
    except Exception:
        # En cas de souci, on ne casse pas l’affichage
        m = max((sl.total for sl in p.stats.values()), default=1)
        return m, f"Échelle: ta carac la plus haute ({m})"

def make_profile_embed(member: discord.Member, p: Profile, *, scale_max: int, scale_caption: str) -> discord.Embed:
    title = f"Profil — {p.player_name}"
    sub   = header_line(p)

    emb = discord.Embed(title=title, description=sub, color=color_for_profile(p))
    try:
        emb.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    stats = p.stats or {}
    # ⟵ ÉCHELLE: on utilise scale_max calculé (local/guild/fixed)
    max_total = max(1, scale_max)

    row_fn = fmt_stat_row_ansi if USE_ANSI else fmt_stat_row_plain

    left_rows = []
    for key, label in [("vitalite","Vitalité"), ("sagesse","Sagesse"), ("force","Force")]:
        sl = stats[key]; bar = make_bar(sl.base, sl.bonus, max_total)
        left_rows.append(row_fn(label, sl.base, sl.bonus, sl.total, bar))

    right_rows = []
    for key, label in [("intelligence","Intelligence"), ("chance","Chance"), ("agilite","Agilité")]:
        sl = stats[key]; bar = make_bar(sl.base, sl.bonus, max_total)
        right_rows.append(row_fn(label, sl.base, sl.bonus, sl.total, bar))

    block_tag = "ansi" if USE_ANSI else "text"
    left_block  = f"```{block_tag}\n" + "\n".join(left_rows)  + "\n```"
    right_block = f"```{block_tag}\n" + "\n".join(right_rows) + "\n```"

    emb.add_field(name="Caractéristiques (1/2)", value=_field_guard(left_block), inline=True)
    emb.add_field(name="Caractéristiques (2/2)", value=_field_guard(right_block), inline=True)

    meta = f"**Initiative** {fmt_int_fr(p.initiative)}  •  **PA / PM** {p.pa}{THIN_NBSP}/ {p.pm}"
    emb.add_field(name="\u200b", value=meta, inline=False)

    # ⟵ LÉGENDE: on explique visuellement la signification + l’échelle
    legend = f"Barres : **█ base** • **▒ bonus** • **░** jusqu’à l’échelle. {scale_caption}."
    emb.add_field(name="\u200b", value=legend, inline=False)

    emb.set_footer(text=f"Dernière mise à jour : {p.updated_at.replace('T',' ')[:19]}")
    return emb

class ProfilActionsView(discord.ui.View):
    def __init__(self, cog: "ProfilCog", prof: Profile):
        super().__init__(timeout=120)
        self.cog = cog
        self.prof = prof

    @discord.ui.button(label="Mettre à jour %stats%", style=discord.ButtonStyle.primary)
    async def btn_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Réponds au message avec ta ligne `%stats%` puis tape `!profil stats` **en réponse**. "
            "Ou simplement `!profil stats` et colle ton %stats%.",
            ephemeral=True
        )

    @discord.ui.button(label="Lister mes persos", style=discord.ButtonStyle.secondary)
    async def btn_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Utilise `!profil list` pour voir tous tes personnages.", ephemeral=True)

    @discord.ui.button(label="Définir comme principal", style=discord.ButtonStyle.success)
    async def btn_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"Utilise `!profil main {self.prof.player_slug}` pour définir **{self.prof.player_name}** comme principal.",
            ephemeral=True
        )

# ==================
# Exceptions et boucle de questions
# ==================

class WizardCancelled(Exception):
    """Le joueur a tapé 'annuler' ou a dépassé le nombre d'essais/timeout."""
    pass


async def wait_user_message(bot: commands.Bot, chan: discord.abc.Messageable, author_id: int, timeout: int = 180) -> discord.Message:
    """Attend un message de l'utilisateur dans le même channel (DM ou non)."""

    def _check(m: discord.Message) -> bool:
        return (m.author.id == author_id) and (m.channel.id == getattr(chan, "id", None))

    return await bot.wait_for("message", check=_check, timeout=timeout)


async def ask_loop(
    bot: commands.Bot,
    chan: discord.abc.Messageable,
    author_id: int,
    prompt: str,
    validator,
    help_text: str = "",
    example: str = "",
    retries: int = 5,
    timeout: int = 180,
):
    """
    Envoie prompt, boucle jusqu'à réponse valide ou annulation/timeout.
    - validator(text) -> (ok, value, err_msg)
    - help_text/exemple affichés seulement en cas d'erreur.
    - 'annuler' à tout moment stoppe proprement.
    """

    await chan.send(prompt)
    tries = 0
    while True:
        try:
            msg = await wait_user_message(bot, chan, author_id, timeout=timeout)
        except asyncio.TimeoutError:
            await chan.send("⏰ Temps écoulé. Reprends quand tu veux avec `!profil set`.")
            raise WizardCancelled()

        content = msg.content.strip()
        if content.lower() == "annuler":
            await chan.send("❎ D’accord, j’annule. Tu pourras reprendre avec `!profil set`.")
            raise WizardCancelled()

        ok, value, err = validator(content)
        if ok:
            return value

        tries += 1
        hint = (("\nℹ️ " + help_text) if help_text else "") + (("\nExemple : `" + example + "`") if example else "")
        await chan.send(f"❌ {err}{hint}\nRéessaie :")
        if retries and tries >= retries:
            await chan.send("❎ Trop d’essais. On arrête ici — relance `!profil set` quand tu veux.")
            raise WizardCancelled()

# ==================
# Cog
# ==================
class ProfilCog(commands.Cog):
    """Gestion des profils joueurs: création, consultation, édition, suppression."""
    WIZARD_EVT_START = "wizard_started"
    WIZARD_EVT_END = "wizard_ended"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # store #console (au lieu du JSON local)
        self.store = ConsoleProfileStore(bot)

    # ---- utilitaires de validation ----
    def canon_classe(self, s: str) -> str:
        key = strip_accents(s).lower().strip()
        if key in CLASSES_CANON:
            return CLASSES_CANON[key]
        raise ValueError(f"Classe inconnue: '{s}'. Classes valides: {', '.join(sorted(set(CLASSES_CANON.values())))}")

    def canon_align(self, s: str) -> str:
        key = strip_accents(s).lower().strip()
        if key in ALIGN_CANON:
            return ALIGN_CANON[key]
        raise ValueError(f"Alignement inconnu: '{s}'. Alignements valides: Neutre, Bonta, Brâkmar")

    async def _open_dm(self, ctx: commands.Context) -> Tuple[discord.abc.Messageable, bool]:
        try:
            dm = await ctx.author.create_dm()
            # prévenir le cog IA que ce user est en wizard -> pas d'IA en DM
            self.bot.dispatch(self.WIZARD_EVT_START, ctx.author.id)
            await dm.send(
                f"👋 Bonjour {ctx.author.display_name} ! On va créer/mettre à jour ton profil. "
                f"(Si tu préfères, ferme ici et relance `!profil set` dans un salon privé.)\n"
                f"On va remplir: **Nom**, **Niveau**, **Classe**, **Alignement**, **%stats%** (coller le texte). "
                f"Tu peux annuler à tout moment avec `annuler`."
            )
            return dm, True
        except discord.Forbidden:
            await ctx.reply("Je ne peux pas t'envoyer de DM. On continue ici (ton texte `%stats%` sera visible dans ce salon).")
            return ctx.channel, False

    async def _ask(self, bot: commands.Bot, chan: discord.abc.Messageable, author_id: int, question: str, check=None, timeout: int = 180) -> str:
        await chan.send(question)
        def _check(msg: discord.Message) -> bool:
            if msg.author.id != author_id: return False
            if check is not None: return check(msg)
            return True
        msg = await bot.wait_for("message", check=_check, timeout=timeout)
        return msg.content.strip()

    # ---- GROUPE !profil ----
    @commands.group(name="profil", invoke_without_command=True)
    @commands.guild_only()
    async def profil(self, ctx: commands.Context, *, maybe_name: Optional[str] = None):
        """Sans sous-commande: affiche ton profil; avec nom entre guillemets, affiche ce joueur."""
        guild_id = ctx.guild.id
        # Si un nom est fourni, chercher par slug
        if maybe_name:
            slug = slugify(maybe_name)
            prof = await self.store.get_by_slug(guild_id, slug)
            if not prof:
                # tentative: si mention d'un membre?
                if ctx.message.mentions:
                    target = ctx.message.mentions[0]
                    prof = await self.store.get_by_owner(guild_id, target.id)
                if not prof:
                    return await ctx.reply(f"Aucun profil trouvé pour **{maybe_name}**.")
            member = ctx.guild.get_member(prof.owner_id) or ctx.author
            scale_max, scale_caption = await compute_bar_scale(self, ctx.guild.id, prof)
            emb = make_profile_embed(member, prof, scale_max=scale_max, scale_caption=scale_caption)
            try:
                view = ProfilActionsView(self, prof)
                return await ctx.reply(embed=emb, view=view)
            except Exception:
                return await ctx.reply(embed=emb)

        # pas de nom: afficher le profil de l'appelant
        prof = await self.store.get_by_owner(guild_id, ctx.author.id)
        if not prof:
            return await ctx.reply("Tu n'as pas encore de profil. Utilise `!profil set` pour le créer.")
        member = ctx.guild.get_member(prof.owner_id) or ctx.author
        scale_max, scale_caption = await compute_bar_scale(self, ctx.guild.id, prof)
        emb = make_profile_embed(member, prof, scale_max=scale_max, scale_caption=scale_caption)
        try:
            view = ProfilActionsView(self, prof)
            await ctx.reply(embed=emb, view=view)
        except Exception:
            await ctx.reply(embed=emb)

    # ---- Création / mise à jour (dialogue guidé) ----
    @profil.command(name="set")
    @commands.guild_only()
    async def profil_set(self, ctx: commands.Context):
        """Crée ou met à jour ton profil via questions guidées (DM si possible)."""
        chan, is_dm = await self._open_dm(ctx)
        guild_id = ctx.guild.id
        owner_id = ctx.author.id
        try:
            existing = await self.store.get_by_owner(guild_id, owner_id)
            suggested_name = existing.player_name if existing else (ctx.author.nick or ctx.author.name)
            await chan.send(
                "On va remplir : **Nom**, **Niveau**, **Classe**, **Alignement**, **%stats%** (coller le texte).\n"
                "Tu peux annuler à tout moment avec `annuler`."
            )

            # --- VALIDATORS ---
            def val_name(text: str):
                name = text.strip() or suggested_name
                if len(name) < 2 or len(name) > 32:
                    return False, None, "Le nom doit faire entre 2 et 32 caractères."
                return True, name, ""

            def val_level(text: str):
                if text.isdigit():
                    n = int(text)
                    if 1 <= n <= 200:
                        return True, n, ""
                return False, None, "Le niveau doit être un entier entre 1 et 200."

            def val_classe(text: str):
                try:
                    return True, self.canon_classe(text), ""
                except Exception as e:
                    return False, None, str(e)

            def val_align(text: str):
                try:
                    return True, self.canon_align(text), ""
                except Exception as e:
                    return False, None, str(e)

            def val_stats(text: str):
                try:
                    stats_map, initiative, pa, pm = parse_stats_block(text)
                    return True, (stats_map, initiative, pa, pm), ""
                except StatsParseError as e:
                    return False, None, f"Format %stats% invalide : {e}"

            # --- QUESTIONS AVEC REPROMPT ---
            name = await ask_loop(
                self.bot, chan, owner_id,
                prompt=f"**Nom du personnage ?** (par ex. `Coca-Cola`)\n*(Entrée pour garder : `{suggested_name}`)*",
                validator=val_name,
                help_text="Le nom sera utilisé pour la recherche (`!profil <nom>`).",
                example="Coca-Cola"
            )
            level = await ask_loop(
                self.bot, chan, owner_id,
                prompt="**Niveau ?** (1..200)",
                validator=val_level,
                help_text="Le niveau doit être un nombre entier.",
                example="200"
            )
            classe = await ask_loop(
                self.bot, chan, owner_id,
                prompt=("**Classe ?** (ex : Iop, Cra, Eniripsa...)\n"
                        "*Alias acceptés : eni/enu/osa/panda/sadi/sacri/eca/xel/cra/féca/crâ ...*"),
                validator=val_classe,
                example="Enu"
            )
            alignement = await ask_loop(
                self.bot, chan, owner_id,
                prompt="**Alignement ?** (Neutre, Bonta, Brâkmar)",
                validator=val_align,
                example="Bonta"
            )
            (stats_map, initiative, pa, pm) = await ask_loop(
                self.bot, chan, owner_id,
                prompt="**Colle le texte `%stats%` complet du jeu :**",
                validator=val_stats,
                help_text=("Exemple de ligne attendue depuis Dofus Retro : "
                           "`Coca-Cola : Vitalité 101 (+1762), Sagesse 101 (+249), Force 389 (+135), "
                           "Intelligence 101 (+129), Chance 101 (+335), Agilité 101 (+30) - Initiative 1400, PA 8, PM 4`"),
                example="Vitalité 101 (+1762), ... - Initiative 1400, PA 8, PM 4",
                retries=0
            )

            now = datetime.now(timezone.utc).isoformat()
            prof = Profile(
                guild_id=guild_id,
                owner_id=owner_id,
                player_name=name,
                player_slug=slugify(name),
                level=level,
                classe=classe,
                alignement=alignement,
                stats=stats_map,
                initiative=initiative,
                pa=pa,
                pm=pm,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            await self.store.upsert(prof)
            await chan.send("✅ Profil sauvegardé.")
            member = ctx.guild.get_member(owner_id) or ctx.author
            scale_max, scale_caption = await compute_bar_scale(self, ctx.guild.id, prof)
            emb = make_profile_embed(member, prof, scale_max=scale_max, scale_caption=scale_caption)
            try:
                view = ProfilActionsView(self, prof)
                await chan.send(embed=emb, view=view)
            except Exception:
                await chan.send(embed=emb)
        except WizardCancelled:
            return
        finally:
            # libérer l'IA quoi qu'il arrive
            self.bot.dispatch(self.WIZARD_EVT_END, owner_id)

    @profil.command(name="import")
    @commands.guild_only()
    async def profil_import(self, ctx: commands.Context):
        """Importe un %stats% depuis un message cité ou répondu."""
        if not ctx.message.reference:
            return await ctx.reply("Réponds à un message qui contient la ligne `%stats%` à importer.")
        try:
            src = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except Exception:
            return await ctx.reply("Impossible de lire le message référencé.")
        text = src.content or ""
        try:
            stats_map, initiative, pa, pm = parse_stats_block(text)
        except StatsParseError as e:
            return await ctx.reply(f"Le message cité ne contient pas un %stats% valide : {e}")

        guild_id = ctx.guild.id
        owner_id = ctx.author.id
        existing = await self.store.get_by_owner(guild_id, owner_id)
        if not existing:
            # Minimal: on a besoin de nom/classe/align/level → lancer un mini-wizard (DM) pour les compléter
            await ctx.reply("🧩 J'ai bien lu tes stats. Il me manque Nom / Niveau / Classe / Alignement. Je t'ouvre un DM pour compléter.")
            return await self.profil_set(ctx)

        # Mise à jour des seules stats
        now = datetime.now(timezone.utc).isoformat()
        existing.stats = stats_map
        existing.initiative = initiative
        existing.pa = pa
        existing.pm = pm
        existing.updated_at = now
        await self.store.upsert(existing)
        member = ctx.guild.get_member(owner_id) or ctx.author
        scale_max, scale_caption = await compute_bar_scale(self, ctx.guild.id, existing)
        emb = make_profile_embed(member, existing, scale_max=scale_max, scale_caption=scale_caption)
        try:
            view = ProfilActionsView(self, existing)
            await ctx.reply("✅ Stats importées depuis le message cité.", embed=emb, view=view)
        except Exception:
            await ctx.reply("✅ Stats importées depuis le message cité.", embed=emb)

    # ---- Suppression ----
    @profil.command(name="delete")
    @commands.guild_only()
    async def profil_delete(self, ctx: commands.Context, *, player_name: Optional[str] = None):
        """Supprime ton profil (ou celui d'autrui si Staff, en donnant le nom)."""
        guild_id = ctx.guild.id
        is_staff = any(r.name == STAFF_ROLE_NAME for r in getattr(ctx.author, "roles", []))
        target_slug = None
        if player_name:
            target_slug = slugify(player_name)
        else:
            # sans nom: self-profile
            me_prof = await self.store.get_by_owner(guild_id, ctx.author.id)
            if me_prof:
                target_slug = me_prof.player_slug
            else:
                return await ctx.reply("Tu n'as pas de profil.")

        prof = await self.store.get_by_slug(guild_id, target_slug)
        if not prof:
            return await ctx.reply("Profil introuvable.")

        if prof.owner_id != ctx.author.id and not is_staff:
            return await ctx.reply("Tu ne peux supprimer que ton propre profil (ou être Staff).")

        ok = await self.store.delete(guild_id, target_slug)
        if ok:
            await ctx.reply(f"🗑️ Profil **{prof.player_name}** supprimé.")
        else:
            await ctx.reply("Rien à supprimer.")

    # ---- Recherche simple ----
    @profil.command(name="search")
    @commands.guild_only()
    async def profil_search(self, ctx: commands.Context, *, q: str):
        """Recherche par fragment de nom."""
        res = await self.store.search(ctx.guild.id, q, limit=5)
        if not res:
            return await ctx.reply("Aucun résultat.")
        lines = [f"• **{p.player_name}** (`!profil {p.player_slug}`)" for p in res]
        await ctx.reply("Résultats :\n" + "\n".join(lines))

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        """Filet de sécurité : si une erreur survient sur une commande profil, on répond calmement au bon endroit."""
        if not ctx.command:
            return
        if not ctx.command.qualified_name.startswith("profil"):
            return

        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                f"⚠️ Une erreur est survenue : {error.__class__.__name__}. "
                f"Tu peux relancer `!profil set`. Si ça persiste, ping le Staff."
            )
            return
        except discord.Forbidden:
            pass
        await ctx.reply(
            f"⚠️ Une erreur est survenue. Essaie `!profil set` à nouveau ou contacte le Staff."
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilCog(bot))
