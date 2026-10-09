"""Field-level scoring of submitted answers against the answer key.

Every task score is in [0, 1]: a weighted mean of its answer-field scores.

Field types
-----------
id             exact match after normalisation (case/punctuation-insensitive)        -> 0 or 1
id_set         F1 between predicted and gold sets (precision punishes false accusations)
number         1 within tolerance, decaying linearly to 0 at ``zero_at`` relative error
bool           exact match
id_number_map  (1 - value_weight) * key-F1 + value_weight * mean value score over gold keys
"""

from __future__ import annotations

import math
import re

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def norm_id(x) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(x).upper())
    if s.isdigit() and len(s) == 11 and s.startswith("1"):
        s = s[1:]  # phone numbers: +1 555 123 4567 == 555-123-4567
    return s


def _as_list(pred) -> list:
    if pred is None:
        return []
    if isinstance(pred, str):
        return [p for p in re.split(r"[,\s;]+", pred) if p]
    if isinstance(pred, dict):
        return list(pred.keys())
    if isinstance(pred, (list, tuple, set)):
        out = []
        for p in pred:
            if isinstance(p, dict):
                p = p.get("id", next(iter(p.values()), None)) if p else None
            if p is not None:
                out.append(p)
        return out
    return [pred]


def parse_number(x) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, str):
        s = x.replace(",", "").replace("$", "").strip().lower()
        mult = 1.0
        if s.endswith("%"):
            s = s[:-1]
        for suf, m in (("k", 1e3), ("m", 1e6), ("million", 1e6), ("thousand", 1e3)):
            if s.endswith(suf):
                s, mult = s[: -len(suf)].strip(), m
                break
        m = _NUM_RE.search(s)
        return float(m.group()) * mult if m else None
    return None


def parse_bool(x) -> bool | None:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("true", "yes", "y", "1"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
    return None


def set_f1(pred: list, gold: list) -> dict:
    p = {norm_id(x) for x in pred}
    g = {norm_id(x) for x in gold}
    tp = len(p & g)
    if not p and not g:
        return {"f1": 1.0, "precision": 1.0, "recall": 1.0, "tp": 0, "fp": 0, "fn": 0}
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"f1": f1, "precision": prec, "recall": rec, "tp": tp, "fp": len(p - g), "fn": len(g - p)}


def number_score(pred, gold: float, f: dict) -> float:
    v = parse_number(pred)
    if v is None:
        return 0.0
    if "abs_tol" in f:
        err = abs(v - gold)
        tol, zero = f["abs_tol"], f.get("abs_zero_at", f["abs_tol"] * 10)
    else:
        err = abs(v - gold) / max(abs(gold), 1e-9)
        tol, zero = f.get("rel_tol", 0.01), f.get("zero_at", 0.25)
    if err <= tol:
        return 1.0
    if err >= zero:
        return 0.0
    return 1.0 - (err - tol) / (zero - tol)


def _map_items(pred) -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(pred, dict):
        for k, v in pred.items():
            out[norm_id(k)] = v
    elif isinstance(pred, (list, tuple)):
        for item in pred:
            if isinstance(item, dict):
                k = item.get("id") or item.get("tin") or item.get("business_id") or item.get("person_id")
                v = item.get("value", item.get("amount", item.get("pct")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                k, v = item[0], item[1]
            else:
                continue
            if k is not None:
                out[norm_id(k)] = v
    return out


def score_field(f: dict, pred, gold) -> tuple[float, dict]:
    t = f["type"]
    if t == "id":
        ok = pred is not None and not isinstance(pred, (list, dict)) and norm_id(pred) == norm_id(gold)
        return (1.0 if ok else 0.0), {"pred": pred, "gold": gold}
    if t == "id_set":
        d = set_f1(_as_list(pred), list(gold))
        return d["f1"], d
    if t == "number":
        s = number_score(pred, float(gold), f)
        return s, {"pred": pred, "gold": gold}
    if t == "bool":
        b = parse_bool(pred)
        return (1.0 if b is not None and b == bool(gold) else 0.0), {"pred": pred, "gold": gold}
    if t == "id_number_map":
        pm = _map_items(pred)
        gm = {norm_id(x["id"]): float(x["value"]) for x in gold}
        d = set_f1(list(pm), list(gm))
        vals = [number_score(pm[k], gm[k], f) for k in gm if k in pm]
        value_score = sum(vals) / len(gm) if gm else (1.0 if not pm else 0.0)
        vw = f.get("value_weight", 0.4)
        d["value_score"] = value_score
        return (1 - vw) * d["f1"] + vw * value_score, d
    raise ValueError(f"unknown field type {t}")


def _pred_ids(f: dict, pred) -> list[str]:
    if pred is None:
        return []
    if f["type"] == "id":
        return [norm_id(pred)] if not isinstance(pred, (list, dict)) else []
    if f["type"] == "id_set":
        return [norm_id(x) for x in _as_list(pred)]
    if f["type"] == "id_number_map":
        return list(_map_items(pred))
    return []


def unwrap_submission(sub):
    if isinstance(sub, dict) and "answer" in sub and isinstance(sub["answer"], dict) and len(sub) <= 2:
        return sub["answer"]
    return sub


def score_task(task: dict, key: dict, submission) -> dict:
    """Score one submission. ``key`` is the answers.jsonl record for the task."""
    sub = unwrap_submission(submission)
    sub = sub if isinstance(sub, dict) else {}
    gold = key["answer"]
    decoys = key.get("decoys", {})
    fields, total_w, total = {}, 0.0, 0.0
    decoy_hits, decoy_total = 0, 0
    set_tp = set_fp = set_fn = 0
    for f in task["answer_fields"]:
        pred = sub.get(f["name"])
        s, detail = score_field(f, pred, gold[f["name"]])
        fields[f["name"]] = {"score": round(s, 4), **{k: v for k, v in detail.items() if k not in ("pred", "gold")}}
        total += f["weight"] * s
        total_w += f["weight"]
        if f["type"] in ("id_set", "id_number_map"):
            set_tp += detail["tp"]
            set_fp += detail["fp"]
            set_fn += detail["fn"]
        dset = {norm_id(x) for x in decoys.get(f["name"], [])}
        if dset:
            decoy_total += len(dset)
            decoy_hits += len(dset & set(_pred_ids(f, pred)))
    return {
        "score": round(total / total_w, 4) if total_w else 0.0,
        "fields": fields,
        "decoy_hits": decoy_hits,
        "decoy_total": decoy_total,
        "set_tp": set_tp, "set_fp": set_fp, "set_fn": set_fn,
    }
