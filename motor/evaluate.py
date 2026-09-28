#!/usr/bin/env python3
"""Evaluador del motor: corre policy contra un split, califica y sella el resumen.

Solo stdlib. Uso:
  python -m motor eval --task T --policy ID --split train|dev \\
      [--data RUTA_EXTERNA] [--n N] [--nonce S] [--rol rapido|fuerte]
  python -m motor seal --task T

Flujo de eval:
  1. Integridad: compara evals/MANIFEST.json (sha256 con CRLF->LF normalizado de
     grader.py, dev.jsonl y task.json de cada tarea sellada). Cualquier hash que
     no coincide = exit 3.
     Solo `python -m motor seal --task T` regenera el manifiesto de esa tarea.
  2. Arma el prompt: policy/prompt.md (campos del input entre llaves) +
     playbook.jsonl como bullets numerados + input (JSON).
  3. llama a motor.llm.complete en paralelo (respetando el semaforo MOTOR_MAX_PAR
     de llm.py) y califica con tasks/<T>/grader.py.
     revelar=True SOLO si split=train y NO hay --data (el aprendiz de dev/examen
     solo ve puntajes: feedback sin datos del esperado).
  4. Escribe results.jsonl por ejemplo en
     runs_motor/<fecha-hora>-<T>-<ID>-<split>/ y agrega el resumen a
     evals/ledger.jsonl via agent._chain_append (cadena anclada).

Resumen: media, IC 95% por bootstrap (1000 remuestreos, semilla fija), n,
fallas de formato, tokens, segundos.

Modo examen (--data externa): en results y ledger se guarda SOLO id + score + ok
(ni inputs, ni salidas, ni esperados, ni feedback).

Robustez F2.0 (2026-09-23):
  - PROBE previo: 1 llamada corta ("Respondé exactamente: OK", nonce unico)
    al rol elegido ANTES de la corrida; si falla -> no corre, ledger
    "eval_invalida" motivo "proveedor caido (probe)", exit 4. Evita agotar
    3 intentos x 90 s x 2 modelos por ejemplo con el proveedor caido.
  - GUARDIA DE REPO: `git ls-files --others --exclude-standard` (subprocess, solo lectura)
    antes y despues. Archivos NUEVOS fuera de runs_motor/ y evals/ ->
    "eval_invalida" motivo "el LLM toco el repo: <lista>", exit 5, y los
    archivos se MUEVEN a .cache/cuarentena/<fecha-hora>/ (no se borran).
    Cazaria el BUG 3 (el LLM escribio .py en la raiz) en la corrida misma.

Codigos de salida: 0 = OK | 1 = error | 3 = integridad | 4 = proveedor |
5 = repo tocado.
"""
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import agent  # noqa: E402  (importar agent NO ejecuta el CLI: if __name__ guard)
from motor import llm  # noqa: E402
from motor import skills as skills_mod  # noqa: E402

TASKS_DIR = _ROOT / "tasks"
POLICIES_DIR = _ROOT / "policies"
RUNS_MOTOR = _ROOT / "runs_motor"
EVALS_DIR = _ROOT / "evals"
LEDGER = EVALS_DIR / "ledger.jsonl"
MANIFEST = EVALS_DIR / "MANIFEST.json"

BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 20260923
SPLITS = ("train", "dev")
ARCHIVOS_SELLADOS = ("grader.py", "dev.jsonl", "task.json")
# Sellado extra por tarea (F2.0-bis): rutas relativas a la RAIZ del repo para
# codigo del motor que tambien decide el puntaje. py_funcs califica via
# motor/sandbox_py.py (gate anti-truchos por tipo nativo): si alguien lo toca
# sin re-sellar, eval se niega con exit 3. Como: `python -m motor seal
# --task py_funcs` regenera las 4 entradas (3 de tasks/ + 1 de motor/).
ARCHIVOS_SELLADOS_EXTRA = {
    "py_funcs": ("motor/sandbox_py.py",),
}


def _ruta_sellada(tarea, fname):
    """Resuelve un archivo sellado: con '/' es relativo a la raiz (motor/);
    sin '/' vive en tasks/<tarea>/."""
    if "/" in fname:
        return _ROOT / Path(*fname.split("/"))
    return TASKS_DIR / tarea / fname


