"""Checks de ejemplo para la task demo_basico (esqueleto generico).

Interfaz fija que el core (agent.py golden) importa por nombre de task:
  hard_filter(row) -> str | None   razon de descarte, o None si pasa el gate
  decide(total, dims, row) -> str  califica | no_califica (solo si pasa el gate)
  validate(row) -> list[str]       errores de consistencia (vacia = ok)

Reemplaza este modulo por los filtros reales de tu negocio manteniendo la interfaz.
"""


def hard_filter(row):
    gate = (row.get("input") or {}).get("gate", "")
    if isinstance(gate, str) and gate.startswith("descarte"):
        return gate
    return None


def decide(total, dims, row):
    try:
        total = float(total)
    except (TypeError, ValueError):
        return "no_califica"
    if total >= 7:
        return "califica"
    return "no_califica"


def validate(row):
    errs = []
    inp = row.get("input") or {}
    exp = row.get("expected") or {}
    gate = inp.get("gate", "")
    if exp.get("decision") == "descarte" and not (
        isinstance(gate, str) and gate.startswith("descarte")
    ):
        errs.append("expected=descarte pero input.gate no es descarte")
    if isinstance(gate, str) and gate.startswith("descarte") and exp.get("decision") != "descarte":
        errs.append("input.gate es descarte pero expected.decision no")
    if exp.get("decision") == "descarte" and exp.get("score") is not None:
        errs.append("descarte debe llevar score null")
    return errs
