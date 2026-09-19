"""Prix personnels datés, associés aux jets exacts et jamais déduits du marché."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, StrictInt, field_validator

from .models import Frozen, Hash, Text, StatValue, values, digest, new_id, now, uuid_text


class PriceQuote(Frozen):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    server: Annotated[str, Field(min_length=1, max_length=80)]
    template_ref: Text
    template_revision: Hash
    jet_signature: Hash
    kamas: Annotated[StrictInt, Field(ge=0, le=10**12)]
    observed_at: str = Field(default_factory=now)
    label: Text
    jets: Annotated[tuple[StatValue, ...], Field(max_length=64)] = ()
    _uuid = field_validator("id")(uuid_text)

    @field_validator("observed_at")
    @classmethod
    def dated(cls, value):
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None or moment > datetime.now(timezone.utc):
            raise ValueError("La date du prix doit être passée et inclure son fuseau.")
        return moment.astimezone(timezone.utc).isoformat()


def jet_signature(instance, template):
    """Les identités d'instance ne changent pas le prix de jets identiques."""
    finals = {value.ref: value.value for value in instance.final_values}
    return digest({
        "template": template.ref,
        "revision": template.revision,
        "values": sorted((effect.ref, finals.get(effect.ref, effect.high))
                         for effect in template.effects if effect.kind == "stat"),
        "extras": sorted((value.stat, value.value) for value in instance.extras),
    })


def quote_for(instance, template, server, kamas, observed_at=None):
    finals = {value.ref: value.value for value in instance.final_values}
    displayed = {}
    for effect in template.effects:
        if effect.kind == "stat":
            displayed[effect.stat] = displayed.get(effect.stat, 0) + finals.get(effect.ref, effect.high)
    for extra in instance.extras:
        displayed[extra.stat] = displayed.get(extra.stat, 0) + extra.value
    return PriceQuote(server=server, template_ref=template.ref,
                      template_revision=template.revision,
                      jet_signature=jet_signature(instance, template), kamas=kamas,
                      observed_at=observed_at or now(), label=template.name, jets=values(displayed))


def latest_quotes(rows, server):
    """Un prix récent remplace uniquement le même objet et les mêmes jets."""
    latest = {}
    for row in rows:
        quote = PriceQuote.model_validate(row)
        if quote.server.casefold() != server.strip().casefold():
            continue
        key = quote.template_ref, quote.jet_signature
        previous = latest.get(key)
        if previous is None or datetime.fromisoformat(quote.observed_at) > datetime.fromisoformat(previous.observed_at):
            latest[key] = quote
    return tuple(latest.values())
