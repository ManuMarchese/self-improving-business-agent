"""Tests del ciclo ACE (motor/learn.py): todo con MOTOR_LLM=mock y tmp_path.

Garantias:
  - El prompt del reflector NO contiene ningun id, enunciado ni assert de dev.
  - Regla con nombre de funcion del lote o literal de assert se descarta.
  - Casi-duplicado se descarta; poda y tope funcionan.
  - Hijo que el mock hace mejor en dev -> ACEPTA y ACTIVA cambia;
    hijo igual -> RECHAZA y ACTIVA no cambia.
  - Eval invalida -> no acepta.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
from motor import compare, evaluate, learn, llm  # noqa: E402

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

    for mod in (evaluate, learn):
        monkeypatch.setattr(mod, "TASKS_DIR", tmp_path / "tasks")
        monkeypatch.setattr(mod, "POLICIES_DIR", tmp_path / "policies")
        monkeypatch.setattr(mod, "RUNS_MOTOR", tmp_path / "runs_motor")
        monkeypatch.setattr(mod, "EVALS_DIR", tmp_path / "evals")
        monkeypatch.setattr(mod, "LEDGER", tmp_path / "evals" / "ledger.jsonl")
        monkeypatch.setattr(mod, "MANIFEST", tmp_path / "evals" / "MANIFEST.json")
        monkeypatch.setattr(mod, "_ROOT", tmp_path)
    monkeypatch.setattr(compare, "EVALS_DIR", tmp_path / "evals")
    monkeypatch.setattr(compare, "LEDGER", tmp_path / "evals" / "ledger.jsonl")
    monkeypatch.setattr(compare, "_ROOT", tmp_path)
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


def _ledger(repo):
    p = repo / "evals" / "ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- 1. Prompt del reflector sin nada de dev ---------------------------------

def test_reflector_prompt_no_contiene_dev():
    train_fallidos = [{
        "input": {"texto": "TRAIN_UNICO_hola"},
        "output": "salida train x",
        "feedback": "esperado='TRAIN_UNICO_HOLA' obtenido='x'",
    }]
    train_pasados = [{
        "input": {"texto": "TRAIN_OTRO_mundo"},
        "output": "TRAIN_OTRO_MUNDO",
        "feedback": "ok",
    }]
    playbook = [{"id": "b1", "regla": "Regla general de formato.", "util": 0, "danina": 0}]
    prompt = learn.build_reflector_prompt(playbook, train_fallidos, train_pasados)
    # Marcadores de dev que JAMAS deben aparecer (ids, enunciados, asserts).
    for prohibido in (
        "ECO-D1", "ECO-D2",
        "golden set con veto", "cadena de auditoria",
        "GOLDEN SET CON VETO", "CADENA DE AUDITORIA",
        "assert first_repeated_char", "mbpp-602",
    ):
        assert prohibido not in prompt, f"prompt del reflector filtra dev: {prohibido!r}"
    # Y si contiene train (es lo correcto: solo train).
    assert "TRAIN_UNICO_hola" in prompt


# --- 2. Filtro anti-memorizacion ----------------------------------------------

def test_filtro_descarta_nombre_funcion_y_literal():
    nombres = learn.extraer_nombres_funcion([
        'assert mi_funcion_unica_xyz("abc") == "a"',
        "assert otra_fn(13) == True",
    ])
    assert "mi_funcion_unica_xyz" in nombres
    lits = learn.extraer_literales(['assert otra_fn(12345) == [1, 2, 3]'])
    assert "12345" in lits

    existentes = []
    propuestas = [
        {"regla": "Usar mi_funcion_unica_xyz siempre que se pueda.", "por_que": "x"},
        {"regla": "Si ves 12345 devolve [1, 2, 3] directamente.", "por_que": "y"},
        {"regla": "Responder solo con un bloque python valido y simple.", "por_que": "z"},
    ]
    nuevo, agregadas, rechazadas, _ = learn.curar_playbook(
        existentes, propuestas, [], [], nombres, lits,
        origen="test", creado="t",
    )
    motivos = " | ".join(r["motivo"] for r in rechazadas)
    assert len(agregadas) == 1 and agregadas[0]["regla"].startswith("Responder solo")
    assert len(rechazadas) == 2
    assert "funcion del lote" in motivos
    assert "literal de assert" in motivos
    assert len(nuevo) == 1


def test_filtro_descarta_mbpp_id():
    nuevo, agregadas, rechazadas, _ = learn.curar_playbook(
        [], [{"regla": "Para mbpp-602 usar un loop especial.", "por_que": ""}],
        [], [], set(), set(), origen="test", creado="t",
    )
    assert agregadas == [] and len(nuevo) == 0
    assert len(rechazadas) == 1 and "mbpp-" in rechazadas[0]["motivo"]


# --- 3. Duplicados, poda y tope ------------------------------------------------

def test_casi_duplicado_se_descarta():
    existentes = [{"id": "b1", "regla": "Escribir funciones simples y claras.",
                   "util": 0, "danina": 0, "origen": "t", "creado": "t"}]
    propuestas = [{"regla": "Escribir funciones simples y claras!", "por_que": ""}]
    nuevo, agregadas, rechazadas, _ = learn.curar_playbook(
        existentes, propuestas, [], [], set(), set(), origen="t", creado="t")
    assert agregadas == []
    assert len(rechazadas) == 1 and "duplicado" in rechazadas[0]["motivo"]
    assert len(nuevo) == 1


def test_poda_danina_ge_util_mas_2():
    existentes = [
        {"id": "b1", "regla": "Regla mala.", "util": 0, "danina": 2,
         "origen": "t", "creado": "t"},
        {"id": "b2", "regla": "Regla buena.", "util": 3, "danina": 0,
         "origen": "t", "creado": "t"},
    ]
    nuevo, _, _, podadas = learn.curar_playbook(
        existentes, [], [], [], set(), set(), origen="t", creado="t")
    assert "b1" in podadas and "b2" not in podadas
    assert [r["id"] for r in nuevo] == ["b2"]


def test_tope_25_saca_peor_util_menos_danina():
    existentes = [
        {"id": f"b{i + 1}", "regla": f"Regla general numero {i + 1} sobre formato.",
         "util": 5 if i < 25 else 0, "danina": 0, "origen": "t", "creado": "t"}
        for i in range(25)
    ]
    propuestas = [{"regla": "Una regla nueva valida sobre tipos simples.", "por_que": ""}]
    nuevo, agregadas, _, podadas = learn.curar_playbook(
        existentes, propuestas, [], [], set(), set(), origen="t", creado="t")
    assert len(nuevo) == 25
    assert len(agregadas) == 1
    # La nueva (0-0) es peor que las 25 con util=5: el tope la puede podar;
    # si la poda, queda fuera; si no, alguna vieja no deberia salir porque
    # todas las viejas son mejores. En ambos casos el tope se cumple.
    assert len(podadas) >= 1


def test_contadores_util_danina_y_json_invalido_reintenta(monkeypatch):
    existentes = [{"id": "b1", "regla": "Regla base de formato.",
                   "util": 0, "danina": 0, "origen": "t", "creado": "t"}]
    nuevo, _, _, _ = learn.curar_playbook(
        existentes, [], ["b1"], ["bX-inexistente"], set(), set(),
        origen="t", creado="t")
    assert nuevo[0]["util"] == 1 and nuevo[0]["danina"] == 0
    # parse: texto con basura + JSON valido dentro se rescata
    ok = learn.parse_reflector_json('hola {"nuevas":[],"utiles":["b1"],"daninas":[]} adios')
    assert ok is not None and ok["utiles"] == ["b1"]
    assert learn.parse_reflector_json("esto no es json { roto") is None


# --- 4-5. Juez pareado + eval invalida (end-to-end con eval falsa) -------------

def _fake_eval_factory(repo, modo):
    """eval falsa: train plausible; dev segun modo (mejor/igual/invalida)."""
    import re as _re

    def fake(args):
        flags = {}
        i = 0
        while i < len(args):
            if args[i].startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[args[i][2:]] = args[i + 1]
                i += 2
            elif args[i].startswith("--"):
                flags[args[i][2:]] = True
                i += 1
            else:
                i += 1
        task, policy, split = flags.get("task"), flags.get("policy"), flags.get("split")
        nonce = flags.get("nonce", "n")
        if modo == "invalida" and split == "dev":
            agent._chain_append(repo / "evals" / "ledger.jsonl", {
                "ts": "2026-09-23T00:00:00+00:00", "event": "eval_invalida",
                "task": task, "policy": policy, "split": split, "nonce": nonce,
                "n": 2, "n_validos": 0, "errores_llm": 2, "mean": 0.0,
                "motivo": "proveedor caido (simulado)",
            })
            return evaluate.EXIT_PROVEEDOR
        stamp = f"20990101-000000-{task}-{policy}-{split}-{nonce}"
        out_dir = repo / "runs_motor" / stamp
        out_dir.mkdir(parents=True, exist_ok=True)
        if split == "train":
            rows = [
                {"id": "ECO-T1", "input": {"texto": "hola mundo"}, "output": "mal",
                 "score": 0.0, "ok": False, "llm_error": False,
                 "feedback": "esperado='HOLA MUNDO' obtenido='mal'",
                 "model": "mock", "tokens_in": 0, "tokens_out": 0, "segundos": 0.0},
                {"id": "ECO-T2", "input": {"texto": "OpenCode es genial"},
                 "output": "OPENCODE ES GENIAL", "score": 1.0, "ok": True,
                 "llm_error": False, "feedback": "ok",
                 "model": "mock", "tokens_in": 0, "tokens_out": 0, "segundos": 0.0},
            ]
            mean = 0.5
        else:
            if modo == "mejor":
                bueno = (policy != "v0")  # el hijo (v1+) pasa todo, el padre falla todo
                rows = [
                    {"id": "ECO-D1", "input": {"texto": "a"},
                     "output": "A" if bueno else "mal",
                     "score": 1.0 if bueno else 0.0, "ok": bueno,
                     "llm_error": False, "feedback": "ok" if bueno else "falla",
                     "model": "mock", "tokens_in": 0, "tokens_out": 0, "segundos": 0.0},
                    {"id": "ECO-D2", "input": {"texto": "b"},
                     "output": "B" if bueno else "mal",
                     "score": 1.0 if bueno else 0.0, "ok": bueno,
                     "llm_error": False, "feedback": "ok" if bueno else "falla",
                     "model": "mock", "tokens_in": 0, "tokens_out": 0, "segundos": 0.0},
                ]
                mean = 1.0 if bueno else 0.0
            else:  # igual
                rows = [
                    {"id": "ECO-D1", "input": {"texto": "a"}, "output": "A",
                     "score": 1.0, "ok": True, "llm_error": False, "feedback": "ok",
                     "model": "mock", "tokens_in": 0, "tokens_out": 0, "segundos": 0.0},
                    {"id": "ECO-D2", "input": {"texto": "b"}, "output": "B",
                     "score": 1.0, "ok": True, "llm_error": False, "feedback": "ok",
                     "model": "mock", "tokens_in": 0, "tokens_out": 0, "segundos": 0.0},
                ]
                mean = 1.0
        (out_dir / "results.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        rel = str((out_dir / "results.jsonl").relative_to(repo)).replace("\\", "/")
        agent._chain_append(repo / "evals" / "ledger.jsonl", {
            "ts": "2026-09-23T00:00:00+00:00", "event": "eval",
            "task": task, "policy": policy, "split": split, "nonce": nonce,
            "n": len(rows), "n_validos": len(rows), "errores_llm": 0,
            "mean": mean, "ci95_lo": mean, "ci95_hi": mean,
            "fallas": sum(1 for r in rows if not r["ok"]),
            "results": rel, "revelar": split == "train",
        })
        return evaluate.EXIT_OK

    return fake


def _fake_reflector(respuesta):
    def fake(prompt, **kw):
        assert kw.get("rol") == "reflector"
        return {"text": respuesta, "model": "mock", "tokens_in": 0,
                "tokens_out": 0, "latency_s": 0.0, "cached": False}
    return fake


def test_acepta_cuando_hijo_mejor_y_activa_cambia(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    monkeypatch.setattr(evaluate, "cmd_eval", _fake_eval_factory(repo, "mejor"))
    monkeypatch.setattr(llm, "complete", _fake_reflector(
        json.dumps({"nuevas": [{"regla": "Responder solo con un bloque python valido y simple.",
                                "por_que": "evita fallas de formato."}],
                    "utiles": [], "daninas": []})))
    rc = learn.cmd_learn(["--task", TASK, "--parent", "v0", "--rondas", "1",
                          "--nonce", "T1"])
    assert rc == 0
    activa = (repo / "policies" / TASK / "ACTIVA").read_text(encoding="utf-8").strip()
    assert activa == "v1"
    meta = json.loads((repo / "policies" / TASK / "v1" / "policy.json").read_text(encoding="utf-8"))
    assert meta["padre"] == "v0" and meta["creada_por"] == "learn-ace"
    assert meta["ronda"] == 1 and meta["nonce"] == "T1"
    assert meta["status"] == "aceptada"
    evs = [e for e in _ledger(repo) if e.get("event") == "learn"]
    assert len(evs) == 1 and evs[0]["decision"] == "ACEPTA"
    assert evs[0]["padre"] == "v0" and evs[0]["hijo"] == "v1"
    assert evs[0]["media_padre"] == 0.0 and evs[0]["media_hijo"] == 1.0
    assert evs[0]["ic"][0] > 0
    assert len(evs[0]["reglas_agregadas"]) == 1


def test_rechaza_cuando_hijo_igual_y_activa_no_cambia(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    monkeypatch.setattr(evaluate, "cmd_eval", _fake_eval_factory(repo, "igual"))
    monkeypatch.setattr(llm, "complete", _fake_reflector(
        json.dumps({"nuevas": [{"regla": "Responder solo con un bloque python valido y simple.",
                                "por_que": "evita fallas de formato."}],
                    "utiles": [], "daninas": []})))
    rc = learn.cmd_learn(["--task", TASK, "--parent", "v0", "--rondas", "1",
                          "--nonce", "T2"])
    assert rc == 0
    assert not (repo / "policies" / TASK / "ACTIVA").exists()
    meta = json.loads((repo / "policies" / TASK / "v1" / "policy.json").read_text(encoding="utf-8"))
    assert meta["status"] == "rechazada"
    evs = [e for e in _ledger(repo) if e.get("event") == "learn"]
    assert evs[0]["decision"] == "RECHAZA"
    assert evs[0]["media_padre"] == evs[0]["media_hijo"] == 1.0


def test_eval_invalida_no_acepta(repo, monkeypatch):
    assert evaluate.cmd_seal(["--task", TASK]) == 0
    monkeypatch.setattr(evaluate, "cmd_eval", _fake_eval_factory(repo, "invalida"))
    llamadas = []
    monkeypatch.setattr(llm, "complete", _fake_reflector(json.dumps(
        {"nuevas": [{"regla": "Regla que nunca se usara.", "por_que": ""}],
         "utiles": [], "daninas": []})))
    rc = learn.cmd_learn(["--task", TASK, "--parent", "v0", "--rondas", "1",
                          "--nonce", "T3"])
    assert rc == 0
    assert not (repo / "policies" / TASK / "ACTIVA").exists()
    evs = [e for e in _ledger(repo) if e.get("event") == "learn"]
    assert evs and evs[-1]["decision"] == "ABORTADA"
    # El hijo con eval invalida existe pero jamas queda aceptado/activo.
    v1 = repo / "policies" / TASK / "v1" / "policy.json"
    if v1.exists():
        assert json.loads(v1.read_text(encoding="utf-8"))["status"] != "aceptada"
    _ = llamadas


# --- F2.2-fix: filtro por palabra completa (sin falsos positivos) ------------

_ASSERTS_LOTE = [
    'assert Split([1,2,3,4,5,6]) == [1,3,5]',
    'assert find_Max_Num([1,2,3]) == 321',
    'assert first_repeated_char("abcabc") == "a"',
    'assert check_Valid("hola") == True',
]


def _motivo(regla):
    from motor import learn
    return learn.motivo_filtro(
        regla,
        learn.extraer_nombres_funcion(_ASSERTS_LOTE),
        learn.extraer_literales(_ASSERTS_LOTE),
    )


def test_reglas_generales_de_r1_pasan_el_filtro():
    for r in (
        "Reproducir mentalmente el ejemplo del enunciado con el codigo propuesto y ajustar la formula antes de responder.",
        "Entregar un unico bloque Python autocontenido con imports arriba y solo la funcion pedida.",
        "Definir retornos seguros para entradas borde como vacios, nulos o fuera del dominio valido.",
        "Usa str.split para separar la cadena por espacios.",
    ):
        assert _motivo(r) is None, r


def test_reglas_que_copian_el_lote_se_descartan():
    for r in (
        "Para Split([1,2,3,4,5,6]) devolve los impares.",
        "Calcular find_Max_Num ordenando los digitos.",
        "En mbpp-554 conviene usar un filtro.",
        "Implementar first_repeated_char con un set de vistos.",
    ):
        assert _motivo(r) is not None, r
