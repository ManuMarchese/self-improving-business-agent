"""Tests de la comparacion pareada (motor/compare.py): tmp_path, sin LLM.

Cubre: identica -> RECHAZA; mejora neta -> ACEPTA; mejora con regresiones ->
RECHAZA por regresiones; ids no compartidos -> exit 1; llm_error se excluye;
bootstrap determinista; evento "compare" en el ledger.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
from motor import compare  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Ledger aislado: el repo real queda intacto."""
    monkeypatch.setattr(compare, "EVALS_DIR", tmp_path / "evals")
    monkeypatch.setattr(compare, "LEDGER", tmp_path / "evals" / "ledger.jsonl")
    monkeypatch.setattr(compare, "_ROOT", tmp_path)
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.setattr(agent, "MEMLOG", tmp_path / "memory_log.jsonl")
    monkeypatch.setattr(agent, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    (tmp_path / "golden").mkdir(exist_ok=True)
    (tmp_path / "runs").mkdir(exist_ok=True)
    return tmp_path


def _escribir(path, filas):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    return str(path)


def _fila(i, score, ok=True, error=False):
    return {"id": f"ej-{i:03d}", "score": score, "ok": ok, "llm_error": error}


def _ledger_events(repo):
    p = repo / "evals" / "ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_identica_rechaza_ic_incluye_cero(repo):
    filas = [_fila(i, 1.0 if i % 3 else 0.0, ok=bool(i % 3)) for i in range(30)]
    a = _escribir(repo / "a.jsonl", filas)
    b = _escribir(repo / "b.jsonl", [dict(f) for f in filas])
    assert compare.cmd_compare(["--a", a, "--b", b]) == 2
    evs = _ledger_events(repo)
    assert len(evs) == 1 and evs[0]["event"] == "compare"
    assert evs[0]["decision"] == "RECHAZA"
    assert evs[0]["n"] == 30 and evs[0]["media_d"] == 0.0
    assert evs[0]["ic"] == [0.0, 0.0]
    assert "IC95%" in evs[0]["motivo"]


def test_mejora_neta_acepta(repo):
    # 10 ids que A falla y B pasa, resto igual: 10 mejoras, 0 regresiones.
    fa = [_fila(i, 0.0, ok=False) for i in range(10)]
    fb = [_fila(i, 1.0, ok=True) for i in range(10)]
    fa += [_fila(i, 1.0, ok=True) for i in range(10, 30)]
    fb += [_fila(i, 1.0, ok=True) for i in range(10, 30)]
    a = _escribir(repo / "a.jsonl", fa)
    b = _escribir(repo / "b.jsonl", fb)
    assert compare.cmd_compare(["--a", a, "--b", b]) == 0
    evs = _ledger_events(repo)
    assert evs[0]["decision"] == "ACEPTA"
    assert evs[0]["mejoras"] == 10 and evs[0]["regresiones"] == 0
    assert evs[0]["ic"][0] > 0  # IC95% inferior > 0


def test_mejora_con_regresiones_rechaza(repo):
    # 6 mejoras y 4 regresiones: 4 > 6/2 -> RECHAZA por regresiones.
    fa, fb = [], []
    for i in range(6):  # A falla, B pasa
        fa.append(_fila(i, 0.0, ok=False))
        fb.append(_fila(i, 1.0, ok=True))
    for i in range(6, 10):  # A pasa, B falla
        fa.append(_fila(i, 1.0, ok=True))
        fb.append(_fila(i, 0.0, ok=False))
    for i in range(10, 30):  # empates
        fa.append(_fila(i, 1.0, ok=True))
        fb.append(_fila(i, 1.0, ok=True))
    a = _escribir(repo / "a.jsonl", fa)
    b = _escribir(repo / "b.jsonl", fb)
    assert compare.cmd_compare(["--a", a, "--b", b]) == 2
    evs = _ledger_events(repo)
    assert evs[0]["decision"] == "RECHAZA"
    assert evs[0]["mejoras"] == 6 and evs[0]["regresiones"] == 4
    assert "regresiones" in evs[0]["motivo"]


def test_ids_no_compartidos_exit1(repo):
    a = _escribir(repo / "a.jsonl", [_fila(i, 1.0) for i in range(20)])
    b = _escribir(repo / "b.jsonl", [_fila(i + 100, 1.0) for i in range(20)])
    assert compare.cmd_compare(["--a", a, "--b", b]) == 1
    assert _ledger_events(repo) == []  # error: nada en el ledger


def test_cobertura_parcial_bajo_90_exit1(repo):
    a = _escribir(repo / "a.jsonl", [_fila(i, 1.0) for i in range(20)])
    # solo 10/20 compartidos = 50% < 90%
    b = _escribir(repo / "b.jsonl",
                  [_fila(i, 1.0) for i in range(10)] + [_fila(i + 100, 1.0) for i in range(10)])
    assert compare.cmd_compare(["--a", a, "--b", b]) == 1


def test_llm_error_se_excluye(repo):
    fa = [_fila(i, 1.0, ok=True) for i in range(10)]
    fb = [dict(f) for f in fa]
    fa[0]["llm_error"] = True  # error en A
    fb[1]["llm_error"] = True  # error en B
    a = _escribir(repo / "a.jsonl", fa)
    b = _escribir(repo / "b.jsonl", fb)
    assert compare.cmd_compare(["--a", a, "--b", b]) == 2  # identicas en lo usable
    evs = _ledger_events(repo)
    assert evs[0]["n"] == 8  # 10 compartidos - 2 con error


def test_bootstrap_determinista():
    diffs = [1.0] * 6 + [0.0] * 20 + [-1.0] * 4
    a = compare.paired_bootstrap_ci(diffs)
    b = compare.paired_bootstrap_ci(list(diffs))
    assert a == b
    assert a["lo"] <= a["mean"] <= a["hi"]


def test_json_imprime_dict(repo, capsys):
    filas = [_fila(i, 1.0 if i % 3 else 0.0, ok=bool(i % 3)) for i in range(30)]
    a = _escribir(repo / "a.jsonl", filas)
    b = _escribir(repo / "b.jsonl", [dict(f) for f in filas])
    assert compare.cmd_compare(["--a", a, "--b", b, "--json"]) == 2
    out = capsys.readouterr().out
    linea_json = [l for l in out.splitlines() if l.startswith("{")]
    assert linea_json
    d = json.loads(linea_json[-1])
    assert d["decision"] == "RECHAZA" and d["n"] == 30
