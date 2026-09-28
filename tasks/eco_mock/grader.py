"""Grader determinista de eco_mock: la salida debe ser el input en mayusculas.

Contrato (motor/evaluate.py):
  grade(ejemplo, salida, revelar=False) -> {"score": 0..1, "ok": bool, "feedback": str}

  - score: 1.0 si la salida limpia coincide con expected, si no 0.0.
  - ok: True si el formato se respeta (texto no vacio en mayusculas).
  - feedback con revelar=False NUNCA incluye datos del esperado.
"""


def grade(ejemplo, salida, revelar=False):
    esperado = str((ejemplo or {}).get("expected", ""))
    if not isinstance(salida, str) or not salida.strip():
        return {
            "score": 0.0,
            "ok": False,
            "feedback": "formato invalido: salida vacia o no es texto",
        }
    limpio = salida.strip()
    formato_ok = limpio == limpio.upper()
    coincide = limpio == esperado.strip()
    score = 1.0 if coincide else 0.0
    if revelar:
        feedback = f"esperado={esperado!r} obtenido={limpio!r} score={score:g}"
    else:
        feedback = "ok: la salida coincide" if coincide else "la salida no coincide con el esperado"
    return {"score": score, "ok": bool(formato_ok), "feedback": feedback}
