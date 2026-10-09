import pytest

from fincrime_bench.scoring import norm_id, number_score, parse_number, score_field, score_task


def test_norm_id():
    assert norm_id(" p123456 ") == "P123456"
    assert norm_id("+1 (555) 123-4567") == norm_id("555-123-4567") == "5551234567"
    assert norm_id("912-00-1234") == norm_id("912001234")


def test_parse_number():
    assert parse_number("$1,234.50") == 1234.5
    assert parse_number("1.5M") == 1_500_000
    assert parse_number("42%") == 42
    assert parse_number(True) is None
    assert parse_number("n/a") is None


def test_id_field():
    f = {"type": "id"}
    assert score_field(f, "ac1234567", "AC1234567")[0] == 1.0
    assert score_field(f, "AC7654321", "AC1234567")[0] == 0.0
    assert score_field(f, ["AC1234567"], "AC1234567")[0] == 0.0
    assert score_field(f, None, "AC1234567")[0] == 0.0


def test_id_set_f1_penalises_false_accusations():
    f = {"type": "id_set"}
    gold = ["P1", "P2", "P3", "P4"]
    assert score_field(f, gold, gold)[0] == 1.0
    s, d = score_field(f, ["P1", "P2"], gold)
    assert d["precision"] == 1.0 and d["recall"] == 0.5 and s == pytest.approx(2 / 3)
    s_spray, _ = score_field(f, gold + [f"X{i}" for i in range(20)], gold)
    assert s_spray < 0.3  # flagging everyone is not rewarded
    assert score_field(f, "P1, P2;P3 P4", gold)[0] == 1.0
    assert score_field(f, [{"id": "P1"}, {"id": "P2"}, {"id": "P3"}, {"id": "P4"}], gold)[0] == 1.0


def test_number_tolerance_and_decay():
    f = {"type": "number", "rel_tol": 0.01, "zero_at": 0.25}
    assert number_score(100.5, 100, f) == 1.0
    assert number_score(113, 100, f) == pytest.approx(0.5)
    assert number_score(130, 100, f) == 0.0
    assert number_score("$100", 100, f) == 1.0
    fa = {"type": "number", "abs_tol": 1.0, "abs_zero_at": 10.0}
    assert number_score(60.9, 60, fa) == 1.0
    assert number_score(65.5, 60, fa) == pytest.approx(0.5)


def test_bool():
    f = {"type": "bool"}
    assert score_field(f, "yes", True)[0] == 1.0
    assert score_field(f, False, True)[0] == 0.0
    assert score_field(f, None, False)[0] == 0.0


def test_id_number_map_formats():
    f = {"type": "id_number_map", "value_weight": 0.4, "rel_tol": 0.02, "zero_at": 0.25}
    gold = [{"id": "A", "value": 100.0}, {"id": "B", "value": 200.0}]
    assert score_field(f, gold, gold)[0] == pytest.approx(1.0)
    assert score_field(f, {"A": 100, "B": 200}, gold)[0] == pytest.approx(1.0)
    assert score_field(f, [["A", 100], ["B", 200]], gold)[0] == pytest.approx(1.0)
    s, d = score_field(f, [{"id": "A", "value": 100}], gold)
    assert d["recall"] == 0.5 and d["value_score"] == 0.5
    assert s == pytest.approx(0.6 * (2 / 3) + 0.4 * 0.5)
    s, _ = score_field(f, [{"id": "A", "value": 999}, {"id": "B", "value": 200}], gold)
    assert s == pytest.approx(0.6 + 0.4 * 0.5)


def test_score_task_weights_and_decoys():
    task = {"answer_fields": [{"name": "who", "type": "id", "weight": 0.25},
                              {"name": "accts", "type": "id_set", "weight": 0.75}]}
    key = {"answer": {"who": "P1", "accts": ["A1", "A2"]}, "decoys": {"who": ["P9"], "accts": ["A9"]}}
    r = score_task(task, key, {"who": "P9", "accts": ["A1", "A2", "A9"]})
    assert r["score"] == pytest.approx(0.75 * 0.8)
    assert r["decoy_hits"] == 2 and r["decoy_total"] == 2
    assert score_task(task, key, {"answer": {"who": "P1", "accts": ["A1", "A2"]}})["score"] == 1.0
    assert score_task(task, key, "garbage")["score"] == 0.0
