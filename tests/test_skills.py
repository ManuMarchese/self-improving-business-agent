"""Biblioteca de habilidades verificadas: sin fuga de dev/examen y recuperacion determinista."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor import evaluate, skills  # noqa: E402

TRAIN = [
    {"id": "t1", "input": {"enunciado": "Write a function to reverse a string."}},
    {"id": "t2", "input": {"enunciado": "Write a function to sum a list of numbers."}},
    {"id": "t3", "input": {"enunciado": "Write a function to count vowels in a string."}},
]


def _ok(tid, code="def f(x):\n    return x", ok=True):
    ent = next(t for t in TRAIN if t["id"] == tid)
    return {"id": tid, "input": ent["input"], "output": f"```python\n{code}\n```", "ok": ok, "score": 1.0 if ok else 0.0}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "tasks" / "T").mkdir(parents=True)
    (tmp_path / "tasks" / "T" / "train.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in TRAIN), encoding="utf-8")
    p0 = tmp_path / "policies" / "T" / "v0"
    p0.mkdir(parents=True)
    (p0 / "prompt.md").write_text("Resolve: {enunciado}", encoding="utf-8")
    (p0 / "playbook.jsonl").write_text("", encoding="utf-8")
    (p0 / "policy.json").write_text(json.dumps({"id": "v0", "rol_modelo": "rapido"}), encoding="utf-8")
    monkeypatch.setattr(skills, "_ROOT", tmp_path)
    monkeypatch.setattr(skills, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(skills, "POLICIES_DIR", tmp_path / "policies")
    return tmp_path


def _results(repo, nombre, filas):
    d = repo / "runs_motor" / nombre
    d.mkdir(parents=True)
    p = d / "results.jsonl"
    p.write_text("".join(json.dumps(f) + "\n" for f in filas), encoding="utf-8")
    return p


def test_solo_soluciones_verificadas_de_train(repo):
    p = _results(repo, "x-T-v0-train", [_ok("t1"), _ok("t2", ok=False), _ok("t3")])
    hijo, n = skills.construir("T", "v0", p)
    assert hijo == "v1" and n == 2
    ids = [s["id"] for s in skills.cargar_skills(repo / "policies" / "T" / "v1")]
    assert ids == ["t1", "t3"]


def test_rechaza_resultados_de_dev(repo):
    p = _results(repo, "x-T-v0-dev", [_ok("t1")])
    with pytest.raises(ValueError, match="TRAIN"):
        skills.construir("T", "v0", p)


def test_rechaza_ids_que_no_son_de_train(repo):
    fila = _ok("t1")
    fila["id"] = "mbpp-555"  # un id de dev colado en un archivo "-train"
    p = _results(repo, "x-T-v0-train", [fila])
    with pytest.raises(ValueError, match="fuga"):
        skills.construir("T", "v0", p)


def test_recuperar_es_determinista_y_excluye_el_propio_id():
    sk = [{"id": "t1", "enunciado": "reverse a string", "codigo": "a"},
          {"id": "t3", "enunciado": "count vowels in a string", "codigo": "b"}]
    r = skills.recuperar(sk, "reverse a string", k=2)
    assert [s["id"] for s in r][0] == "t1"
    r2 = skills.recuperar(sk, "reverse a string", k=2, excluir_id="t1")
    assert all(s["id"] != "t1" for s in r2)


def test_build_prompt_muestra_solo_texto_de_regla_y_ejemplos():
    fila = json.dumps({"id": "b1", "regla": "Usar return", "util": 3, "danina": 0})
    p = evaluate.build_prompt("Resolve: {enunciado}", [fila], {"enunciado": "x"}, "EJEMPLOS")
    assert "1. Usar return" in p and "util" not in p and "EJEMPLOS" in p
