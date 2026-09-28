"""Tests F2.0 (motor robusto): probe, guardia de repo, anti-__eq__, timeout/intentos.

Todo con tmp_path/mock, nada real: no se toca el repo ni la red.
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
from motor import evaluate, llm, sandbox_py  # noqa: E402

TASK = "eco_mock"

GRADER = (ROOT / "tasks" / "eco_mock" / "grader.py").read_text(encoding="utf-8")
TASK_JSON = (ROOT / "tasks" / "eco_mock" / "task.json").read_text(encoding="utf-8")
TRAIN = (ROOT / "tasks" / "eco_mock" / "train.jsonl").read_text(encoding="utf-8")
DEV = (ROOT / "tasks" / "eco_mock" / "dev.jsonl").read_text(encoding="utf-8")
PROMPT = (ROOT / "policies" / "eco_mock" / "v0" / "prompt.md").read_text(encoding="utf-8")
POLICY_JSON = (ROOT / "policies" / "eco_mock" / "v0" / "policy.json").read_text(encoding="utf-8")
MOCK = (ROOT / "tasks" / "eco_mock" / "mock.json").read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Repo aislado con tasks/ + policies/ + rutas del evaluador y de la cadena."""
    tdir = tmp_path / "tasks" / TASK
    tdir.mkdir(parents=True)
    (tdir / "grader.py").write_text(GRADER, encoding="utf-8")
    (tdir / "task.json").write_text(TASK_JSON, encoding="utf-8")
    (tdir / "train.jsonl").write_text(TRAIN, encoding="utf-8")
    (tdir / "dev.jsonl").write_text(DEV, encoding="utf-8")
    pdir = tmp_path / "policies" / TASK / "v0"
    pdir.mkdir(parents=True)
    (pdir / "prompt.md").write_text(PROMPT, encoding="utf-8")
    (pdir / "policy.json").write_text(POLICY_JSON, encoding="utf-8")
    (pdir / "playbook.jsonl").write_text("", encoding="utf-8")
    mock_file = tmp_path / "mock.json"
    mock_file.write_text(MOCK, encoding="utf-8")

    monkeypatch.setattr(evaluate, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(evaluate, "POLICIES_DIR", tmp_path / "policies")
    monkeypatch.setattr(evaluate, "RUNS_MOTOR", tmp_path / "runs_motor")
    monkeypatch.setattr(evaluate, "EVALS_DIR", tmp_path / "evals")
    monkeypatch.setattr(evaluate, "LEDGER", tmp_path / "evals" / "ledger.jsonl")
    monkeypatch.setattr(evaluate, "MANIFEST", tmp_path / "evals" / "MANIFEST.json")
    monkeypatch.setattr(evaluate, "_ROOT", tmp_path)

    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.setattr(agent, "MEMLOG", tmp_path / "memory_log.jsonl")
    monkeypatch.setattr(agent, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    (tmp_path / "golden").mkdir(exist_ok=True)
    (tmp_path / "runs").mkdir(exist_ok=True)

    cache = tmp_path / "cache"
    monkeypatch.setattr(llm, "_CACHE_ROOT", cache)
    monkeypatch.setattr(llm, "_CACHE_DIR", cache / "llm")
    monkeypatch.setattr(llm, "_USAGE_FILE", cache / "llm_usage.jsonl")
    monkeypatch.setenv("MOTOR_LLM", "mock")
    monkeypatch.setenv("MOTOR_MOCK_FILE", str(mock_file))
    return tmp_path


def _ledger_events(repo):
    p = repo / "evals" / "ledger.jsonl"
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- probe previo: falla -> exit 4 sin llamar al resto -----------------------


def test_probe_fallido_exit4_sin_correr(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    llamadas = []

    def siempre_falla(prompt, **kw):
        llamadas.append(prompt)
        raise RuntimeError("504 A Timeout Occurred (simulado)")

    monkeypatch.setattr(llm, "complete", siempre_falla)
    rc = evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "dev", "--nonce", "probe1"]
    )
    assert rc == evaluate.EXIT_PROVEEDOR == 4
    assert len(llamadas) == 1  # solo el probe, el resto no se llamo
    assert llamadas[0] == evaluate.PROBE_PROMPT
    evs = _ledger_events(repo)
    assert not [e for e in evs if e.get("event") == "eval"]
    inval = [e for e in evs if e.get("event") == "eval_invalida"]
    assert len(inval) == 1
    assert "proveedor caido (probe)" in inval[0].get("motivo", "")
    assert not list((repo / "runs_motor").glob("*/results.jsonl"))


def test_probe_ok_sigue_corriendo(repo):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    # mock sin entrada para el probe: devuelve "" sin excepcion -> el probe pasa
    assert evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "dev", "--nonce", "probe2"]
    ) == 0
    evs = _ledger_events(repo)
    assert [e for e in evs if e.get("event") == "eval"]


