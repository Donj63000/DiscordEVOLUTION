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

# ==================
# Embed de rendu
# ==================
def embed_color_for_align(align: str) -> discord.Color:
    a = ALIGN_CANON.get(strip_accents(align).lower(), align)
    if a == "Bonta":
        return discord.Color.blue()
    if a == "Brâkmar":
        return discord.Color.red()
    return discord.Color.dark_gray()

def class_emoji(name: str) -> str:
    key = strip_accents(name).lower()
    mapping = {
        "feca": "🛡️", "osamodas": "🐾", "enutrof": "💰", "sram": "🗡️", "xelor": "⏳",
        "ecaflip": "🎲", "eniripsa": "✨", "iop": "⚔️", "cra": "🏹", "crâ": "🏹",
        "sadida": "🌿", "sacrieur": "🩸", "pandawa": "🍶",
    }
    return mapping.get(key, "🎮")

def format_stat(sl: StatLine) -> str:
    sign = "+" if sl.bonus >= 0 else ""
    return f"{sl.base} ({sign}{sl.bonus}) = {sl.total}"

def make_profile_embed(member: discord.Member, p: Profile) -> discord.Embed:
    title = f"Profil — {p.player_name}"
    desc = f"Niv. **{p.level}** • {class_emoji(p.classe)} **{p.classe}** • {p.alignement}"
    emb = discord.Embed(
        title=title,
        description=desc,
        color=embed_color_for_align(p.alignement)
    )
    try:
        emb.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    emb.add_field(name="Vitalité", value=format_stat(p.stats["vitalite"]), inline=True)
    emb.add_field(name="Sagesse", value=format_stat(p.stats["sagesse"]), inline=True)
    emb.add_field(name="Initiative", value=f"{p.initiative}", inline=True)

    emb.add_field(name="Force", value=format_stat(p.stats["force"]), inline=True)
    emb.add_field(name="Intelligence", value=format_stat(p.stats["intelligence"]), inline=True)
    emb.add_field(name="PA / PM", value=f"{p.pa} PA • {p.pm} PM", inline=True)

    emb.add_field(name="Chance", value=format_stat(p.stats["chance"]), inline=True)
    emb.add_field(name="Agilité", value=format_stat(p.stats["agilite"]), inline=True)
    emb.add_field(name="\u200b", value="\u200b", inline=True)  # équilibre la grille

    emb.set_footer(text=f"Dernière mise à jour : {p.updated_at.replace('T',' ')[:19]}")
    return emb

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
            return await ctx.reply(embed=make_profile_embed(member, prof))

        # pas de nom: afficher le profil de l'appelant
        prof = await self.store.get_by_owner(guild_id, ctx.author.id)
        if not prof:
            return await ctx.reply("Tu n'as pas encore de profil. Utilise `!profil set` pour le créer.")
        member = ctx.guild.get_member(prof.owner_id) or ctx.author
        await ctx.reply(embed=make_profile_embed(member, prof))

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

            def not_cancel(msg: discord.Message) -> bool:
                return msg.content.strip().lower() != "annuler"

            # Nom
            name = await self._ask(
                self.bot,
                chan,
                owner_id,
                f"Nom du personnage ? (par ex. `Coca-Cola`)\n*(Entrée pour garder: `{slugify(suggested_name)}`)*",
                not_cancel,
            )
            if name == "":
                name = suggested_name
            if strip_accents(name).lower() == "annuler":
                return await chan.send("Annulé.")

            # Niveau
            async def _check_level(msg: discord.Message) -> bool:
                return msg.content.isdigit() and 1 <= int(msg.content) <= 200

            level_txt = await self._ask(self.bot, chan, owner_id, "Niveau ? (1..200)", _check_level)
            level = int(level_txt)

            # Classe (alias acceptés)
            async def _check_class(msg: discord.Message) -> bool:
                try:
                    self.canon_classe(msg.content)
                    return True
                except Exception:
                    return False

            classe_in = await self._ask(
                self.bot,
                chan,
                owner_id,
                "Classe ? (ex: Iop, Cra, Eniripsa...)\n*Valides (alias acceptés) : Iop, Crâ/Cra, Eniripsa/Eni, Enutrof/Enu, Feca/Féca, Osamodas/Osa, Pandawa/Panda, Sacrieur/Sacri, Sadida/Sadi, Sram, Xelor/Xel, Ecaflip/Eca*",
                _check_class,
            )
            classe = self.canon_classe(classe_in)

            # Alignement
            async def _check_align(msg: discord.Message) -> bool:
                try:
                    self.canon_align(msg.content)
                    return True
                except Exception:
                    return False

            align_in = await self._ask(self.bot, chan, owner_id, "Alignement ? (Neutre, Bonta, Brâkmar)", _check_align)
            alignement = self.canon_align(align_in)

            # %stats%
            async def _check_stats(msg: discord.Message) -> bool:
                try:
                    parse_stats_block(msg.content)
                    return True
                except Exception:
                    return False

            stats_text = await self._ask(self.bot, chan, owner_id, "Colle le texte `%stats%` complet du jeu :", _check_stats)
            try:
                stats_map, initiative, pa, pm = parse_stats_block(stats_text)
            except StatsParseError as e:
                return await chan.send(f"Erreur de parsing: {e}")

            now = datetime.now(timezone.utc).isoformat()
            prof = Profile(
                guild_id=guild_id,
                owner_id=owner_id,
                player_name=name.strip(),
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
            await ctx.reply(embed=make_profile_embed(member, prof))
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
        await ctx.reply("✅ Stats importées depuis le message cité.", embed=make_profile_embed(member, existing))

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

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilCog(bot))