def _sellados_de(tarea):
    """Todos los archivos sellados exigidos para una tarea (base + extra)."""
    return tuple(ARCHIVOS_SELLADOS) + tuple(ARCHIVOS_SELLADOS_EXTRA.get(tarea, ()))

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTEGRIDAD = 3
EXIT_PROVEEDOR = 4  # demasiados errores del proveedor LLM: corrida invalida
EXIT_REPO = 5  # el LLM toco el repo (guardia): corrida invalida + cuarentena

PROBE_PROMPT = "Respondé exactamente: OK"


def _git_untracked():
    """Conjunto de paths sin seguimiento, RELATIVOS a la raiz del motor.

    `git ls-files --others --exclude-standard` (archivo por archivo) corrido con
    cwd=_ROOT devuelve rutas relativas a esa carpeta (no a la raiz de git).
    Con `git status --porcelain` las rutas eran relativas a la raiz de git:
    dentro de skeleton/ la propia corrida ("skeleton/runs_motor") se veia como
    archivo ajeno -> falso exit 5. Solo lectura. Si git no esta o falla,
    devuelve set() vacio y avisa: la guardia se omite, no invalida.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except Exception as ex:
        print(f"guardia de repo: git no disponible ({ex}), se omite")
        return set()
    if proc.returncode != 0:
        print(f"guardia de repo: git ls-files salio {proc.returncode}, se omite")
        return set()
    out = set()
    for line in (proc.stdout or "").splitlines():
        path = line.strip().strip('"').replace("\\", "/").rstrip("/")
        if path:
            out.add(path)
    return out


def _fuera_de_zonas(paths):
    """Filtra lo esperado: runs_motor/ y evals/ los escribe la corrida misma."""
    return {
        p
        for p in (paths or set())
        if not (
            p == "runs_motor"
            or p.startswith("runs_motor/")
            or p == "evals"
            or p.startswith("evals/")
        )
    }


def _cuarentena(nuevos):
    """Mueve los archivos nuevos a .cache/cuarentena/<fecha-hora>/ (no borra).

    Devuelve (ruta_destino_str, movidos). Lo que no se puede mover se deja
    y se reporta en consola (no rompe la corrida: ya es invalida igual).
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_root = _ROOT / ".cache" / "cuarentena" / stamp
    movidos = []
    for rel in sorted(nuevos):
        if not rel or os.path.isabs(rel) or rel.split("/")[0] == "..":
            print(f"cuarentena: se omite path raro: {rel!r}")
            continue
        src = _ROOT / Path(*rel.split("/"))
        try:
            dest = dest_root / Path(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            movidos.append(rel)
        except OSError as ex:
            print(f"cuarentena: no se pudo mover {rel}: {ex}")
    return dest_root.relative_to(_ROOT).as_posix(), movidos  # relativa: el ledger no guarda rutas personales

MAX_ERRORES_LLM = 0.10  # fraccion maxima de ejemplos con error del proveedor


def _hash_normalizado(path):
    """sha256 de los bytes con finales de linea normalizados (CRLF -> LF).

    git core.autocrlf=true deja CRLF en la working copy de Windows y el repo
    guarda LF: sin normalizar, un git archive / clone en Linux rompe el
    MANIFEST con "hash no coincide" sin que nadie haya tocado nada.
    Unica funcion usada en seal y en la verificacion.
    """
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _read_jsonl(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    return rows


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bootstrap_ci(scores, n_boot=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    """Media + IC 95% por bootstrap. Determinista: semilla fija, remuestreo con reemplazo.

    Devuelve {"mean", "lo", "hi"} (percentiles 2.5/97.5 de las medias muestrales).
    """
    xs = [float(s) for s in scores]
    if not xs:
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0}
    n = len(xs)
    mean = sum(xs) / n
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        total = 0.0
        for _ in range(n):
            total += xs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    lo_i = max(0, min(n_boot - 1, int(0.025 * n_boot)))
    hi_i = max(0, min(n_boot - 1, int(0.975 * n_boot) - 1))
    return {
        "mean": round(mean, 6),
        "lo": round(means[lo_i], 6),
        "hi": round(means[hi_i], 6),
    }


def check_integrity(task=None):
    """Verifica MANIFEST.json contra disco. Devuelve (ok, errores).

    Cubre CADA entrada del manifiesto (no solo la tarea pedida): si alguien
    toca un grader/dev/task.json sellado en cualquier tarea, eval se niega.
    """
    if not MANIFEST.exists():
        return False, [
            f"{MANIFEST.name} no existe (corre: python -m motor seal --task {task or '<tarea>'})"
        ]
    try:
        data = _load_json(MANIFEST)
    except (OSError, ValueError) as ex:
        return False, [f"{MANIFEST.name} ilegible: {ex}"]
    if not isinstance(data, dict):
        return False, [f"{MANIFEST.name} no es un objeto"]
    errs = []
    for tname, files in data.items():
        if not isinstance(files, dict):
            errs.append(f"{tname}: entrada invalida en el manifiesto")
            continue
        for fname, exp_sha in files.items():
            p = _ruta_sellada(tname, fname)
            if not p.exists():
                errs.append(f"{tname}/{fname}: falta el archivo (sellado)")
            else:
                got = _hash_normalizado(p)
                if got != exp_sha:
                    errs.append(
                        f"{tname}/{fname}: hash no coincide "
                        f"(manifiesto {str(exp_sha)[:12]}... vs disco {got[:12]}...)"
                    )
    if task and task not in data:
        errs.append(f"{task}: sin sello en el manifiesto (corre: python -m motor seal --task {task})")
    elif task:
        for fname in _sellados_de(task):
            if fname not in data.get(task, {}):
                errs.append(f"{task}/{fname}: falta en el manifiesto (corre seal)")
    return (not errs), errs


def load_grader(task):
    """Importa tasks/<task>/grader.py y devuelve el modulo (debe tener grade)."""
    path = TASKS_DIR / task / "grader.py"
    if not path.exists():
        raise FileNotFoundError(f"Sin grader: {path}")
    spec = importlib.util.spec_from_file_location(f"motor_grader_{task}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "grade"):
        raise AttributeError(f"{path} sin funcion grade(ejemplo, salida, revelar=False)")
    return mod


def _playbook_lines(policy_dir):
    """Bullets numerados del playbook (v0: archivo vacio = sin bullets)."""
    p = policy_dir / "playbook.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            out.append(ln.strip())
    return out


def _texto_regla(fila):
    """Solo el texto de la regla: el modelo no debe ver ids ni contadores util/danina."""
    try:
        obj = json.loads(fila)
    except (TypeError, ValueError):
        return str(fila)
    if isinstance(obj, dict) and obj.get("regla"):
        return str(obj["regla"])
    return str(fila)


def build_prompt(prompt_md, playbook_rows, input_obj, ejemplos=""):
    """prompt.md (campos {del_input}) + playbook numerado + ejemplos verificados + input JSON."""
    try:
        rendered = prompt_md.format(**(input_obj or {}))
    except (KeyError, IndexError, ValueError) as ex:
        raise ValueError(f"prompt.md no calza con el input: {ex}") from ex
    partes = [rendered]
    if playbook_rows:
        bullets = "\n".join(f"{i}. {_texto_regla(b)}" for i, b in enumerate(playbook_rows, 1))
        partes.append("Playbook:\n" + bullets)
    if ejemplos:
        partes.append(ejemplos)
    partes.append("Input:\n" + json.dumps(input_obj or {}, ensure_ascii=False, sort_keys=True))
    return "\n\n".join(partes)


def _grade_call(grader, ejemplo, salida, revelar):
    """Llama grade y normaliza el retorno (dict valido o fallback)."""
    try:
        g = grader.grade(ejemplo, salida, revelar=revelar)
    except Exception as ex:  # grader roto no tira la corrida entera
        return {"score": 0.0, "ok": False, "feedback": f"grader error: {type(ex).__name__}: {ex}"}
    if not isinstance(g, dict):
        return {"score": 0.0, "ok": False, "feedback": "grader devolvio algo que no es dict"}
    try:
        score = float(g.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "ok": bool(g.get("ok", False)),
        "feedback": str(g.get("feedback", "")),
    }


class _Cortacircuito:
    """Si el proveedor ya fallo mas de `limite` veces, no se llama mas (la corrida
    ya es invalida): evita horas de timeouts de 180 s x reintentos x respaldo."""

    def __init__(self, limite):
        self.limite = limite
        self.fallos = 0
        self._lock = threading.Lock()

    def abierto(self):
        with self._lock:
            return self.fallos > self.limite

    def fallo(self):
        with self._lock:
            self.fallos += 1


def _eval_one(idx, ejemplo, prompt, grader, revelar, nonce, rol, corte=None):
    """Una fila de evaluacion. Devuelve dict listo para results.jsonl."""
    t0 = time.monotonic()
    salida, tokens_in, tokens_out, err, model = "", 0, 0, None, None
    if corte is not None and corte.abierto():
        err = "llm error: cortacircuito abierto (proveedor caido, no se llamo)"
    else:
        try:
            r = llm.complete(prompt, rol=rol, nonce=nonce)
            salida = r.get("text", "")
            tokens_in = int(r.get("tokens_in", 0))
            tokens_out = int(r.get("tokens_out", 0))
            model = r.get("model")
        except Exception as ex:
            err = f"llm error: {type(ex).__name__}: {ex}"
            if corte is not None:
                corte.fallo()
    g = _grade_call(grader, ejemplo, salida if not err else "", revelar)
    if err:
        g = {"score": 0.0, "ok": False, "feedback": err}
    return {
        "idx": idx,
        "id": ejemplo.get("id", f"ej-{idx}"),
        "input": ejemplo.get("input"),
        "output": salida,
        "score": g["score"],
        "ok": g["ok"],
        "llm_error": bool(err),
        "feedback": g["feedback"],
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "segundos": round(time.monotonic() - t0, 3),
    }


def _exam_row(row):
    """Modo examen: SOLO id + score + ok (ni input, output, feedback, esperado)."""
    return {"id": row["id"], "score": row["score"], "ok": row["ok"]}


def _parse_args(args):
    flags, pos = {}, []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            name = a[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[name] = args[i + 1]
                i += 2
            else:
                flags[name] = True
                i += 1
        else:
            pos.append(a)
            i += 1
    return flags, pos


def cmd_eval(args):
    flags, _ = _parse_args(args)
    task = str(flags.get("task") or "")
    policy_id = str(flags.get("policy") or "")
    split = str(flags.get("split") or "")
    data = flags.get("data")
    n_arg = flags.get("n")
    nonce = flags.get("nonce")
    rol_flag = flags.get("rol")

    if not task or not policy_id or split not in SPLITS:
        print(
            "Uso: python -m motor eval --task T --policy ID --split train|dev "
            "[--data RUTA_EXTERNA] [--n N] [--nonce S] [--rol rapido|fuerte]"
        )
        return EXIT_ERROR

    tdir = TASKS_DIR / task
    pdir = POLICIES_DIR / task / policy_id
    if not tdir.is_dir():
        print(f"Sin tasks/{task}/")
        return EXIT_ERROR
    if not pdir.is_dir():
        print(f"Sin policies/{task}/{policy_id}/")
        return EXIT_ERROR

    ok_man, errs = check_integrity(task)
    if not ok_man:
        print("eval: INTEGRIDAD (exit 3)")
        for e in errs:
            print(f"  - {e}")
        return EXIT_INTEGRIDAD

    try:
        task_meta = _load_json(tdir / "task.json")
        policy_meta = _load_json(pdir / "policy.json")
        prompt_md = (pdir / "prompt.md").read_text(encoding="utf-8")
        grader = load_grader(task)
    except (OSError, ValueError, AttributeError) as ex:
        print(f"error cargando tarea/policy/grader: {ex}")
        return EXIT_ERROR

    examen = bool(data)
    if data:
        data_path = Path(str(data))
        if not data_path.exists():
            print(f"--data no existe: {data_path}")
            return EXIT_ERROR
        try:
            rows_in = _read_jsonl(data_path)
        except (OSError, ValueError) as ex:
            print(f"--data ilegible: {ex}")
            return EXIT_ERROR
        split_label = f"{split}-examen"
    else:
        split_file = tdir / f"{split}.jsonl"
        if not split_file.exists():
            print(f"Sin {split_file}")
            return EXIT_ERROR
        try:
            rows_in = _read_jsonl(split_file)
        except (OSError, ValueError) as ex:
            print(f"{split_file.name} ilegible: {ex}")
            return EXIT_ERROR
        split_label = split

    if n_arg is not None:
        try:
            n = int(str(n_arg))
        except ValueError:
            print(f"--n invalido: {n_arg}")
            return EXIT_ERROR
        rows_in = rows_in[: max(0, n)]

    if not rows_in:
        print("sin ejemplos que evaluar")
        return EXIT_ERROR

    revelar = split == "train" and not examen
    playbook = _playbook_lines(pdir)
    rol = str(rol_flag or policy_meta.get("rol_modelo") or "rapido")
    if rol not in ("rapido", "fuerte"):
        print(f"--rol invalido: {rol} (use rapido|fuerte)")
        return EXIT_ERROR

    antes = _fuera_de_zonas(_git_untracked())

    t_probe0 = time.monotonic()
    try:
        probe_nonce = (
            f"probe-{nonce}-{time.time_ns()}" if nonce else f"probe-{time.time_ns()}"
        )
        llm.complete(PROBE_PROMPT, rol=rol, nonce=probe_nonce)
    except Exception as ex:
        total_seg = round(time.monotonic() - t_probe0, 3)
        summary = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "eval_invalida",
            "task": task,
            "policy": policy_id,
            "split": split_label,
            "examen": examen,
            "rol": rol,
            "nonce": nonce,
            "n": len(rows_in),
            "n_validos": 0,
            "errores_llm": len(rows_in),
            "mean": 0.0,
            "ci95_lo": 0.0,
            "ci95_hi": 0.0,
            "fallas": 0,
            "fallas_formato": 0,
            "models": {},
            "tokens_in": 0,
            "tokens_out": 0,
            "segundos": total_seg,
            "revelar": revelar,
            "motivo": f"proveedor caido (probe): {type(ex).__name__}: {ex}",
        }
        EVALS_DIR.mkdir(parents=True, exist_ok=True)
        agent._chain_append(LEDGER, summary)
        print(
            f"eval {task}/{policy_id} split={split_label} rol={rol}: "
            f"PROBE FALLIDO (exit {EXIT_PROVEEDOR}): {summary['motivo']}. "
            f"No se corrio; ledger -> {LEDGER}"
        )
        return EXIT_PROVEEDOR

    skills_rows = skills_mod.cargar_skills(pdir)
    k_skills = int((policy_meta.get("skills") or {}).get("k", skills_mod.K_DEFAULT))
    prompts = []
    for i, ejemplo in enumerate(rows_in):
        try:
            inp = ejemplo.get("input") or {}
            ejemplos = ""
            if skills_rows:
                # Sobre train nunca se muestra la solucion del propio ejemplo.
                excluir = ejemplo.get("id") if (split == "train" and not examen) else None
                ejemplos = skills_mod.bloque_ejemplos(skills_mod.recuperar(
                    skills_rows, str(inp.get("enunciado", "")), k_skills, excluir))
            prompts.append(build_prompt(prompt_md, playbook, inp, ejemplos))
        except ValueError as ex:
            print(f"prompt error en ejemplo {ejemplo.get('id', i)}: {ex}")
            return EXIT_ERROR

    max_workers = max(1, getattr(llm, "_MAX_PAR", 2))
    t_start = time.monotonic()
    results = [None] * len(rows_in)
    corte = _Cortacircuito(int(MAX_ERRORES_LLM * len(rows_in)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [
            pool.submit(_eval_one, i, rows_in[i], prompts[i], grader, revelar, nonce, rol, corte)
            for i in range(len(rows_in))
        ]
        for fut in as_completed(futs):
            row = fut.result()
            results[row["idx"]] = row
    total_seg = round(time.monotonic() - t_start, 3)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = RUNS_MOTOR / f"{stamp}-{task}-{policy_id}-{split_label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for row in results:
            payload = _exam_row(row) if examen else {
                k: row[k]
                for k in (
                    "id", "input", "output", "score", "ok", "llm_error", "feedback",
                    "model", "tokens_in", "tokens_out", "segundos",
                )
            }
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # Un error del proveedor (timeout, 504, cuota) NO es "el modelo no sabe":
    # se excluye del puntaje. Si supera el umbral, la corrida entera es invalida.
    errores_llm = sum(1 for r in results if r.get("llm_error"))
    invalida = errores_llm > MAX_ERRORES_LLM * len(results)
    scores = [r["score"] for r in results if not r.get("llm_error")]
    ci = bootstrap_ci(scores) if scores else {"mean": 0.0, "lo": 0.0, "hi": 0.0}
    fallas = sum(1 for r in results if not r["ok"])
    # fallas_formato = SOLO feedback de formato (sin bloque, salida vacia, etc);
    # el total de fallas (cualquier motivo) va en "fallas".
    fallas_formato = sum(
        1 for r in results if str(r.get("feedback", "")).startswith("formato")
    )
    tok_in = sum(r["tokens_in"] for r in results)
    tok_out = sum(r["tokens_out"] for r in results)
    models = {}
    for r in results:
        m = r.get("model") or "(sin modelo)"
        models[m] = models.get(m, 0) + 1

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "eval_invalida" if invalida else "eval",
        "task": task,
        "policy": policy_id,
        "split": split_label,
        "examen": examen,
        "rol": rol,
        "nonce": nonce,
        "n": len(results),
        "n_validos": len(scores),
        "errores_llm": errores_llm,
        "mean": ci["mean"],
        "ci95_lo": ci["lo"],
        "ci95_hi": ci["hi"],
        "fallas": fallas,
        "fallas_formato": fallas_formato,
        "models": models,
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "segundos": total_seg,
        "results": str(results_path.relative_to(_ROOT)).replace("\\", "/")
        if str(results_path).startswith(str(_ROOT))
        else str(results_path),
        "revelar": revelar,
    }
    if examen:
        summary["rows"] = [_exam_row(r) for r in results]

    despues = _fuera_de_zonas(_git_untracked())
    nuevos = sorted(despues - antes)
    if nuevos:
        dest, movidos = _cuarentena(nuevos)
        summary["event"] = "eval_invalida"
        summary["motivo"] = f"el LLM toco el repo: {', '.join(nuevos)}"
        summary["cuarentena"] = dest
        summary["movidos"] = movidos
        EVALS_DIR.mkdir(parents=True, exist_ok=True)
        agent._chain_append(LEDGER, summary)
        print(
            f"eval {task}/{policy_id} split={split_label} rol={rol}: n={len(results)} "
            f"mean={ci['mean']:.4f} IC95%=[{ci['lo']:.4f},{ci['hi']:.4f}] "
            f"fallas={fallas} fallas_formato={fallas_formato} "
            f"tokens={tok_in}+{tok_out} segundos={total_seg}"
        )
        print(f"models -> {models}")
        print(f"results -> {summary['results']}")
        print(f"ledger  -> {LEDGER}")
        print(
            f"eval INVALIDA (exit {EXIT_REPO}): {summary['motivo']}. "
            f"Movidos a {dest} (no se borran)."
        )
        return EXIT_REPO

    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    agent._chain_append(LEDGER, summary)

    print(
        f"eval {task}/{policy_id} split={split_label} rol={rol}: n={len(results)} "
        f"mean={ci['mean']:.4f} IC95%=[{ci['lo']:.4f},{ci['hi']:.4f}] "
        f"fallas={fallas} fallas_formato={fallas_formato} "
        f"tokens={tok_in}+{tok_out} segundos={total_seg}"
    )
    print(f"models -> {models}")
    print(f"results -> {summary['results']}")
    print(f"ledger  -> {LEDGER}")
    if invalida:
        print(
            f"eval INVALIDA (exit {EXIT_PROVEEDOR}): {errores_llm}/{len(results)} errores del "
            f"proveedor LLM (> {MAX_ERRORES_LLM:.0%}). No usar como linea base; reintentar."
        )
        return EXIT_PROVEEDOR
    return EXIT_OK


def cmd_seal(args):
    """Regenera el manifiesto de UNA tarea y deja linea 'seal' en el ledger."""
    flags, _ = _parse_args(args)
    task = str(flags.get("task") or "")
    if not task:
        print("Uso: python -m motor seal --task T")
        return EXIT_ERROR
    tdir = TASKS_DIR / task
    if not tdir.is_dir():
        print(f"Sin tasks/{task}/")
        return EXIT_ERROR

    old = None
    if MANIFEST.exists():
        try:
            data = _load_json(MANIFEST)
            old = data.get(task) if isinstance(data, dict) else None
        except (OSError, ValueError):
            data = {}
    else:
        data = {}

    new = {}
    for fname in _sellados_de(task):
        p = _ruta_sellada(task, fname)
        if not p.exists():
            print(f"seal: falta {p}")
            return EXIT_ERROR
        new[fname] = _hash_normalizado(p)

    data[task] = new
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    agent._atomic_write(
        MANIFEST, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    )
    agent._chain_append(
        LEDGER,
        {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "seal",
            "task": task,
            "old": old,
            "new": new,
        },
    )
    print(f"seal {task}:")
    print(f"  viejos: {json.dumps(old, sort_keys=True) if old else '(sin sello previo)'}")
    print(f"  nuevos: {json.dumps(new, sort_keys=True)}")
    print(f"  manifiesto -> {MANIFEST}")
    print(f"  ledger     -> {LEDGER}")
    return EXIT_OK
