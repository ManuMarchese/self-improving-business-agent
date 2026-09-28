"""Tests del evaluador (motor/evaluate.py): tmp_path + monkeypatch, repo real intacto.

Cubre: bootstrap determinista, integridad exit 3 + seal, modo examen sin
inputs/salidas, ledger encadenado con verify OK, importar agent sin CLI.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
from motor import evaluate, llm  # noqa: E402

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


def _seal():
    return evaluate.cmd_seal(["--task", TASK])


def _eval(*extra):
    return evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "dev", *extra]
    )


def _results_lines(repo):
    out = []
    rdir = repo / "runs_motor"
    if rdir.exists():
        for f in sorted(rdir.glob("*-dev/results.jsonl")):
            out += [
                json.loads(l)
                for l in f.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
    return out


def _ledger_events(repo):
    p = repo / "evals" / "ledger.jsonl"
    if not p.exists():
        return []
    return [
        json.loads(l)
        for l in p.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


# --- bootstrap determinista ------------------------------------------------


def test_bootstrap_determinista():
    scores = [1.0, 0.0, 1.0, 0.5, 1.0, 0.0, 1.0]
    a = evaluate.bootstrap_ci(scores)
    b = evaluate.bootstrap_ci(list(scores))
    assert a == b
    assert a["lo"] <= a["mean"] <= a["hi"]
    assert a["lo"] >= 0.0 and a["hi"] <= 1.0


def test_bootstrap_vacio_y_n1():
    assert evaluate.bootstrap_ci([]) == {"mean": 0.0, "lo": 0.0, "hi": 0.0}
    one = evaluate.bootstrap_ci([0.75])
    assert one["mean"] == 0.75
    assert one["lo"] == one["hi"] == 0.75


def test_bootstrap_semilla_distinta_puede_cambiar():
    scores = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.5, 1.0]
    a = evaluate.bootstrap_ci(scores, n_boot=200, seed=1)
    b = evaluate.bootstrap_ci(scores, n_boot=200, seed=2)
    assert a["mean"] == b["mean"]  # la media no depende de la semilla
    # con pocas muestras los percentiles pueden diferir; al menos no truena
    assert a["lo"] <= a["hi"]


# --- integridad: grader modificado -> exit 3; seal -> vuelve a correr -------


def test_integridad_grader_modificado_exit3_luego_seal(repo):
    assert _seal() == 0
    assert _eval("--nonce", "i1") == 0

    g = repo / "tasks" / TASK / "grader.py"
    g.write_text(GRADER + "\n# sabotaje\n", encoding="utf-8")
    assert _eval("--nonce", "i2") == 3

    assert _seal() == 0
    assert _eval("--nonce", "i3") == 0


def test_integridad_sin_manifiesto_exit3(repo):
    assert _eval("--nonce", "m1") == 3


def test_integridad_dev_modificado_exit3(repo):
    assert _seal() == 0
    d = repo / "tasks" / TASK / "dev.jsonl"
    d.write_text(DEV + '{"id": "ECO-DX", "input": {"texto": "x"}, "expected": "X"}\n',
                 encoding="utf-8")
    assert _eval("--nonce", "d1") == 3


# --- camino feliz: results + resumen en ledger ------------------------------


def test_eval_camino_feliz(repo):
    assert _seal() == 0
    assert _eval("--nonce", "h1") == 0
    rows = _results_lines(repo)
    assert len(rows) == 2
    assert all(r["score"] == 1.0 and r["ok"] for r in rows)
    evs = _ledger_events(repo)
    evals = [e for e in evs if e.get("event") == "eval"]
    assert len(evals) == 1
    e = evals[0]
    assert e["n"] == 2 and e["mean"] == 1.0
    assert e["ci95_lo"] <= e["mean"] <= e["ci95_hi"]
    assert e["split"] == "dev"
    seals = [x for x in evs if x.get("event") == "seal"]
    assert len(seals) == 1


def test_split_train_revela_feedback_completo(repo):
    assert _seal() == 0
    assert evaluate.cmd_eval(
        ["--task", TASK, "--policy", "v0", "--split", "train", "--nonce", "t1"]
    ) == 0
    out = []
    for f in (repo / "runs_motor").glob("*-train/results.jsonl"):
        out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(out) == 3
    assert any("esperado=" in r["feedback"] for r in out)


def test_dev_feedback_no_contiene_esperado(repo):
    assert _seal() == 0
    assert _eval("--nonce", "f1") == 0
    for r in _results_lines(repo):
        esperado = {"ECO-D1": "GOLDEN SET CON VETO", "ECO-D2": "CADENA DE AUDITORIA"}[r["id"]]
        assert esperado not in r["feedback"]
        assert "esperado=" not in r["feedback"]


# --- modo examen: sin inputs ni salidas en ningun archivo generado ----------


def test_examen_no_guarda_inputs_ni_salidas(repo):
    assert _seal() == 0
    # el mock necesita conocer este input para que la eval devuelva score 1
    mock_file = repo / "mock.json"
    table = json.loads(mock_file.read_text(encoding="utf-8"))
    table["secretozumbo xylofono"] = "SECRETOZUMBO XYLOFONO"
    mock_file.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")

    ext = repo / "examen.jsonl"
    ext.write_text(
        json.dumps(
            {"id": "EX-1", "input": {"texto": "secretozumbo xylofono"},
             "expected": "SECRETOZUMBO XYLOFONO"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert _eval("--data", str(ext), "--nonce", "e1") == 0

    # unicos resultados del examen (ademas del seal previo en ledger)
    exam_dirs = [
        d for d in (repo / "runs_motor").iterdir() if d.is_dir() and "examen" in d.name
    ]
    assert len(exam_dirs) == 1
    blob = ""
    for d in exam_dirs:
        blob += (d / "results.jsonl").read_text(encoding="utf-8")
    blob += (repo / "evals" / "ledger.jsonl").read_text(encoding="utf-8")

    for forbidden in ("secretozumbo", "SECRETOZUMBO", "xylofono", "XYLOFONO"):
        assert forbidden not in blob, f"modo examen filtra {forbidden!r}"

    lines = (exam_dirs[0] / "results.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    assert set(row.keys()) == {"id", "score", "ok"}
    assert row["id"] == "EX-1" and row["score"] == 1.0 and row["ok"] is True

    # en el ledger, las rows del examen tampoco traen input/output/feedback
    exam_ev = [
        e for e in _ledger_events(repo)
        if e.get("event") == "eval" and e.get("examen")
    ]
    assert exam_ev and set(exam_ev[0]["rows"][0].keys()) == {"id", "score", "ok"}


# --- ledger encadenado + verify OK -----------------------------------------


def test_ledger_encadenado_y_verify_ok(repo):
    assert _seal() == 0
    assert _eval("--nonce", "v1") == 0
    assert agent.cmd_verify("evals") == 0
    assert agent.cmd_verify("all") == 0

    heads = json.loads((repo / "chain_heads.json").read_text(encoding="utf-8"))
    assert "evals/ledger.jsonl" in heads
    assert heads["evals/ledger.jsonl"]["n"] >= 2  # seal + eval

    # editar una linea del ledger rompe verify
    p = repo / "evals" / "ledger.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"event": "seal"', '"event": "SEAL-FALSIFICADO"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert agent.cmd_verify("evals") == 1


# --- importar agent.py no ejecuta el CLI -----------------------------------


def test_import_agent_no_ejecuta_cli():
    proc = subprocess.run(
        [sys.executable, "-c", "import agent; print('IMPORT_OK')"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "IMPORT_OK"
    assert "Uso:" not in proc.stdout
    assert "Comando desconocido" not in proc.stdout


# --- seal deja linea con hashes viejos y nuevos -----------------------------


def test_seal_registra_hashes_viejos_y_nuevos(repo):
    assert _seal() == 0
    assert _seal() == 0  # segundo seal: old != None
    seals = [e for e in _ledger_events(repo) if e.get("event") == "seal"]
    assert len(seals) == 2
    assert seals[0]["old"] is None
    assert seals[1]["old"] == seals[0]["new"]
    assert set(seals[1]["new"].keys()) == {"grader.py", "dev.jsonl", "task.json"}
    # el manifiesto coincide con disco
    ok, errs = evaluate.check_integrity(TASK)
    assert ok and not errs


# --- normalizacion CRLF/LF en el hash del manifiesto (PASO 5-bis) -----------


def test_hash_normalizado_mismo_para_crlf_y_lf(tmp_path):
    lf = tmp_path / "con_lf.txt"
    crlf = tmp_path / "con_crlf.txt"
    lf.write_bytes(b"uno\ndos\n tres\n")
    crlf.write_bytes(b"uno\r\ndos\r\n tres\r\n")
    h_lf = evaluate._hash_normalizado(lf)
    h_crlf = evaluate._hash_normalizado(crlf)
    assert h_lf == h_crlf
    assert h_lf == hashlib.sha256(b"uno\ndos\n tres\n").hexdigest()


def test_eval_tarea_convertida_a_lf_exit0(repo):
    """Seal con working copy CRLF (Windows/autocrlf) + copia limpia LF (git
    archive / CI Linux) -> eval sigue en exit 0. Repo real intacto."""
    tdir = repo / "tasks" / TASK
    # 1) working copy Windows: todo CRLF
    for p in tdir.iterdir():
        if p.is_file():
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert _seal() == 0
    # 2) copia limpia: todo LF de nuevo
    for p in tdir.iterdir():
        if p.is_file():
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
    assert _eval("--nonce", "lf1") == 0


# --- errores del proveedor LLM (timeout/504/cuota) no son "el modelo no sabe" ---


def _llm_que_falla(fallar_ids):
    """complete() que tira LLMError para los prompts que contienen alguno de los ids."""
    real = llm.complete

    def fake(prompt, **kw):
        if any(t in prompt for t in fallar_ids):
            raise RuntimeError("504 A Timeout Occurred (simulado)")
        return real(prompt, **kw)

    return fake


def _dev_textos(repo):
    rows = [json.loads(l) for l in (repo / "tasks" / TASK / "dev.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [json.dumps(r["input"], ensure_ascii=False, sort_keys=True) for r in rows]


def test_todos_errores_proveedor_eval_invalida_exit4(repo, monkeypatch):
    assert _seal() == 0
    monkeypatch.setattr(llm, "complete", _llm_que_falla(["Input:"]))
    assert _eval("--nonce", "p1") == evaluate.EXIT_PROVEEDOR
    evs = _ledger_events(repo)
    assert not [e for e in evs if e.get("event") == "eval"]
    inval = [e for e in evs if e.get("event") == "eval_invalida"]
    assert len(inval) == 1
    assert inval[0]["errores_llm"] == inval[0]["n"] and inval[0]["n_validos"] == 0


def test_error_proveedor_se_excluye_del_puntaje(repo, monkeypatch):
    # 1 de 2 falla (50% > 10%) -> invalida, pero el ejemplo con error NO cuenta como 0.
    assert _seal() == 0
    segundo = _dev_textos(repo)[1]
    monkeypatch.setattr(llm, "_MAX_PAR", 1, raising=False)  # en serie: el 1ro pasa, el 2do falla
    monkeypatch.setattr(llm, "complete", _llm_que_falla([segundo]))
    assert _eval("--nonce", "p2") == evaluate.EXIT_PROVEEDOR
    e = [x for x in _ledger_events(repo) if x.get("event") == "eval_invalida"][0]
    assert e["n_validos"] == 1 and e["errores_llm"] == 1
    assert e["mean"] == 1.0  # el unico valido paso; el error no arrastra el promedio
    rows = _results_lines(repo)
    assert sum(1 for r in rows if r.get("llm_error")) == 1


def test_sin_errores_proveedor_sigue_siendo_eval(repo):
    assert _seal() == 0
    assert _eval("--nonce", "p3") == 0
    e = [x for x in _ledger_events(repo) if x.get("event") == "eval"][0]
    assert e["errores_llm"] == 0 and e["n_validos"] == e["n"]


def test_cortacircuito_deja_de_llamar_al_proveedor(repo, monkeypatch):
    # Con 2 ejemplos el limite es 0: tras el primer fallo no se llama mas.
    # F2.0: el probe pasa (si no, exit 4 sin resultados); falla el resto.
    assert _seal() == 0
    llamadas = []

    def probe_ok_resto_falla(prompt, **kw):
        llamadas.append(1)
        if prompt == evaluate.PROBE_PROMPT:
            return {"text": "OK", "tokens_in": 0, "tokens_out": 0,
                    "model": "probe", "latency_s": 0.0, "cached": False}
        raise RuntimeError("504 simulado")

    monkeypatch.setattr(llm, "_MAX_PAR", 1, raising=False)
    monkeypatch.setattr(llm, "complete", probe_ok_resto_falla)
    assert _eval("--nonce", "p4") == evaluate.EXIT_PROVEEDOR
    assert len(llamadas) == 2  # probe + 1er ejemplo (el 2do lo corta el circuito)
    rows = _results_lines(repo)
    assert any("cortacircuito" in r["feedback"] for r in rows)
