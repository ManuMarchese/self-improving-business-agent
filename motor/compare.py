#!/usr/bin/env python3
"""Comparacion pareada entre dos corridas: la regla que decide si un cambio se QUEDA.

Solo stdlib. Uso:
  python -m motor compare --a RUTA_results_A --b RUTA_results_B [--json]

A = policy vieja / padre, B = candidata. Las rutas apuntan a
runs_motor/<...>/results.jsonl (los que deja `python -m motor eval`).

Por que pareado: con n=60 el IC de UNA corrida mide ~10 pts. Comparar medias
sueltas engana (el ruido entre corridas tapa la senal). Lo correcto es comparar
los MISMOS ejemplos: d = score_B - score_A por id.

Reglas:
  - Usa SOLO los ids presentes en ambos y que NO tengan llm_error en ninguno.
  - Si los ids presentes en ambos son < 90% de A: exit 1 (no se compara
    peras con manzanas).
  - media de d + IC95% por bootstrap PAREADO (remuestrear ids con reemplazo,
    2000 remuestreos, semilla fija).
  - mejoras = A falla (ok=False) y B pasa (ok=True);
    regresiones = A pasa y B falla; empates = el resto.
  - DECISION: "ACEPTA" solo si IC95% inferior > 0 Y regresiones <= mejoras / 2.
    Si no, "RECHAZA" (con el motivo).
  - Cada comparacion deja un evento "compare" en evals/ledger.jsonl via
    agent._chain_append (a, b, n, media_d, ic, mejoras, regresiones, decision).

Codigos de salida: 0 = ACEPTA | 1 = error | 2 = RECHAZA.
"""

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import agent  # noqa: E402  (importar agent NO ejecuta el CLI: if __name__ guard)

EVALS_DIR = _ROOT / "evals"

def ruta_repo(path):
    """Ruta relativa al repo con '/' (el ledger no guarda rutas personales)."""
    try:
        return Path(path).resolve().relative_to(_ROOT).as_posix()
    except (ValueError, OSError):
        return Path(path).name

LEDGER = EVALS_DIR / "ledger.jsonl"

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260923
COBERTURA_MINIMA = 0.90

EXIT_ACEPTA = 0
EXIT_ERROR = 1
EXIT_RECHAZA = 2


def _read_results(path):
    """Lee un results.jsonl y devuelve dict id -> fila (ultima gana si hay duplicados)."""
    rows = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        rid = r.get("id")
        if rid is None:
            raise ValueError(f"{path}: fila sin id: {ln[:120]}")
        rows[str(rid)] = r
    return rows


