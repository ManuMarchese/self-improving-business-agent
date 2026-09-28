#!/usr/bin/env python3
"""Biblioteca de habilidades verificadas (aprendizaje por experiencia propia).

Patron "skill library" (Voyager; EvoAgentBench 2026: lo que mejor transfiere son
procedimientos concretos y verificados, no consejos abstractos). Solo stdlib.

  python -m motor skills --task T --parent ID --desde RUTA_results_train.jsonl [--k 2]

Crea una policy HIJA (siguiente vN) que copia prompt.md + playbook del padre y
agrega `skills.jsonl`: una fila por ejemplo de TRAIN que el propio sistema
resolvio y que PASO TODOS sus tests (ok=True en el grader). Nada mas entra.

Garantias (con test):
  - Solo resultados de split TRAIN: la ruta debe ser un results.jsonl de
    runs_motor/*-train/ y cada id debe existir en tasks/T/train.jsonl. Un id de
    dev o del examen -> se rechaza la construccion entera (exit 1).
  - Solo soluciones verificadas: ok=True y con bloque de codigo extraible.
  - Recuperacion determinista (sin embeddings ni red): similitud lexica
    (Jaccard de tokens) entre enunciados; empate -> id.

evaluate.build_prompt agrega, si la policy tiene skills.jsonl, las k soluciones
mas parecidas al enunciado actual como "ejemplos resueltos y verificados". Al
evaluar sobre train se excluye el propio id (no se le muestra su respuesta).
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TASKS_DIR = _ROOT / "tasks"
POLICIES_DIR = _ROOT / "policies"
K_DEFAULT = 2
MAX_CODIGO = 1200
_RE_TOKEN = re.compile(r"[a-z0-9]+")
_RE_BLOQUE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_STOP = frozenset(
    "a an the of to in and or for is are be by with from that this it as on at "
    "write function python given which find check whether into its".split()
)


def tokens(texto):
    return {t for t in _RE_TOKEN.findall((texto or "").lower()) if t not in _STOP and len(t) > 1}


def similitud(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cargar_skills(policy_dir):
    p = Path(policy_dir) / "skills.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def recuperar(skills, enunciado, k=K_DEFAULT, excluir_id=None):
    """Las k skills mas parecidas al enunciado (determinista)."""
    cand = [s for s in skills if s.get("id") != excluir_id]
    cand.sort(key=lambda s: (-similitud(enunciado, s.get("enunciado", "")), s.get("id", "")))
    return [s for s in cand[:k] if similitud(enunciado, s.get("enunciado", "")) > 0]


def bloque_ejemplos(recuperadas):
    if not recuperadas:
        return ""
    partes = ["Ejemplos resueltos y verificados (problemas parecidos, sus soluciones pasaron todos los tests):"]
    for s in recuperadas:
        partes.append(f"Problema: {s['enunciado']}\n```python\n{s['codigo']}\n```")
    return "\n\n".join(partes)


def _siguiente_id(task):
    nums = [int(p.name[1:]) for p in (POLICIES_DIR / task).glob("v*") if p.name[1:].isdigit()]
    return f"v{max(nums, default=0) + 1}"


def construir(task, parent, desde, k=K_DEFAULT):
    """Devuelve (hijo_id, n_skills) o levanta ValueError con el motivo."""
    ruta = Path(desde)
    if not ruta.is_absolute():
        ruta = _ROOT / ruta
    if "-train" not in ruta.parent.name:
        raise ValueError(f"solo resultados de TRAIN (runs_motor/*-train/): {ruta.parent.name}")
    train_ids = {
        json.loads(l)["id"]
        for l in (TASKS_DIR / task / "train.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    filas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
    ajenos = sorted({f.get("id") for f in filas} - train_ids)
    if ajenos:
        raise ValueError(f"ids que no son de train (fuga): {ajenos[:5]}")
    skills = []
    for f in filas:
        if not f.get("ok"):
            continue
        m = _RE_BLOQUE.search(f.get("output") or "")
        if not m or not m.group(1).strip():
            continue
        skills.append({
            "id": f["id"],
            "enunciado": (f.get("input") or {}).get("enunciado", ""),
            "codigo": m.group(1).strip()[:MAX_CODIGO],
        })
    if not skills:
        raise ValueError("ninguna solucion verificada (ok=True) en esos resultados")
    pdir = POLICIES_DIR / task / parent
    hijo = _siguiente_id(task)
    hdir = POLICIES_DIR / task / hijo
    hdir.mkdir(parents=True)
    for nombre in ("prompt.md", "playbook.jsonl"):
        src = pdir / nombre
        (hdir / nombre).write_text(src.read_text(encoding="utf-8") if src.exists() else "", encoding="utf-8")
    meta = json.loads((pdir / "policy.json").read_text(encoding="utf-8"))
    meta.update({
        "id": hijo, "padre": parent, "creada_por": "skills-v1",
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skills": {"k": k, "n": len(skills), "desde": ruta.relative_to(_ROOT).as_posix()},
    })
    meta.pop("status", None)
    (hdir / "policy.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (hdir / "skills.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in skills), encoding="utf-8"
    )
    return hijo, len(skills)


def cmd_skills(args):
    flags, i = {}, 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            flags[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    task, parent, desde = flags.get("task"), flags.get("parent"), flags.get("desde")
    if not (task and parent and desde):
        print("Uso: python -m motor skills --task T --parent ID --desde runs_motor/<...-train>/results.jsonl [--k 2]")
        return 1
    try:
        hijo, n = construir(task, parent, desde, int(flags.get("k", K_DEFAULT)))
    except (ValueError, OSError) as ex:
        print(f"skills: RECHAZADO: {ex}")
        return 1
    print(f"skills: policy {hijo} (padre {parent}) con {n} soluciones verificadas de train")
    return 0
