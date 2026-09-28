"""Fixtures comunes: los tests NO dependen del motor/models.json real.

models.json cambia segun el proveedor disponible (Zen, OpenRouter, Ollama local).
Los tests de llm/learn parchean `_run_once` (opencode) y asumen un respaldo:
se les fija un juego de modelos falsos estable. Un test que necesite otro juego
vuelve a parchear `_load_models` (su monkeypatch pisa a este).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODELOS_FALSOS = {
    "rapido": "opencode/fake-rapido",
    "respaldo_rapido": "opencode/fake-respaldo",
    "fuerte": "opencode/fake-fuerte",
    "reflector": "opencode/fake-reflector",
}


@pytest.fixture(autouse=True)
def _modelos_estables(monkeypatch):
    from motor import llm
    monkeypatch.setattr(llm, "_load_models", lambda: dict(MODELOS_FALSOS))
