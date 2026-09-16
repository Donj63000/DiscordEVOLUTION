"""Sessions ephemeres exportables : aucune donnee de guilde ni secret persiste."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import secrets

from utils.exo_engine import (
    D, Item, LEGACY_PROFILE, PROFILE, Rates, Rune, STATS, State, decimal_value,
    validate_goals, validate_item_jets, weight_text,
)
from utils.exo_math import Budget, integer, probability

EXPORT_LIMIT = 512 * 1024


@dataclass
class Session:
    item: Item
    sim: State
    observed: State
    rune: Rune = field(default_factory=lambda: Rune("pm"))
    goal_stat: str = "pm"
    goal_value: int = 1
    mode: str = "simulation"
    tab: str = "atelier"
    seed: int = field(default_factory=lambda: secrets.randbits(64))
    custom: dict[str, Rates] = field(default_factory=dict)
    prices: dict[str, int] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)
    p: float = .01
    cap: int = 100
    experiments: int = 10000
    confidence: float = .95
    observation_ready: bool = False
    revision: int = 0
    notice: str = ""
    quality: dict[str, int] = field(default_factory=dict)
    journal_page: int = 0
    last_changes: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def create(cls, item: Item, objective: str | None = None) -> Session:
        if objective is None:
            objective = next((key for key in ("pm", "pa", "po", *STATS)
                              if key not in item.bounds), None)
            # Une fiche couvrant toutes les stats n'a pas d'exo absent :
            # proposer un remontage naturel, pas un over lourd impossible.
            if objective is None:
                objective = next((key for key in STATS if item.maximum(key) > 0), "fo")
                goal_value = max(1, item.maximum(objective))
            else:
                goal_value = 1
        else:
            Rune(objective)  # Valide la cle avant de construire les objectifs.
            goal_value = max(1, item.maximum(objective) + 1)
        quality = {key: max(0, low) for key, (low, _) in item.bounds.items()
                   if low > 0 and key != objective}
        validate_goals(item, {objective: goal_value, **quality})
        return cls(
            item, State.initial(item), State.initial(item, tracking=True),
            rune=Rune(objective), goal_stat=objective,
            goal_value=goal_value, quality=quality,
        )

    @property
    def state(self) -> State:
        return self.sim if self.mode == "simulation" else self.observed

    @property
    def rune_key(self) -> str:
        return f"{self.rune.stat}:{self.rune.tier}"

    @property
    def rates(self) -> Rates | None:
        return self.custom.get(self.rune_key)

    @property
    def price(self) -> int:
        return self.prices.get(self.rune_key, 0)

    @property
    def requirements(self) -> dict[str, int]:
        return {self.goal_stat: self.goal_value, **{
            key: value for key, value in self.quality.items() if key != self.goal_stat
        }}

    @property
    def goal_met(self) -> bool:
        return self.state.jets.get(self.goal_stat, 0) >= self.goal_value

    @property
    def reached(self) -> bool:
        return all(self.state.jets.get(key, 0) >= value
                   for key, value in self.requirements.items())

    @property
    def rune_target(self) -> int:
        if self.rune.stat == self.goal_stat:
            return self.goal_value
        return max(self.rune.gain, self.item.maximum(self.rune.stat),
                   self.quality.get(self.rune.stat, 0))

    @property
    def display_changes(self) -> dict[str, list[int]]:
        if self.last_changes:
            return self.last_changes
        return self.state.journal[-1].get("changes", {}) if self.state.journal else {}


def _state_data(state: State) -> dict:
    return {
        "jets": state.jets, "sink": None if state.sink is None else str(state.sink),
        "sequence": state.sequence, "spent": state.spent, "attempts": state.attempts,
        "successes": state.successes, "journal": state.journal,
    }


def export_session(session: Session) -> bytes:
    payload = {
        "schema": 2, "profile": PROFILE,
        "warning": "Bac à sable nominal ; ni preuve de jet ni résultat serveur. Journal déclaratif.",
        "item": {
            "name": session.item.name, "token": session.item.token,
            "bounds": session.item.bounds, "source": session.item.source,
            "unsupported": session.item.unsupported, "immutable": session.item.immutable,
        },
        "simulation": _state_data(session.sim),
        "observations": _state_data(session.observed),
        "rune": {"stat": session.rune.stat, "tier": session.rune.tier},
        "goal": {"stat": session.goal_stat, "value": session.goal_value},
        "quality": session.quality,
        "mode": session.mode, "seed": session.seed,
        "rates": {key: {"sc": rate.sc, "sn": rate.sn} for key, rate in session.custom.items()},
        "prices": session.prices,
        "budget": asdict(session.budget),
        "math": {
            "p": session.p, "cap": session.cap, "experiments": session.experiments,
            "confidence": session.confidence,
        },
        "observation_ready": session.observation_ready,
    }
    _validate_utf8(payload)
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(data) > EXPORT_LIMIT:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > EXPORT_LIMIT:
        raise ValueError("Export trop volumineux (limite : 512 Kio).")
    return data


def _object(value: object, allowed: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError("Objet JSON invalide.")
    if allowed is not None and set(value) - allowed:
        raise ValueError("Champ inattendu dans la sauvegarde.")
    return value


def _rune_key(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError("Identité de rune invalide.")
    rune = Rune(parts[0], int(parts[1]))
    return f"{rune.stat}:{rune.tier}"


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > 100:
        raise ValueError("Liste de texte invalide.")
    if any(not isinstance(text, str) or len(text) > 300 for text in value):
        raise ValueError("Texte invalide.")
    return tuple(value)


def _validate_utf8(value: object) -> None:
    """Refuse les surrogates JSON isoles avant toute publication ou reexport."""
    pending = [value]
    visited = set()
    while pending:
        current = pending.pop()
        if isinstance(current, (dict, list, tuple)):
            identity = id(current)
            if identity in visited:
                continue  # json.dumps rejettera ensuite une eventuelle reference circulaire.
            visited.add(identity)
        if isinstance(current, str):
            try:
                current.encode("utf-8")
            except UnicodeError:
                raise ValueError("Texte Unicode invalide dans la sauvegarde.") from None
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)


def _validate_journal_tail(state: State) -> None:
    """Verifie la portion conservee, sans inventer les essais deja tronques.

    On remonte depuis le snapshot courant ; les deltas v1 (sans changes)
    permettent de poursuivre la verification des lignes v2 plus anciennes.
    """
    if not state.journal:
        return
    jets = dict(state.jets)
    sink = state.sink
    expected_n = state.sequence
    for row in reversed(state.journal):
        if row["n"] != expected_n:
            raise ValueError("Journal incomplet : la fin conservée doit être consécutive et actuelle.")
        expected_n -= 1
        after_sink = None if row["sink_after"] is None else decimal_value(row["sink_after"])
        if after_sink != sink:
            raise ValueError("Puits du journal incompatible avec l'état courant ou l'essai suivant.")
        sink = None if row["sink_before"] is None else decimal_value(row["sink_before"])
        gained = row["gain"] if row["outcome"] != "EC" else 0
        for key in {row["stat"], *row["losses"]}:
            after = jets.get(key, 0)
            before = after - (gained if key == row["stat"] else 0) + row["losses"].get(key, 0)
            integer(before, -10000, 10000, "Jet reconstruit du journal")
            if "changes" in row and row["changes"][key] != [before, after]:
                raise ValueError("Jets du journal incompatibles avec l'état courant ou l'essai suivant.")
            jets[key] = before


def _load_state(payload: object) -> State:
    data = _object(payload, {"jets", "sink", "sequence", "spent", "attempts", "successes", "journal"})
    state = State(
        dict(_object(data["jets"])),
        None if data["sink"] is None else decimal_value(data["sink"]),
        integer(data["sequence"], 0, 10**9, "Séquence"),
        integer(data["spent"], 0, 10**18, "Dépense"),
        integer(data["attempts"], 0, 10**9, "Tentatives"),
        integer(data["successes"], 0, data["attempts"], "Réussites"),
    )
    state.validate()
    journal = data.get("journal", [])
    if not isinstance(journal, list) or len(journal) > 100:
        raise ValueError("Journal invalide.")
    for raw in journal:
        row = dict(_object(raw, {
            "n", "mode", "rune", "stat", "gain", "weight", "outcome", "losses",
            "sink_before", "sink_after", "price", "unexplained_weight",
            "applied_gain", "changes", "rates", "profile",
        }))
        if row.get("mode") not in {"simulation", "observation"} or row.get("outcome") not in {"SC", "SN", "EC"}:
            raise ValueError("Événement de journal invalide.")
        if row.get("stat") not in STATS:
            raise ValueError("Statistique du journal invalide.")
        if not isinstance(row.get("rune"), str) or len(row["rune"]) > 60:
            raise ValueError("Rune du journal invalide.")
        integer(row["n"], 1, 10**9, "Ligne de journal")
        integer(row["gain"], 1, 100, "Gain")
        integer(row["price"], 0, 10**12, "Prix")
        for field_name in ("weight", "unexplained_weight", "sink_before", "sink_after"):
            if row[field_name] is None and field_name in {"sink_before", "sink_after"}:
                continue
            row[field_name] = weight_text(decimal_value(row[field_name]))
        for key, value in _object(row["losses"]).items():
            if key not in STATS:
                raise ValueError("Statistique du journal invalide.")
            integer(value, 0, 10000, "Perte")
        if row["outcome"] == "SC" and any(row["losses"].values()):
            raise ValueError("Un succès critique du journal ne peut pas comporter de pertes.")
        gain = row["gain"] if row["outcome"] in {"SC", "SN"} else 0
        if "applied_gain" in row:
            integer(row["applied_gain"], 0, 100, "Gain appliqué")
            if row["applied_gain"] != gain:
                raise ValueError("Gain du journal incohérent.")
        if "profile" in row and row["profile"] not in {PROFILE, LEGACY_PROFILE}:
            raise ValueError("Profil de journal inconnu.")
        if "changes" in row:
            changes = _object(row["changes"])
            if set(changes) != {row["stat"], *row["losses"]}:
                raise ValueError("Lignes de bilan du journal incohérentes.")
            for key, pair in changes.items():
                if key not in STATS or not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Bilan de jet invalide.")
                before, after = (integer(value, -10000, 10000, "Jet du journal") for value in pair)
                expected = (gain if key == row["stat"] else 0) - row["losses"].get(key, 0)
                if after - before != expected:
                    raise ValueError("Variation de jet incohérente.")
        if row.get("rates") is not None:
            rates_data = _object(row["rates"], {"sc", "sn", "source"})
            if not isinstance(rates_data.get("source"), str) or len(rates_data["source"]) > 160:
                raise ValueError("Source des taux invalide.")
            Rates(**rates_data)
        if row["n"] > state.sequence or (state.journal and row["n"] <= state.journal[-1]["n"]):
            raise ValueError("Ordre du journal incohérent.")
        state.journal.append(dict(row))
    if (len(state.journal) > state.attempts
            or sum(row["price"] for row in state.journal) > state.spent
            or sum(row["outcome"] != "EC" for row in state.journal) > state.successes):
        raise ValueError("Compteurs du journal incohérents.")
    if sum(row["outcome"] == "EC" for row in state.journal) > state.attempts - state.successes:
        raise ValueError("Compteurs d'échecs du journal incohérents.")
    _validate_journal_tail(state)
    return state


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Champ JSON répété.")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"Constante JSON interdite : {value}.")


def import_session(raw: bytes) -> Session:
    if not isinstance(raw, bytes) or len(raw) > EXPORT_LIMIT:
        raise ValueError("Fichier JSON limité à 512 Kio.")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        _validate_utf8(data)
        data = _object(data, {
            "schema", "profile", "warning", "item", "simulation", "observations",
            "rune", "goal", "mode", "seed", "rates", "prices", "budget", "math",
            "observation_ready", "quality",
        })
        legacy = data["schema"] == 1 and data["profile"] == LEGACY_PROFILE
        if type(data["schema"]) is not int or not (
            legacy or (data["schema"] == 2 and data["profile"] == PROFILE)
        ):
            raise ValueError("Version de sauvegarde non prise en charge.")
        item_data = _object(data["item"], {
            "name", "token", "bounds", "source", "unsupported", "immutable",
        })
        if not isinstance(item_data.get("name"), str) or not isinstance(item_data.get("token"), str):
            raise ValueError("Identité de l'objet invalide.")
        bounds = {}
        for key, pair in _object(item_data["bounds"]).items():
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("Bornes invalides.")
            bounds[key] = tuple(pair)
        item = Item(
            str(item_data["name"]), str(item_data["token"]), bounds,
            "Snapshot importé, déclaratif et non revérifié au catalogue",
            _strings(item_data.get("unsupported", [])), _strings(item_data.get("immutable", [])),
        )
        session = Session.create(item)
        session.sim = _load_state(data["simulation"])
        validate_item_jets(item, session.sim.jets)
        session.observed = _load_state(data["observations"])
        if any(row["mode"] != "simulation" for row in session.sim.journal):
            raise ValueError("Journal simulé mélangé avec des observations.")
        if any(row["mode"] != "observation" for row in session.observed.journal):
            raise ValueError("Journal observé mélangé avec des simulations.")
        rune_data = _object(data["rune"], {"stat", "tier"})
        session.rune = Rune(**rune_data)
        goal = _object(data["goal"], {"stat", "value"})
        if goal["stat"] not in STATS:
            raise ValueError("Objectif inconnu.")
        session.goal_stat = goal["stat"]
        session.goal_value = integer(goal["value"], 1, 10000, "Objectif")
        session.quality = {}
        for key, value in _object(data.get("quality", {})).items():
            if key not in STATS:
                raise ValueError("Critère de qualité inconnu.")
            if key == session.goal_stat:
                raise ValueError("L'objectif principal ne doit pas être répété dans les seuils.")
            session.quality[key] = integer(value, 0, 10000, "Seuil de qualité")
        validate_goals(item, session.requirements)
        session.seed = integer(data["seed"], 0, 2**64 - 1, "Graine")
        for key, value in _object(data["rates"]).items():
            session.custom[_rune_key(key)] = Rates(**_object(value, {"sc", "sn"}))
        for key, value in _object(data["prices"]).items():
            session.prices[_rune_key(key)] = integer(value, 0, 10**12, "Prix")
        session.budget = Budget(**_object(
            data["budget"], {"rune", "rebuild", "item", "preparation", "limit"},
        ))
        maths = _object(data["math"], {"p", "cap", "experiments", "confidence"})
        session.p = probability(maths["p"])
        session.confidence = probability(maths["confidence"])
        session.cap = integer(maths["cap"], 0, 1000000, "Plafond")
        session.experiments = integer(maths["experiments"], 1, 50000, "Campagnes")
        if data["mode"] not in {"simulation", "observation"}:
            raise ValueError("Mode inconnu.")
        session.mode = data["mode"]
        if type(data["observation_ready"]) is not bool:
            raise ValueError("État de suivi invalide.")
        session.observation_ready = data["observation_ready"]
        session.notice = "Snapshot importé : paramètres et historique déclaratifs, jamais preuve d'un résultat en jeu."
        try:
            validate_item_jets(item, session.observed.jets)
        except ValueError:
            session.notice += " Le suivi contient un jet hors profil local : conservé comme déclaration, non validé en simulation."
        if legacy:
            session.notice += (
                " Migration v1 → v2 : jets et journaux conservés ; les futurs tirages changent de moteur. "
                "Ancien objectif simple conservé : ajoutez vos seuils dans « Objectifs »."
            )
        return session
    except (KeyError, TypeError, UnicodeError, OverflowError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("Sauvegarde /exo invalide ou incomplète.") from exc
