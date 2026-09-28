"""Tests de la cadena anclada (chain_heads.json) + verify estricto (Fase 0, paso 2).

Todo con tmp_path + monkeypatch de ROOT/rutas: el repo real NUNCA se toca.

Modelo probado: chain init es el bootstrap puntual del ancla sobre historia que
ya existe (registra prefijos legacy sin sello); _chain_append mantiene el ancla
en cada escritura sellada (autoregistra archivos nuevos, ej: journals de run new).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402

TASK = "demo_chain"

CHECKS = '''"""Checks minimos para la task demo_chain (solo para tests de cadena)."""


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
    "id": "T-CH01",
    "task": TASK,
    "weight": 3,
    "frozen_at": "2026-09-22",
    "input": {"gate": "ok", "handle": "@test", "oferta": "programa"},
    "expected": {"decision": "califica", "score": 8.0, "truth_source": "verdad humana de test"},
}

LEGACY = [
    '{"ts": "2026-09-19", "tag": "a", "fact": "legacy 1"}',
    '{"ts": "2026-09-19", "tag": "b", "fact": "legacy 2"}',
]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Repo aislado: ROOT/MEMLOG/GOLDEN_DIR/RUNS sobre tmp_path."""
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.setattr(agent, "MEMLOG", tmp_path / "memory_log.jsonl")
    monkeypatch.setattr(agent, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    (tmp_path / "golden" / "checks").mkdir(parents=True)
    (tmp_path / "runs").mkdir(parents=True)
    return tmp_path


def _heads(repo):
    return json.loads((repo / "chain_heads.json").read_text(encoding="utf-8"))


def _lines(p):
    return [l for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def _rewrite(p, lines):
    Path(p).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _insert(p, idx, text):
    _rewrite(p, _lines(p)[:idx] + [text] + _lines(p)[idx:])


def _append_manual(p, fact, prev="GENESIS"):
    """Linea sellada valida escrita a mano (sin pasar por _chain_append -> no ancla)."""
    import hashlib as _h

    e = {"ts": "2026-09-23", "tag": "tag", "fact": fact, "prev": prev}
    e["hash"] = _h.sha256(agent._chain_canon({k: v for k, v in e.items() if k != "hash"})).hexdigest()
    with Path(p).open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _seed_legacy(p, lines=LEGACY):
    """Prefijo legacy sin sello (como las 4 lineas historicas de results.jsonl)."""
    _rewrite(p, list(lines))


def _anchored_chain(repo):
    """Cadena viva lista: legacy(2) + selladas(2), con chain_heads generado por init."""
    _seed_legacy(agent.MEMLOG)
    agent.cmd_memadd("tag", "sellada 1")
    agent.cmd_memadd("tag", "sellada 2")


# --- camino feliz ---------------------------------------------------------


def test_camino_feliz(repo):
    # bootstrap del ancla sobre prefijo legacy (n=0) y luego appends sellados
    _seed_legacy(agent.MEMLOG)
    assert agent.cmd_chain_init() == 0
    h = _heads(repo)["memory_log.jsonl"]
    assert h["n"] == 0 and h["legacy_n"] == 2 and h["head"] is None
    assert h["legacy_sha256"] and h["legacy_sha256"] != ""
    agent.cmd_memadd("tag", "sellada 1")
    agent.cmd_memadd("tag", "sellada 2")
    assert agent.cmd_verify("all") == 0
    h = _heads(repo)["memory_log.jsonl"]
    assert h["n"] == 2 and h["legacy_n"] == 2


def test_sin_ancla_es_warn_no_fail(repo):
    # lineas selladas escritas a mano (sin pasar por _chain_append) y sin ancla
    _seed_legacy(agent.MEMLOG)
    agent.cmd_memadd("tag", "sellada 1")
    (repo / "chain_heads.json").unlink()  # simula repo recien clonado
    assert agent.cmd_verify("all") == 0  # WARN, no FAIL
    assert agent.cmd_chain_init() == 0  # recupera el estado real (legacy 2 + sellada 1)
    assert _heads(repo)["memory_log.jsonl"]["n"] == 1


def test_chain_init_se_niega_si_existe(repo):
    _seed_legacy(agent.MEMLOG)
    assert agent.cmd_chain_init() == 0
    assert agent.cmd_chain_init() == 1
    assert agent.cmd_chain_init(force=True) == 0


def test_chain_init_se_niega_si_cadena_rota(repo):
    # lineas selladas a mano (no tocan el ancla) + legacy despues del genesis
    _seed_legacy(agent.MEMLOG)
    for i, fact in enumerate(("a", "b")):
        _append_manual(agent.MEMLOG, f"sellada {fact}", prev=agent._chain_prev(agent.MEMLOG) or "GENESIS")
    first_sealed = next(i for i, l in enumerate(_lines(agent.MEMLOG)) if '"hash"' in l)
    _insert(agent.MEMLOG, first_sealed + 1, '{"ts": "x", "tag": "y", "fact": "falso"}')
    assert agent.cmd_verify("all") == 1
    assert agent.cmd_chain_init() == 1
    assert not (repo / "chain_heads.json").exists()


# --- los 4 sabotajes del prompt -------------------------------------------


def test_inserta_linea_sin_hash_en_el_medio(repo):
    _anchored_chain(repo)
    assert agent.cmd_verify("all") == 0
    _insert(agent.MEMLOG, 1, '{"ts": "x", "tag": "y", "fact": "falso"}')
    assert agent.cmd_verify("all") == 1


def test_borra_la_ultima_linea(repo):
    _anchored_chain(repo)
    assert agent.cmd_verify("all") == 0
    _rewrite(agent.MEMLOG, _lines(agent.MEMLOG)[:-1])
    assert agent.cmd_verify("all") == 1


def test_edita_linea_legacy(repo):
    _anchored_chain(repo)
    lines = _lines(agent.MEMLOG)
    lines[0] = lines[0].replace("legacy 1", "LEGEDITADO 1")
    _rewrite(agent.MEMLOG, lines)
    assert agent.cmd_verify("all") == 1


def test_edita_campo_de_linea_sellada(repo):
    _anchored_chain(repo)
    lines = _lines(agent.MEMLOG)
    lines[2] = lines[2].replace("sellada 1", "SELLADA EDITADA")
    _rewrite(agent.MEMLOG, lines)
    assert agent.cmd_verify("all") == 1


# --- append sobre historia corrupta no escribe -----------------------------


def test_chain_append_no_escribe_sobre_cadena_rota(repo):
    agent.cmd_memadd("tag", "a")  # [sellada]
    _insert(agent.MEMLOG, 1, '{"ts": "x", "tag": "y", "fact": "falso"}')  # legacy despues del genesis
    antes = _lines(agent.MEMLOG)
    with pytest.raises(SystemExit):
        agent.cmd_memadd("tag", "no deberia entrar")
    assert _lines(agent.MEMLOG) == antes


# --- run new + paso: el journal queda anclado ------------------------------


def test_run_nuevo_queda_anclado(repo):
    agent.cmd_run_new("corrida de test", "1")
    rdir = agent._resolve_run(None)
    agent.cmd_run_step(rdir.name, "paso 1 ejecutado")
    h = _heads(repo)
    key = f"runs/{rdir.name}/journal.jsonl"
    assert key in h, f"journal nuevo sin anclar: {list(h)}"
    assert h[key]["n"] == 2
    assert agent.cmd_verify("all") == 0
    # sabotaje sobre el journal anclado tambien se detecta
    _rewrite(rdir / "journal.jsonl", _lines(rdir / "journal.jsonl")[:-1])
    assert agent.cmd_verify("runs") == 1


# --- golden diff dry_run no cambia chain_heads -----------------------------


@pytest.fixture
def golden_demo(repo):
    g = agent.GOLDEN_DIR
    (g / "checks" / f"{TASK}.py").write_text(CHECKS, encoding="utf-8")
    g.joinpath("golden.jsonl").write_text(json.dumps(GOLDEN_ROW) + "\n", encoding="utf-8")
    rub = repo / "rubric.json"
    rub.write_text(json.dumps(RUBRIC), encoding="utf-8")
    return rub


def _sheet(repo, score):
    p = repo / "scored.jsonl"
    p.write_text(json.dumps({"id": "T-CH01", "score": score, "dims": {"O": 2}}) + "\n", encoding="utf-8")
    return str(p)


def test_golden_diff_dry_run_no_toca_ancla(repo, golden_demo):
    _seed_legacy(agent.MEMLOG)
    assert agent.cmd_chain_init() == 0
    antes = (repo / "chain_heads.json").read_text(encoding="utf-8")
    rc = agent.cmd_golden_diff(_sheet(repo, 8.0), TASK, str(golden_demo), dry_run=True)
    assert rc == 0
    assert (repo / "chain_heads.json").read_text(encoding="utf-8") == antes
    assert not (agent.GOLDEN_DIR / "results.jsonl").exists()


def test_golden_diff_escribe_y_ancla(repo, golden_demo):
    # results.jsonl con prefijo legacy (como el repo real: 4 lineas historicas)
    _seed_legacy(agent.GOLDEN_DIR / "results.jsonl", lines=[l.replace("tag", "task") for l in LEGACY])
    assert agent.cmd_chain_init() == 0
    assert _heads(repo)["golden/results.jsonl"]["n"] == 0
    assert agent.cmd_golden_diff(_sheet(repo, 8.0), TASK, str(golden_demo)) == 0
    assert _heads(repo)["golden/results.jsonl"]["n"] == 1
    assert agent.cmd_verify("golden") == 0
    # la linea sellada no se puede editar
    p = agent.GOLDEN_DIR / "results.jsonl"
    lines = _lines(p)
    lines[-1] = lines[-1].replace('"raw_pass": 1', '"raw_pass": 99')
    _rewrite(p, lines)
    assert agent.cmd_verify("golden") == 1
