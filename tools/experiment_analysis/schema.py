"""Strict transport schema; scientific registration remains caller-owned."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from . import ANALYSIS_VERSION


class AnalysisError(ValueError):
    """An ambiguous identity, unregistered input, or invalid calculation."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        if key in result:
            raise AnalysisError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def read_json(path: str | Path) -> Any:
    def invalid(value: str) -> None:
        raise AnalysisError(f"non-finite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=invalid)


def obj(value: Any, required: str, optional: str = "") -> dict:
    if not isinstance(value, dict):
        raise AnalysisError("expected a JSON object")
    req, allowed = set(required.split()), set((required + " " + optional).split())
    if set(value) - allowed or req - set(value):
        raise AnalysisError(f"schema fields: missing={sorted(req-set(value))}, unknown={sorted(set(value)-allowed)}")
    return value


def text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or any(ord(c) < 32 for c in value):
        raise AnalysisError("expected nonempty, trimmed text without control characters")
    return value


def sha(value: Any) -> str:
    value = text(value)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise AnalysisError("expected a lowercase SHA-256 digest")
    return value


def number(value: Any, low: float | None = None, high: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AnalysisError("expected a finite number, not a boolean or string")
    result = float(value)
    if low is not None and result < low or high is not None and result > high:
        raise AnalysisError(f"number outside [{low}, {high}]")
    return result


def integer(value: Any, low: int = 0) -> int:
    if type(value) is not int or value < low:
        raise AnalysisError(f"expected an integer >= {low}")
    return value


def strings(value: Any, *, empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not value and not empty:
        raise AnalysisError("expected a nonempty array")
    result = tuple(text(item) for item in value)
    if len(set(result)) != len(result):
        raise AnalysisError("duplicate identifiers")
    return result


@dataclass(frozen=True)
class Measure:
    id: str
    kind: str
    unit: str
    low: float | None
    high: float | None
    causes: tuple[str, ...]

    @classmethod
    def parse(cls, data: dict) -> Measure:
        obj(data, "id kind unit", "bounds causes")
        kind = data["kind"]
        if kind == "scalar":
            if "causes" in data:
                raise AnalysisError("scalar measure cannot have causes")
            bounds = data.get("bounds", [None, None])
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise AnalysisError("bounds require [lower, upper]")
            lo, hi = (None if v is None else number(v) for v in bounds)
            if lo is not None and hi is not None and lo > hi:
                raise AnalysisError("reversed bounds")
            causes = ()
        elif kind == "event":
            if "bounds" in data:
                raise AnalysisError("event measure cannot have scalar bounds")
            causes = strings(data.get("causes"))
            if "censor" in causes:
                raise AnalysisError("censor is an observation status, not an event cause")
            lo = hi = None
        else:
            raise AnalysisError(f"unknown measure kind: {kind}")
        return cls(text(data["id"]), kind, text(data["unit"]), lo, hi, causes)

    def check(self, value: Any) -> None:
        if self.kind == "scalar":
            number(value, self.low, self.high)
        else:
            obj(value, "time cause")
            number(value["time"], 0)
            if value["cause"] not in (*self.causes, "censor"):
                raise AnalysisError(f"unregistered event cause: {value['cause']}")


@dataclass(frozen=True)
class Cell:
    id: str
    domain: str
    primary_label: str
    difficulty: str
    min_instances: int
    min_coverage: float

    @classmethod
    def parse(cls, data: dict) -> Cell:
        obj(data, "id domain primary_label difficulty min_instances min_coverage")
        return cls(*(text(data[key]) for key in ("id", "domain", "primary_label", "difficulty")),
                   integer(data["min_instances"], 1), number(data["min_coverage"], 0, 1))


@dataclass(frozen=True)
class Unit:
    id: str
    cell_id: str
    members: tuple[tuple[str, str], ...]
    repeats: tuple[str, ...]
    supports: tuple[str, ...]
    infrastructure_status: str
    reason: str | None
    family_id: str | None

    @classmethod
    def parse(cls, data: dict) -> Unit:
        obj(data, "id cell_id members repeats supports infrastructure_status", "reason family_id")
        members = data["members"]
        if not isinstance(members, dict) or not members:
            raise AnalysisError("unit members map is required")
        pairs = tuple(sorted((text(k), text(v)) for k, v in members.items()))
        if len(set(v for _, v in pairs)) != len(pairs):
            raise AnalysisError("different members must name different base instances")
        status = data["infrastructure_status"]
        if status not in {"valid", "invalid", "unverified"}:
            raise AnalysisError("unknown infrastructure_status")
        reason = text(data["reason"]) if data.get("reason") is not None else None
        if status != "valid" and not reason or status == "valid" and reason:
            raise AnalysisError("infrastructure reason must agree with status")
        family = text(data["family_id"]) if data.get("family_id") is not None else None
        return cls(text(data["id"]), text(data["cell_id"]), pairs, strings(data["repeats"]),
                   strings(data["supports"], empty=True), status, reason, family)


@dataclass(frozen=True)
class Term:
    member: str
    condition_id: str
    branch_role: str
    checkpoint_round: str
    measure: str
    system_id: str | None

    @classmethod
    def parse(cls, data: dict) -> Term:
        obj(data, "member condition_id branch_role checkpoint_round measure", "system_id")
        return cls(*(text(data[key]) for key in ("member", "condition_id", "branch_role", "checkpoint_round", "measure")),
                   text(data["system_id"]) if data.get("system_id") is not None else None)


@dataclass(frozen=True)
class Family:
    id: str
    anchor: float
    scale: float
    orientation: int
    min_pairs: int
    min_coverage: float
    certificate_digest: str

    @classmethod
    def parse(cls, data: dict) -> Family:
        obj(data, "id anchor scale orientation min_pairs min_coverage certificate_digest")
        scale = number(data["scale"], 0)
        orientation = data["orientation"]
        if scale <= 0 or type(orientation) is not int or orientation not in (-1, 1):
            raise AnalysisError("family requires positive scale and orientation +/-1")
        return cls(text(data["id"]), number(data["anchor"]), scale, orientation,
                   integer(data["min_pairs"], 1), number(data["min_coverage"], 0, 1), sha(data["certificate_digest"]))


@dataclass(frozen=True)
class Estimand:
    id: str
    kind: str
    support_id: str
    cells: tuple[str, ...]
    terms: tuple[Term, ...]
    min_repeats: int
    min_units: int
    min_coverage: float
    denominator_floor: float
    coefficients: tuple[float, ...]
    horizon: float | None
    cause: str | None
    families: tuple[Family, ...]

    @classmethod
    def parse(cls, data: dict) -> Estimand:
        obj(data, "id kind support_id cells terms min_repeats min_units min_coverage",
            "denominator_floor coefficients horizon cause families")
        terms = tuple(Term.parse(v) for v in data["terms"])
        if not terms or len(set(terms)) != len(terms):
            raise AnalysisError("estimand requires unique terms")
        kind = data["kind"]
        arity = {"mean": 1, "ratio_of_means": 2, "mean_of_cell_ratios": 2,
                 "linear": None, "cif": 1, "rmst": 1, "family_gap": 2}
        if kind not in arity or arity[kind] is not None and len(terms) != arity[kind]:
            raise AnalysisError("unsupported estimand or wrong number of terms")
        extras = {
            "mean": set(), "ratio_of_means": {"denominator_floor"},
            "mean_of_cell_ratios": {"denominator_floor"}, "linear": {"coefficients"},
            "cif": {"horizon", "cause"}, "rmst": {"horizon"}, "family_gap": {"families"},
        }[kind]
        for key in {"denominator_floor", "coefficients", "horizon", "cause", "families"} - extras:
            if key in data:
                raise AnalysisError(f"{key} is inapplicable to {kind}")
        if extras - set(data):
            raise AnalysisError(f"missing estimator parameters: {sorted(extras-set(data))}")
        coeff = tuple(number(x) for x in data.get("coefficients", []))
        if kind == "linear" and (len(coeff) != len(terms) or not any(coeff)):
            raise AnalysisError("linear contrast requires a nonzero coefficient per term")
        horizon = number(data["horizon"], 0) if "horizon" in data else None
        if horizon == 0:
            raise AnalysisError("event horizon must be positive")
        families = tuple(Family.parse(x) for x in data.get("families", []))
        if kind == "family_gap" and not families:
            raise AnalysisError("family gap needs a frozen family set")
        unique(families)
        return cls(text(data["id"]), kind, text(data["support_id"]), strings(data["cells"]), terms,
                   integer(data["min_repeats"], 1), integer(data["min_units"], 1), number(data["min_coverage"], 0, 1),
                   number(data.get("denominator_floor", 0), 0), coeff, horizon,
                   text(data["cause"]) if "cause" in data else None, families)


def unique(items: tuple) -> None:
    if len({item.id for item in items}) != len(items):
        raise AnalysisError("duplicate schema IDs")


@dataclass(frozen=True)
class Design:
    id: str
    authority: str
    protocol_digest: str
    digest: str
    systems: tuple[str, ...]
    cells: tuple[Cell, ...]
    units: tuple[Unit, ...]
    measures: tuple[Measure, ...]
    estimands: tuple[Estimand, ...]
    seed: int
    resamples: int

    @classmethod
    def parse(cls, data: dict) -> Design:
        obj(data, "schema id authority protocol_digest systems cells units measures estimands bootstrap")
        if data["schema"] != ANALYSIS_VERSION + ".design":
            raise AnalysisError("unsupported design schema")
        bootstrap = obj(data["bootstrap"], "seed resamples")
        cells, units, measures, estimands = (tuple(parser.parse(v) for v in data[key]) for key, parser in
                                           (("cells", Cell), ("units", Unit), ("measures", Measure), ("estimands", Estimand)))
        for items in (cells, units, measures, estimands):
            unique(items)
        if not cells or not estimands or not measures:
            raise AnalysisError("cells, measures and estimands cannot be empty")
        result = cls(text(data["id"]), text(data["authority"]), sha(data["protocol_digest"]), digest(data),
                     strings(data["systems"]), cells, units, measures, estimands,
                     integer(bootstrap["seed"]), integer(bootstrap["resamples"], 2))
        cell_ids, measure_map = {c.id for c in cells}, {m.id: m for m in measures}
        all_base_ids = [v for unit in units for _, v in unit.members]
        if len(set(all_base_ids)) != len(all_base_ids):
            raise AnalysisError("one base instance belongs to only one registered unit/cell")
        for unit in units:
            if unit.cell_id not in cell_ids:
                raise AnalysisError("unit names an unknown cell")
        for estimand in estimands:
            if set(estimand.cells) - cell_ids:
                raise AnalysisError("estimand names an unknown cell")
            support = [u for u in units if estimand.support_id in u.supports]
            if {u.cell_id for u in support} - set(estimand.cells):
                raise AnalysisError("support has units outside registered cells")
            for term in estimand.terms:
                if term.measure not in measure_map or term.system_id is not None and term.system_id not in result.systems:
                    raise AnalysisError("term names an unknown measure/system")
                measure = measure_map[term.measure]
                event_kind = estimand.kind in {"cif", "rmst"}
                if event_kind != (measure.kind == "event"):
                    raise AnalysisError("estimator does not match measure type")
                if estimand.kind == "cif" and estimand.cause not in measure.causes:
                    raise AnalysisError("CIF cause is not registered")
                if any(term.member not in dict(u.members) for u in support):
                    raise AnalysisError("support unit is missing a term member")
            if any(t.system_id is not None for t in estimand.terms) and any(t.system_id is None for t in estimand.terms):
                raise AnalysisError("system contrasts must specify every term's system_id")
            if estimand.kind in {"linear", "family_gap"} and len({measure_map[t.measure].unit for t in estimand.terms}) != 1:
                raise AnalysisError("contrasted terms must have the same natural unit")
            if estimand.kind == "family_gap":
                if any(t.system_id is not None for t in estimand.terms):
                    raise AnalysisError("family gaps are within-system domain contrasts")
                if estimand.terms[0].member == estimand.terms[1].member:
                    raise AnalysisError("family gap needs distinct source and target members")
                if {u.family_id for u in support} - {f.id for f in estimand.families}:
                    raise AnalysisError("unregistered transfer family")
        return result


RECORD_FIELDS = "unit_id base_instance_id member system_id condition_id branch_role checkpoint_round repeat_id status method_outcome values artifact_digest pair_context_digest"
METHOD_FAILURES = {"method_failure", "timeout", "malformed_action", "budget_overrun", "no_plan"}
METHOD_OUTCOMES = METHOD_FAILURES | {"completed", "safe_stop", "infeasible", "censored", "not_run"}


def record_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(row[k] for k in ("unit_id", "member", "system_id", "condition_id", "branch_role", "checkpoint_round", "repeat_id"))


@dataclass(frozen=True)
class Dataset:
    design: Design
    provenance: dict
    records: tuple[dict, ...]
    digest: str

    @classmethod
    def parse(cls, data: dict, design_data: dict) -> Dataset:
        design = Design.parse(design_data)
        obj(data, "schema design_digest provenance records")
        if data["schema"] != ANALYSIS_VERSION + ".dataset" or data["design_digest"] != design.digest:
            raise AnalysisError("dataset schema or design digest mismatch")
        provenance = obj(data["provenance"], "kind producer source_revision evidence_digests")
        if provenance["kind"] not in {"synthetic", "measured_unverified"}:
            raise AnalysisError("only synthetic or measured_unverified input is supported; no self-certified release")
        text(provenance["producer"])
        text(provenance["source_revision"])
        evidence = strings(provenance["evidence_digests"], empty=True)
        for value in evidence:
            sha(value)
        unit_map, measure_map = {u.id: u for u in design.units}, {m.id: m for m in design.measures}
        seen = set()
        records = []
        for raw in data["records"]:
            obj(raw, RECORD_FIELDS, "reason")
            for k in RECORD_FIELDS.split():
                if k != "values":
                    text(raw[k])
            unit = unit_map.get(raw["unit_id"])
            if unit is None or dict(unit.members).get(raw["member"]) != raw["base_instance_id"]:
                raise AnalysisError("record base identity differs from registered unit member")
            if raw["repeat_id"] not in unit.repeats or raw["system_id"] not in design.systems:
                raise AnalysisError("unregistered repeat/system; retries cannot manufacture sample size")
            if raw["status"] not in {"valid", "infrastructure_invalid", "not_evaluable"}:
                raise AnalysisError("unknown record status")
            if raw["method_outcome"] not in METHOD_OUTCOMES:
                raise AnalysisError("unknown method outcome")
            if raw["status"] != "valid":
                text(raw.get("reason"))
                if raw["values"] or raw["method_outcome"] in METHOD_FAILURES:
                    raise AnalysisError("unavailable records cannot carry values or exclude method failures")
            elif raw.get("reason") is not None or raw["method_outcome"] == "not_run":
                raise AnalysisError("valid outcome cannot have exclusion reason or not_run status")
            if not isinstance(raw["values"], dict) or set(raw["values"]) - set(measure_map):
                raise AnalysisError("invalid or unregistered measure values")
            for name, value in raw["values"].items():
                measure_map[name].check(value)
            if sha(raw["artifact_digest"]) not in evidence:
                raise AnalysisError("record evidence digest is not in the dataset provenance")
            sha(raw["pair_context_digest"])
            key = record_key(raw)
            if key in seen:
                raise AnalysisError("duplicate observation identity; select retry attempts upstream")
            seen.add(key)
            records.append(json.loads(canonical(raw)))
        return cls(design, json.loads(canonical(provenance)), tuple(records), digest(data))
