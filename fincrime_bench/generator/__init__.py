"""Deterministic synthetic world + task generator.

    from fincrime_bench.generator import build_world, write_world
    w = build_world(seed=20251)
    write_world(w, "data/public")
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from ..schema import TABLES, column_names
from . import aml, capstone, ownership, taxfraud, telecom
from .tax import build_tax_background
from .world import World

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20251


def build_world(seed: int = DEFAULT_SEED, scale: float = 1.0) -> World:
    w = World(seed, scale)
    w.build_background()
    build_tax_background(w)

    # ---- AML
    aml.plant_layering(w, "aml_layering_01", "easy", n_hops=3, exit_kind="cash", month=2)
    aml.plant_layering(w, "aml_layering_02", "medium", n_hops=4, exit_kind="intl", month=5)
    aml.plant_layering(w, "aml_layering_03", "hard", n_hops=5, exit_kind="intl", month=8, split_at=2, owner_depth=2,
                       distractors=True)
    aml.plant_layering(w, "aml_layering_04", "hard", n_hops=6, exit_kind="cash", month=10, split_at=3,
                       distractors=True)
    aml.plant_smurfing(w, "aml_smurfing_01")
    aml.plant_structuring(w, "aml_structuring_01")
    aml.plant_mule_ring(w, "aml_mule_01", "medium", link="device", month=3)
    aml.plant_mule_ring(w, "aml_mule_02", "hard", link="ip", month=9)
    aml.plant_ato(w, "aml_ato_01")
    aml.plant_round_trip(w, "aml_roundtrip_01")
    # ---- corporate ownership
    ownership.plant_ubo(w, "own_ubo_01", "easy")
    ownership.plant_ubo(w, "own_ubo_02", "medium")
    ownership.plant_ubo(w, "own_ubo_03", "hard")
    ownership.plant_kickback(w, "own_kickback_01", "medium", variant="direct")
    ownership.plant_kickback(w, "own_kickback_02", "hard", variant="spouse")
    # ---- telecom
    telecom.plant_contact_chain(w, "tel_chain_01", "medium")
    telecom.plant_contact_chain(w, "tel_chain_02", "hard")
    telecom.plant_burner(w, "tel_burner_01", "medium")
    telecom.plant_burner(w, "tel_burner_02", "hard")
    # ---- cross-domain investigations
    capstone.plant_capstone_scam(w, "x_capstone_01")
    capstone.plant_capstone_audit(w, "x_capstone_02")
    # ---- tax
    taxfraud.plant_preparer(w, "tax_preparer_01")
    taxfraud.plant_dependents(w, "tax_dependents_01")
    taxfraud.plant_unreported(w, "tax_unreported_01")
    taxfraud.plant_lifestyle(w, "tax_lifestyle_01", "tax_networth_01")
    taxfraud.plant_skimming(w, "tax_skimming_01")

    finalize(w)
    return w


def finalize(w: World) -> None:
    for a in w.answers:
        if a["answer"] is None:
            a["answer"] = w.deferred[a["task_id"]]()
    order = {t["task_id"]: i for i, t in enumerate(sorted(w.tasks, key=lambda t: t["task_id"]))}
    w.tasks.sort(key=lambda t: order[t["task_id"]])
    w.answers.sort(key=lambda a: order[a["task_id"]])


_SORT_KEYS = {
    "transactions": ("timestamp", "txn_id"),
    "calls": ("timestamp", "call_id"),
    "logins": ("timestamp", "event_id"),
    "ownership": ("owned_id", "owner_id", "role", "start_date"),
    "relationships": ("person_id_1", "person_id_2"),
    "return_dependents": ("return_id", "dependent_person_id"),
}


def _fmt(v, ctype: str) -> str:
    if v is None:
        return ""
    if ctype == "REAL":
        return f"{float(v):.2f}"
    return str(v)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_world(w: World, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"benchmark": "FinCrimeBench", "generator_version": GENERATOR_VERSION, "seed": w.seed,
                "scale": w.scale, "tables": {}, "tasks": len(w.tasks)}
    for name, spec in TABLES.items():
        cols = column_names(name)
        types = {c[0]: c[1] for c in spec["columns"]}
        keys = _SORT_KEYS.get(name, (cols[0],))
        rows = sorted(w.tables[name], key=lambda r: tuple("" if r.get(k) is None else str(r.get(k)) for k in keys))
        path = out / f"{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f, lineterminator="\n")
            wr.writerow(cols)
            for r in rows:
                wr.writerow([_fmt(r.get(c), types[c]) for c in cols])
        manifest["tables"][name] = {"rows": len(rows), "sha256": _sha256(path)}
    with open(out / "tasks.jsonl", "w", encoding="utf-8") as f:
        for t in w.tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(out / "answers.jsonl", "w", encoding="utf-8") as f:
        for a in w.answers:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    for extra in ("tasks.jsonl", "answers.jsonl"):
        manifest[extra.replace(".jsonl", "_sha256")] = _sha256(out / extra)
    with open(out / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return manifest


def generate(seed: int = DEFAULT_SEED, out_dir: str | Path = "data/public", scale: float = 1.0) -> dict:
    return write_world(build_world(seed, scale), out_dir)
