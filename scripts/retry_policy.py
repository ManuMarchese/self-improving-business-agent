#!/usr/bin/env python3
"""Clasificacion de errores + backoff exponencial. Solo stdlib.

Clases:
- transitorio (timeout, red, 429, 5xx): reintentar hasta INTENTOS_MAX.
- permanente (400, 401, 403, 404, 4xx desconocido): NO reintentar.
- critico (token ilegible, auth rota): parar todo, no marcar nada.
"""
import random
import time

INTENTOS_MAX = 5
BASE_SEG = 5
TOPE_SEG = 75

TRANSITORIOS_HTTP = {0, 408, 425, 429, 500, 502, 503, 504}
PERMANENTES_HTTP = {400, 401, 403, 404}
CLAVES_TRANSITORIAS = (
    "timeout", "timed out", "error de red", "connection", "temporal",
    "rate limit", "429", "408", "500", "502", "503", "504", "eof", "reset",
)


class ErrorCritico(Exception):
    """Token ilegible o auth rota: abortar oleada sin marcar handles."""


class ErrorPermanente(Exception):
    """Error definitivo: no reintentar (se descarta, no se quema reintento)."""


def clasificar(codigo_http=None, mensaje=""):
    """Devuelve 'transitorio', 'permanente' o 'critico'."""
    m = (mensaje or "").lower()
    if "token" in m and ("ilegible" in m or "no carga" in m or "missing" in m):
        return "critico"
    if codigo_http in PERMANENTES_HTTP:
        return "permanente"
    if codigo_http in TRANSITORIOS_HTTP:
        return "transitorio"
    if codigo_http is not None:
        return "permanente"
    for k in CLAVES_TRANSITORIAS:
        if k in m:
            return "transitorio"
    return "permanente"


def espera_backoff(intento, base=BASE_SEG, tope=TOPE_SEG):
    """Espera creciente 5, 10, 20... con jitter, tope 75s."""
    return min(tope, base * (2 ** max(0, intento - 1)) + random.uniform(0, 2))


def con_reintentos(fn, intentos=INTENTOS_MAX, dormir=True):
    """Ejecuta fn(). Reintenta SOLO transitorios. Levanta lo demas tal cual."""
    ultimo = None
    for i in range(1, intentos + 1):
        try:
            return fn()
        except (ErrorCritico, ErrorPermanente):
            raise
        except Exception as ex:
            ultimo = ex
            if clasificar(getattr(ex, "code", None), str(ex)) != "transitorio":
                raise
            if i >= intentos:
                raise
            if dormir:
                time.sleep(espera_backoff(i))
    raise ultimo
