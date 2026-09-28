"""Tests del escaner de secretos: 3 formas de ruta de usuario (Fase 0, paso 3-bis).

Formas probadas: barra simple (invertida), barra normal (slash) y barras dobles
escapadas como en JSON. Sin ejemplos literales de ruta: el propio archivo del test
no debe matchear los patrones del escaner (evita self-match en doctor).

Tambien: excepcion por hash (doctor_allow.json) — hit permitido pasa; la misma
ruta en otra linea nueva falla; la linea permitida editada falla.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402


def _path_single():
    b = chr(92)
    return "C:" + b + "Users" + b + "demo_user" + b + "demo.txt"


def _path_double():
    b = chr(92) * 2
    return "C:" + b + "Users" + b + "demo_user" + b + "demo.txt"


def _path_fwd():
    s = chr(47)
    return "C:" + s + "Users" + s + "demo_user" + s + "demo.txt"


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content + "\n", encoding="utf-8")
    return p


def test_detecta_3_formas_de_ruta_de_usuario(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    files = [
        _write(tmp_path, "a_single.txt", "dir " + _path_single()),
        _write(tmp_path, "b_double.txt", "dir " + _path_double()),
        _write(tmp_path, "c_fwd.txt", "dir " + _path_fwd()),
    ]
    hits = agent._scan_secrets(files)
    joined = "\n".join(hits)
    assert len(hits) == 3, joined
    assert "a_single.txt:1" in joined
    assert "b_double.txt:1" in joined
    assert "c_fwd.txt:1" in joined
    assert joined.count("ruta absoluta Windows") == 3


def test_forma_sin_barra_de_usuario_no_matchea(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    ok = _write(tmp_path, "ok.txt", "dir " + "C:" + chr(92) + "Temp" + chr(92) + "x.txt")
    hits = agent._scan_secrets([ok])
    assert hits == []


def _allow(tmp_path, entries):
    (tmp_path / "doctor_allow.json").write_text(json.dumps(entries), encoding="utf-8")


def _sha(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def test_hit_permitido_pasa(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    body = "dir " + _path_double()
    f = _write(tmp_path, "hist.txt", body)
    _allow(tmp_path, [{"file": "hist.txt", "line": 1, "sha256": _sha(body),
                       "motivo": "historia append-only previa al escaner ampliado"}])
    assert agent._scan_secrets([f]) == []


def test_misma_ruta_en_linea_nueva_falla(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    body = "dir " + _path_double()
    f = _write(tmp_path, "hist.txt", body + "\n" + body)
    _allow(tmp_path, [{"file": "hist.txt", "line": 1, "sha256": _sha(body),
                       "motivo": "historia append-only previa al escaner ampliado"}])
    hits = agent._scan_secrets([f])
    assert len(hits) == 1, hits
    assert "hist.txt:2" in hits[0]


def test_linea_permitida_editada_falla(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    body = "dir " + _path_double()
    _allow(tmp_path, [{"file": "hist.txt", "line": 1, "sha256": _sha(body),
                       "motivo": "historia append-only previa al escaner ampliado"}])
    edited = _write(tmp_path, "hist.txt", body + " editada")
    hits = agent._scan_secrets([edited])
    assert len(hits) == 1, hits
    assert "hist.txt:1" in hits[0]