def paired_bootstrap_ci(diffs, n_boot=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    """Media + IC 95% por bootstrap pareado. Determinista: semilla fija.

    Devuelve {"mean", "lo", "hi"} (percentiles 2.5/97.5 de las medias remuestreadas).
    """
    xs = [float(d) for d in diffs]
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


def _parse_args(args):
    flags = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            flags["json"] = True
            i += 1
        elif a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            flags[a[2:]] = args[i + 1]
            i += 2
        elif a.startswith("--"):
            flags[a[2:]] = True
            i += 1
        else:
            flags.setdefault("_pos", []).append(a)
            i += 1
    return flags


def cmd_compare(args):
    flags = _parse_args(list(args or []))
    ruta_a = flags.get("a")
    ruta_b = flags.get("b")
    como_json = bool(flags.get("json"))

    if not ruta_a or not ruta_b or isinstance(ruta_a, bool) or isinstance(ruta_b, bool):
        print("Uso: python -m motor compare --a RUTA_results_A --b RUTA_results_B [--json]")
        return EXIT_ERROR

    pa, pb = Path(str(ruta_a)), Path(str(ruta_b))
    if not pa.exists():
        print(f"compare: no existe --a: {pa}")
        return EXIT_ERROR
    if not pb.exists():
        print(f"compare: no existe --b: {pb}")
        return EXIT_ERROR
    try:
        ra = _read_results(pa)
        rb = _read_results(pb)
    except (OSError, ValueError) as ex:
        print(f"compare: results ilegible: {ex}")
        return EXIT_ERROR

    if not ra:
        print(f"compare: --a sin filas: {pa}")
        return EXIT_ERROR

    ids_a = set(ra)
    compartidos = sorted(ids_a & set(rb))
    if len(compartidos) < COBERTURA_MINIMA * len(ids_a):
        print(
            f"compare: ids compartidos {len(compartidos)}/{len(ids_a)} "
            f"(< {COBERTURA_MINIMA:.0%} de A): no se compara peras con manzanas"
        )
        return EXIT_ERROR

    # Solo ids limpios en AMBOS (un error del proveedor no es "el modelo no sabe").
    ids = [i for i in compartidos
           if not ra[i].get("llm_error") and not rb[i].get("llm_error")]
    excluidos = len(compartidos) - len(ids)
    if not ids:
        print("compare: sin ids comparables (todos con llm_error)")
        return EXIT_ERROR

    def _score(r):
        try:
            s = float(r.get("score", 0.0))
        except (TypeError, ValueError):
            s = 0.0
        return max(0.0, min(1.0, s))

    diffs = [_score(rb[i]) - _score(ra[i]) for i in ids]
    ci = paired_bootstrap_ci(diffs)
    mejoras = sum(1 for i in ids if not ra[i].get("ok") and rb[i].get("ok"))
    regresiones = sum(1 for i in ids if ra[i].get("ok") and not rb[i].get("ok"))
    empates = len(ids) - mejoras - regresiones

    motivos = []
    if not ci["lo"] > 0:
        motivos.append("IC95% incluye 0 (sin mejora significativa)")
    if not regresiones <= mejoras / 2:
        motivos.append(
            f"regresiones {regresiones} > mejoras/2 ({mejoras}/2={mejoras / 2:g})"
        )
    decision = "ACEPTA" if not motivos else "RECHAZA"
    motivo = "; ".join(motivos)

    resultado = {
        "a": ruta_repo(ruta_a),
        "b": ruta_repo(ruta_b),
        "n": len(ids),
        "media_d": ci["mean"],
        "ic": [ci["lo"], ci["hi"]],
        "mejoras": mejoras,
        "regresiones": regresiones,
        "empates": empates,
        "decision": decision,
        "motivo": motivo,
    }

    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    agent._chain_append(
        LEDGER,
        {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "compare",
            "a": ruta_repo(ruta_a),
            "b": ruta_repo(ruta_b),
            "n": len(ids),
            "media_d": ci["mean"],
            "ic": [ci["lo"], ci["hi"]],
            "mejoras": mejoras,
            "regresiones": regresiones,
            "empates": empates,
            "decision": decision,
            "motivo": motivo,
        },
    )

    print(f"compare A={ruta_a}")
    print(f"        B={ruta_b}")
    print(f"  n compartidos: {len(ids)} (A={len(ids_a)}, B={len(rb)}, "
          f"excluidos llm_error={excluidos})")
    print(f"  media_d (B-A): {ci['mean']:+.4f}")
    print(f"  IC95% pareado: [{ci['lo']:+.4f}, {ci['hi']:+.4f}] (bootstrap {BOOTSTRAP_N}, semilla fija)")
    print(f"  mejoras (A falla, B pasa):     {mejoras}")
    print(f"  regresiones (A pasa, B falla): {regresiones}")
    print(f"  empates: {empates}")
    if decision == "ACEPTA":
        print("  decision: ACEPTA (el cambio se QUEDA)")
    else:
        print(f"  decision: RECHAZA ({motivo})")
    print(f"  ledger -> {LEDGER}")
    if como_json:
        print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))

    return EXIT_ACEPTA if decision == "ACEPTA" else EXIT_RECHAZA