# --- guardia de repo: archivo nuevo -> exit 5 + cuarentena --------------------


def test_guardia_detecta_archivo_nuevo_exit5_y_cuarentena(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    intruso = repo / "hack_llm.py"
    intruso.write_text("def f():\n    return 1\n", encoding="utf-8")
    respuestas = [set(), {"hack_llm.py"}]
    monkeypatch.setattr(evaluate, "_git_untracked", lambda: respuestas.pop(0))
    rc = evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "dev", "--nonce", "guard1"]
    )
    assert rc == evaluate.EXIT_REPO == 5
    evs = _ledger_events(repo)
    assert not [e for e in evs if e.get("event") == "eval"]
    inval = [e for e in evs if e.get("event") == "eval_invalida"]
    assert len(inval) == 1
    assert "el LLM toco el repo" in inval[0].get("motivo", "")
    assert "hack_llm.py" in inval[0].get("motivo", "")
    assert not intruso.exists()  # movido, no borrado
    movidos = list((repo / ".cache" / "cuarentena").rglob("hack_llm.py"))
    assert len(movidos) == 1
    assert movidos[0].read_text(encoding="utf-8") == "def f():\n    return 1\n"


def test_guardia_sin_novedades_exit0(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    monkeypatch.setattr(evaluate, "_git_untracked", lambda: set())
    assert evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "dev", "--nonce", "guard2"]
    ) == 0
    evs = _ledger_events(repo)
    assert [e for e in evs if e.get("event") == "eval"]


def test_guardia_ignora_runs_motor_y_evals(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    respuestas = [
        set(),
        {"runs_motor/20260923-x/results.jsonl", "evals/ledger.jsonl"},
    ]
    monkeypatch.setattr(evaluate, "_git_untracked", lambda: respuestas.pop(0))
    assert evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "dev", "--nonce", "guard3"]
    ) == 0


# --- sandbox: dunders de comparacion bloqueados --------------------------------


@pytest.mark.parametrize("dunder", ["__eq__", "__ne__", "__hash__", "__class__"])
def test_sandbox_bloquea_dunder_de_comparacion(dunder):
    codigo = (
        "class Truco:\n"
        f"    def {dunder}(self, otro):\n"
        "        return True\n"
        "def f(x):\n"
        "    return Truco()\n"
    )
    res = sandbox_py.ejecutar(codigo, ["assert f(1) == 1"])
    assert res["estado"] == "bloqueado"
    assert res["pasan"] == 0
    assert "dunder de comparacion" in res["detalle"]


def test_sandbox_trampa_eq_siempre_true_bloqueada():
    codigo = (
        "class SiempreIgual:\n"
        "    def __eq__(self, otro):\n"
        "        return True\n"
        "def doble(n):\n"
        "    return SiempreIgual()\n"
    )
    res = sandbox_py.ejecutar(
        codigo, ["assert doble(2) == 4", "assert doble(0) == 0"]
    )
    assert res["estado"] == "bloqueado"
    assert "dunder de comparacion" in res["detalle"]


def test_sandbox_no_falla_en_codigo_limpio_con_palabras_parecidas():
    res = sandbox_py.ejecutar(
        "def equal(x):\n    return x\ndef klass():\n    return 1",
        ["assert equal(2) == 2", "assert klass() == 1"],
    )
    assert res["estado"] == "ok"
    assert res["pasan"] == 2


