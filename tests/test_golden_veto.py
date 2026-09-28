"""Tests del veto duro en `golden diff` (Fase 0). Solo stdlib + tmp_path.

Golden temporal de 1 fila de peso 3: una hoja puntuada equivocada debe dar
exit 2 (veto bloquea de verdad), una correcta exit 0. dry_run=True para no
escribir results.jsonl.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402

TASK = "demo_veto"

CHECKS = '''"""Checks minimos para la task demo_veto (solo para tests del veto)."""


def hard_filter(row):
    return None


def decide(total, dims, row):
    try:
        total = float(total)
    except (TypeError, ValueError):
        return "no_califica"
    return "califica" if total >= 7 else "no_califica"


def validate(row):
    return []
'''

RUBRIC = {
    "task": TASK,
    "version": "test-v1",
    "score_tolerance": 0.5,
    "dimensions": [{"key": "O", "max": 2}],
}

GOLDEN_ROW = {
    "id": "T-VETO01",
    "task": TASK,
    "weight": 3,
    "frozen_at": "2026-09-22",
    "input": {"gate": "ok", "handle": "@test", "oferta": "programa"},
    "expected": {"decision": "califica", "score": 8.0, "truth_source": "verdad humana de test"},
}


def _sheet(tmp_path, score):
    p = tmp_path / "scored.jsonl"
    p.write_text(json.dumps({"id": "T-VETO01", "score": score, "dims": {"O": 2}}) + "\n",
                 encoding="utf-8")
    return p


@pytest.fixture
def rubric(tmp_path, monkeypatch):
    g = tmp_path / "golden"
    (g / "checks").mkdir(parents=True)
    (g / "checks" / f"{TASK}.py").write_text(CHECKS, encoding="utf-8")
    g.joinpath("golden.jsonl").write_text(json.dumps(GOLDEN_ROW) + "\n", encoding="utf-8")
    rub = tmp_path / "rubric.json"
    rub.write_text(json.dumps(RUBRIC), encoding="utf-8")
    monkeypatch.setattr(agent, "GOLDEN_DIR", g)
    return rub


def test_veto_peso3_devuelve_2(rubric, tmp_path):
    rc = agent.cmd_golden_diff(str(_sheet(tmp_path, 3.0)), TASK, str(rubric), dry_run=True)
    assert rc == 2
    # dry_run no escribe results.jsonl (el golden temporal queda limpio)
    assert not (tmp_path / "golden" / "results.jsonl").exists()


def test_hoja_correcta_devuelve_0(rubric, tmp_path):
    rc = agent.cmd_golden_diff(str(_sheet(tmp_path, 8.0)), TASK, str(rubric), dry_run=True)
    assert rc == 0
