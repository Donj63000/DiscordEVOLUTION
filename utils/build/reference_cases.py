"""Je compare des observations fournies sans transformer un test en preuve en jeu."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .calculator import calculate
from .import_export import parse_json
from .models import Build, BuildError, Frozen, StatValue, Text, as_stats


class ReferenceCase(Frozen):
    schema_version: Literal[1] = 1
    name: Text
    origin: Literal["synthetic", "in_game"]
    game_version: Text
    observed_at: str | None = None
    evidence: Annotated[tuple[Annotated[str, Field(max_length=400)], ...], Field(max_length=8)] = ()
    build: Build
    expected: Annotated[tuple[StatValue, ...], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def observation_has_evidence(self):
        as_stats(self.expected)
        if self.origin == "in_game":
            if not self.evidence or not self.observed_at:
                raise ValueError("Une observation en jeu exige date et preuve consultable.")
            if datetime.fromisoformat(self.observed_at).tzinfo is None:
                raise ValueError("La date d'observation doit préciser son fuseau.")
            if any(not entry.startswith("https://") for entry in self.evidence):
                raise ValueError("La preuve en jeu doit avoir une URL HTTPS consultable.")
        return self


def load_reference_cases(raw):
    payload = parse_json(raw)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "cases"} or payload["schema_version"] != 1:
        raise BuildError("Corpus de référence version 1 attendu.")
    if not isinstance(payload["cases"], list) or not 1 <= len(payload["cases"]) <= 100:
        raise BuildError("Le corpus doit contenir entre 1 et 100 cas.")
    return tuple(ReferenceCase.model_validate(case) for case in payload["cases"])


def check_reference_cases(cases, catalog, rules):
    outcomes = []
    for case in cases:
        report = calculate(case.build, catalog, rules)
        differences = {row.stat: {"expected": row.value, "actual": report.totals[row.stat]}
                       for row in case.expected if row.value != report.totals[row.stat]}
        outcomes.append({"name": case.name, "origin": case.origin, "differences": differences,
                         "matches": not differences,
                         "in_game_evidence_supplied": case.origin == "in_game",
                         "partial_metrics": [row.stat for row in case.expected if not report.known(row.stat)]})
    return tuple(outcomes)