# --- timeout e intentos leidos de config --------------------------------------


def test_timeout_default_90_y_configurable(monkeypatch):
    monkeypatch.delenv("MOTOR_TIMEOUT", raising=False)
    assert llm._timeout_default() == 90
    monkeypatch.setenv("MOTOR_TIMEOUT", "11")
    assert llm._timeout_default() == 11
    monkeypatch.setenv("MOTOR_TIMEOUT", "basura")
    assert llm._timeout_default() == 90


def test_intentos_default_3_y_configurable(monkeypatch):
    monkeypatch.delenv("MOTOR_INTENTOS", raising=False)
    assert llm._intentos_motor() == 3
    monkeypatch.setenv("MOTOR_INTENTOS", "5")
    assert llm._intentos_motor() == 5
    monkeypatch.setenv("MOTOR_INTENTOS", "basura")
    assert llm._intentos_motor() == 3


def test_complete_usa_timeout_de_env_y_respeta_explicito(tmp_path, monkeypatch):
    vistos = []

    def fake_run_once(model, message, timeout):
        vistos.append(timeout)
        return {"text": "ok", "tokens_in": 1, "tokens_out": 1}

    cache = tmp_path / "c1"
    monkeypatch.setattr(llm, "_CACHE_DIR", cache / "llm")
    monkeypatch.setattr(llm, "_CACHE_ROOT", cache)
    monkeypatch.setattr(llm, "_USAGE_FILE", cache / "llm_usage.jsonl")
    monkeypatch.setattr(llm, "_run_once", fake_run_once)
    monkeypatch.setattr("retry_policy.time.sleep", lambda *a, **k: None)
    monkeypatch.setenv("MOTOR_TIMEOUT", "11")
    llm.complete("hola", rol="rapido", nonce="t-env")
    assert vistos[-1] == 11
    llm.complete("hola", rol="rapido", timeout=22, nonce="t-exp")
    assert vistos[-1] == 22


def test_real_complete_usa_3_intentos_por_candidato(tmp_path, monkeypatch):
    usados = []

    def fake_con_reintentos(fn, intentos=5, dormir=True):
        usados.append(intentos)
        return fn()

    cache = tmp_path / "c2"
    monkeypatch.setattr(llm, "_CACHE_DIR", cache / "llm")
    monkeypatch.setattr(llm, "_CACHE_ROOT", cache)
    monkeypatch.setattr(llm, "_USAGE_FILE", cache / "llm_usage.jsonl")
    monkeypatch.setattr(llm, "con_reintentos", fake_con_reintentos)
    monkeypatch.setattr(
        llm, "_run_once", lambda m, msg, t: {"text": "ok", "tokens_in": 0, "tokens_out": 0}
    )
    monkeypatch.delenv("MOTOR_INTENTOS", raising=False)
    llm.complete("hola", rol="rapido", nonce="t-int")
    assert usados and all(u == 3 for u in usados)
    with mock.patch.dict("os.environ", {"MOTOR_INTENTOS": "2"}):
        llm.complete("hola", rol="rapido", nonce="t-int2")
    assert usados[-1] == 2


def test_guardia_rutas_relativas_a_la_raiz_del_motor(tmp_path, monkeypatch):
    """Regresion: con el motor en una SUBcarpeta del repo git (ej. skeleton/),
    las rutas nuevas deben ser relativas a esa carpeta (no 'skeleton/...')."""
    import subprocess
    from motor import evaluate
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git no disponible")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sub = tmp_path / "skeleton"
    sub.mkdir()
    monkeypatch.setattr(evaluate, "_ROOT", sub)
    antes = evaluate._git_untracked()
    (sub / "runs_motor").mkdir()
    (sub / "runs_motor" / "r.jsonl").write_text("{}", encoding="utf-8")
    (sub / "intruso.py").write_text("x", encoding="utf-8")
    nuevos = evaluate._fuera_de_zonas(evaluate._git_untracked() - antes)
    assert nuevos == {"intruso.py"}
