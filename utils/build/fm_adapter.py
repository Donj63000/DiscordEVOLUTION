"""Copie des jets de /exo ; aucun changement de session, puits ou probabilité."""
from .models import Instance, EffectValue, StatValue, BuildError, revised


def from_exo(instance, template, session):
    if session.item.token != template.ref:
        raise BuildError("La session /exo ne porte pas sur cet objet.")
    jets = dict(session.state.jets)
    definitions = [e for e in template.effects if e.kind == "stat"]
    if len({e.stat for e in definitions}) != len(definitions):
        raise BuildError("Plusieurs lignes naturelles identiques : importer les jets effet par effet.")
    natural = {e.stat for e in definitions}
    finals = tuple(EffectValue(ref=e.ref, value=jets.get(e.stat, 0)) for e in definitions)
    extras = tuple(StatValue(stat=stat, value=value) for stat, value in sorted(jets.items()) if stat not in natural and value)
    return revised(instance, mode="declared_fm", final_values=[v.model_dump() for v in finals], extras=[v.model_dump() for v in extras])


def to_exo_values(instance, template):
    """Export de valeurs pour recopie ; ce fichier n’est pas une session native /exo."""
    if len({e.stat for e in template.effects if e.kind == "stat"}) != sum(e.kind == "stat" for e in template.effects):
        raise BuildError("Effets multiples non représentables dans une session FM simple.")
    finals = {e.ref: e.value for e in instance.final_values}
    jets = {e.stat: finals.get(e.ref, e.high) for e in template.effects if e.kind == "stat"}
    for extra in instance.extras:
        if extra.stat in jets:
            raise BuildError("Ligne exo déjà présente naturellement.")
        jets[extra.stat] = extra.value
    return {"reference": template.ref, "jets_declares": jets, "avertissement": "Simulation déclarée. Aucune session /exo modifiée."}
