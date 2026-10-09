"""Dataset integrity: determinism, referential integrity, answer-key sanity, no label leakage."""

import csv
import json
import re

import pytest

from conftest import PUBLIC

from fincrime_bench.schema import TABLES


def load(name):
    with open(PUBLIC / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def T():
    return {name: load(name) for name in TABLES}


def test_committed_data_matches_generator(tmp_path):
    """The committed public split is exactly what the generator produces for its seed."""
    from fincrime_bench.generator import generate
    committed = json.loads((PUBLIC / "manifest.json").read_text())
    fresh = generate(seed=committed["seed"], out_dir=tmp_path, scale=committed["scale"])
    assert fresh == committed


def test_primary_keys_unique(T):
    for name, rows in T.items():
        pk = TABLES[name]["columns"][0][0]
        if name in ("relationships", "ownership", "return_dependents"):
            continue
        ids = [r[pk] for r in rows]
        assert len(ids) == len(set(ids)), name


def test_referential_integrity(T):
    persons = {r["person_id"] for r in T["persons"]}
    businesses = {r["business_id"] for r in T["businesses"]}
    accounts = {r["account_id"] for r in T["accounts"]}
    phones = {r["phone_number"] for r in T["phones"]}
    txns = {r["txn_id"] for r in T["transactions"]}
    tins = {r["tin"] for r in T["persons"] if r["tin"]} | {r["ein"] for r in T["businesses"]}
    addresses = {r["address_id"] for r in T["addresses"]}
    entities = persons | businesses
    assert all(r["address_id"] in addresses for r in T["persons"])
    assert all(r["address_id"] in addresses for r in T["businesses"])
    assert all(not r["employer_id"] or r["employer_id"] in businesses for r in T["persons"])
    assert all(r["holder_id"] in entities for r in T["accounts"])
    assert all(not r["authorized_signer_id"] or r["authorized_signer_id"] in persons for r in T["accounts"])
    for r in T["transactions"]:
        assert r["from_account"] or r["to_account"]
        assert not r["from_account"] or r["from_account"] in accounts
        assert not r["to_account"] or r["to_account"] in accounts
        assert not r["conducted_by"] or r["conducted_by"] in persons
        assert r["timestamp"].startswith("2025-")
    for r in T["logins"]:
        assert r["person_id"] in persons
        assert not r["txn_id"] or r["txn_id"] in txns
    assert all(not r["subscriber_id"] or r["subscriber_id"] in persons for r in T["phones"])
    assert all(r["caller"] in phones and r["callee"] in phones for r in T["calls"])
    assert all(r["owner_id"] in entities and r["owned_id"] in businesses for r in T["ownership"])
    assert all(r["person_id_1"] in persons and r["person_id_2"] in persons for r in T["relationships"])
    returns = {r["return_id"] for r in T["tax_returns"]}
    assert all(r["filer_tin"] in tins for r in T["tax_returns"])
    assert all(not r["refund_account_id"] or r["refund_account_id"] in accounts for r in T["tax_returns"])
    assert all(r["return_id"] in returns and r["dependent_person_id"] in persons for r in T["return_dependents"])
    assert all(r["payer_tin"] in tins and r["payee_tin"] in tins for r in T["info_returns"])
    assert all(r["owner_id"] in entities for r in T["assets"])
    assert all(r["person_id"] in persons for r in T["preparers"])


def test_answer_ids_exist_and_decoys_are_not_gold(bench, T):
    universe = set()
    for name in TABLES:
        pk = TABLES[name]["columns"][0][0]
        universe |= {r[pk] for r in T[name]}
    universe |= {r["tin"] for r in T["persons"] if r["tin"]}
    for t in bench.tasks:
        key = bench.keys[t["task_id"]]
        for f in t["answer_fields"]:
            gold = key["answer"][f["name"]]
            ids = []
            if f["type"] == "id":
                ids = [gold]
            elif f["type"] == "id_set":
                ids = gold
                assert gold, f"{t['task_id']}.{f['name']} empty"
            elif f["type"] == "id_number_map":
                ids = [x["id"] for x in gold]
                assert gold, f"{t['task_id']}.{f['name']} empty"
            for i in ids:
                assert i in universe, f"{t['task_id']}.{f['name']}: {i} not in data"
            decoys = set(key["decoys"].get(f["name"], []))
            assert not decoys & set(ids), f"{t['task_id']}.{f['name']}: decoy is also gold"


def test_task_specs_well_formed(bench):
    ids = [t["task_id"] for t in bench.tasks]
    assert len(ids) == len(set(ids)) == len(bench.keys)
    for t in bench.tasks:
        assert t["category"] in {"aml", "tax", "ownership", "telecom", "investigation"}
        assert t["difficulty"] in {"easy", "medium", "hard"}
        assert abs(sum(f["weight"] for f in t["answer_fields"]) - 1.0) < 1e-9, t["task_id"]
        assert len(t["prompt"]) > 100


GIVEAWAYS = re.compile(r"\b(mule|launder\w*|fraud\w*|shell|smurf\w*|nominee|kickback|skim\w*|scam\w*|burner|decoy|"
                       r"suspicious|planted|ground.?truth)\b", re.I)


def test_no_label_leakage_in_data(T):
    for name, rows in T.items():
        for r in rows:
            for v in r.values():
                assert not GIVEAWAYS.search(v or ""), f"{name}: {v}"


def test_no_activity_before_account_opened(T):
    opened = {r["account_id"]: r["open_date"] for r in T["accounts"]}
    for r in T["transactions"]:
        for col in ("from_account", "to_account"):
            if r[col]:
                assert r["timestamp"][:10] >= opened[r[col]], (r["txn_id"], col, opened[r[col]])


def test_people_are_adults_when_acting(T):
    dob = {r["person_id"]: r["dob"] for r in T["persons"]}
    for r in T["ownership"]:
        if r["owner_id"] in dob:
            assert int(r["start_date"][:4]) - int(dob[r["owner_id"]][:4]) >= 18, r
    for r in T["assets"]:
        if r["owner_id"] in dob:
            assert int(r["purchase_date"][:4]) - int(dob[r["owner_id"]][:4]) >= 18, r
        if r["asset_type"] == "vehicle":
            assert int(r["description"][:4]) <= int(r["purchase_date"][:4]) + 1, r


def test_layering_intermediaries_never_overdrawn(bench, T):
    """Freshly opened pass-through accounts never send more than they have received so far.

    (Long-standing accounts have an unknown opening balance from before 2025, so only accounts opened
    from 2024 on are checked.)"""
    opened = {r["account_id"]: r["open_date"] for r in T["accounts"]}
    by_acct = {}
    for r in T["transactions"]:
        if r["from_account"]:
            by_acct.setdefault(r["from_account"], []).append((r["timestamp"], -float(r["amount"])))
        if r["to_account"]:
            by_acct.setdefault(r["to_account"], []).append((r["timestamp"], float(r["amount"])))
    for t in bench.tasks:
        if t["scheme"] != "layering":
            continue
        for acct in bench.keys[t["task_id"]]["answer"]["accounts"]:
            if opened[acct] < "2024-01-01":
                continue
            bal = 0.0
            for _ts, amt in sorted(by_acct[acct]):
                bal += amt
                assert bal > -1.0, (t["task_id"], acct, bal)
