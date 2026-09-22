"""Tests metrica ponderada por dificultad (run report). Solo stdlib, dir temporal."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import _dificultad, _piso_evidencia, cmd_run_report  # noqa: E402


def _mk_run(base, name, d="ausente", events=(), cerrada=True, tiempo="1 h", tokens="est ~1,000"):
    """Crea runs/<name>/ con brief.md (+ linea Dificultad segun d), journal y closeout opcional.

    d: "ausente" (sin linea), "invalida" (valor fuera de 1|2|3) o 1/2/3.
    tiempo: valor para la linea `- Tiempo:` (ej "1 h", "5 h").
    tokens: valor para la linea `- Tokens:` (ej "est ~5,000 | searches=2 ig=1 posts=3 readkb=0").
    """
    rdir = Path(base) / name
    rdir.mkdir(parents=True)
    brief = f"# BRIEF\n\n## Presupuesto\n- Tiempo: {tiempo}\n- Tokens: {tokens}\n"
    if d == "invalida":
        brief += "- Dificultad: alta\n"
    elif d != "ausente":
        brief += f"- Dificultad: {d}  # 1|2|3\n"
    (rdir / "brief.md").write_text(brief, encoding="utf-8")
    with (rdir / "journal.jsonl").open("w", encoding="utf-8") as f:
        for ev, msg in events:
            f.write(json.dumps({"ts": "2026-09-22T00:00:00", "event": ev, "msg": msg}) + "\n")
    if cerrada:
        (rdir / "closeout.md").write_text("cierre", encoding="utf-8")
    return rdir


def _report(tmp):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_run_report(runs_dir=tmp)
    return rc, buf.getvalue()


class TestMetricasPond(unittest.TestCase):
    def test_d1_pesa_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1")])
            self.assertEqual(_dificultad(Path(tmp) / "a" / "brief.md"), 1)
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("a: cerrada | pasos 1 | gate 0 friccion 0 limite-externo 0 | D=1 w=0", out)
            self.assertIn("pond N/A (solo D1)", out)

    def test_default_d1_ausente_o_invalida(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d="ausente", events=[("paso-ok", "p1")])
            _mk_run(tmp, "b", d="invalida", events=[("paso-ok", "p1")])
            self.assertEqual(_dificultad(Path(tmp) / "a" / "brief.md"), 1)
            self.assertEqual(_dificultad(Path(tmp) / "b" / "brief.md"), 1)
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=1 w=0", out)
            self.assertIn("pond N/A (solo D1)", out)

    def test_formula_pond_mezcla_1_2_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1")])
            # D2 honesto: evidencia API (ig/posts>0 en linea - Tokens:)
            _mk_run(tmp, "b", d=2, events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            # D3 honesto: evidencia horas>3 (piso 3 por - Tiempo:)
            _mk_run(tmp, "c", d=3, events=[("paso-ok", "p1")], tiempo="5 h",
                    tokens="est ~8,000 | searches=1 ig=1 posts=3 readkb=0")
            _mk_run(tmp, "d", d=2, events=[("paso-ok", "p1"), ("intervencion", "[friccion] traba")],
                    tokens="est ~5,000 | searches=1 ig=0 posts=0 readkb=0")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("b: cerrada | pasos 1 | gate 0 friccion 0 limite-externo 0 | D=2 w=1", out)
            self.assertIn("c: cerrada | pasos 1 | gate 0 friccion 0 limite-externo 0 | D=3 w=2", out)
            # raw intacto ANTES de la linea nueva: 3/4 en 0 friccion y 0 total
            self.assertIn("Corridas cerradas (instrumentadas): 4 | 0 friccion: 3 | 0 total: 3", out)
            # pond: qw=0+1+2+1=4, pf=0+1+2+0=3, pt=0+1+2+0=3
            self.assertIn("0 friccion: 3/4 raw + 3/4 pond | 0 total: 3/4 raw + 3/4 pond", out)

    def test_suma_cero_da_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1")])
            _mk_run(tmp, "b", d="ausente", events=[("paso-ok", "p1")])
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("0 friccion: 2/2 raw + pond N/A (solo D1)", out)

    def test_racha_x3_dispara_alerta(self):
        with tempfile.TemporaryDirectory() as tmp:
            for n in ("a", "b", "c"):
                _mk_run(tmp, n, d=2, events=[("paso-ok", "p1")],
                        tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("ALERTA: racha 0-fricción x3 (D≥2), auditar", out)

    def test_racha_cortada_no_alerta(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=2, events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            _mk_run(tmp, "b", d=2, events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            _mk_run(tmp, "c", d=2, events=[("paso-ok", "p1"), ("intervencion", "[friccion] traba")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            _mk_run(tmp, "d", d=2, events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertNotIn("ALERTA", out)


    def test_piso_api_sube_a_2(self):
        brief = "# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n- Tokens: est ~5,000 | searches=0 ig=1 posts=3 readkb=0\n- Dificultad: 1  # 1|2|3\n"
        self.assertEqual(_piso_evidencia(brief, ""), 2)
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("a: cerrada | pasos 1 | gate 0 friccion 0 limite-externo 0 | D=2 w=1", out)

    def test_piso_horas_mas_3_sube_a_3(self):
        brief = "# BRIEF\n\n## Presupuesto\n- Tiempo: 5 h\n- Tokens: est ~1,000\n- Dificultad: 1  # 1|2|3\n"
        self.assertEqual(_piso_evidencia(brief, ""), 3)
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1")], tiempo="5 h")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=3 w=2", out)

    def test_piso_gate_sube_a_3(self):
        brief = "# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n- Tokens: est ~1,000\n- Dificultad: 1  # 1|2|3\n"
        journal = '{"ts": "2026-09-22T00:00:00", "event": "intervencion", "msg": "[gate] ok operador"}\n'
        self.assertEqual(_piso_evidencia(brief, journal), 3)
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1"), ("intervencion", "[gate] ok operador")])
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=3 w=2", out)

    def test_piso_omision_con_evidencia(self):
        brief = "# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n- Tokens: est ~5,000 | searches=2 ig=0 posts=0 readkb=0\n"
        self.assertEqual(_piso_evidencia(brief, ""), 2)
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d="ausente", events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=2 ig=0 posts=0 readkb=0")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=2 w=1", out)


    def test_estrella_d3_sin_evidencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=3, events=[("paso-ok", "p1")],
                    tiempo="1 h", tokens="est ~1,000")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=3* w=2", out)
            self.assertIn("D* sobre-evidencia: 1 (prioritarias para sorteo-auditoría)", out)

    def test_estrella_d2_sin_evidencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=2, events=[("paso-ok", "p1")],
                    tiempo="1 h", tokens="est ~1,000")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=2* w=1", out)
            self.assertIn("D* sobre-evidencia: 1 (prioritarias para sorteo-auditoría)", out)

    def test_sin_estrella_acorde_evidencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=2, events=[("paso-ok", "p1")],
                    tokens="est ~5,000 | searches=0 ig=1 posts=3 readkb=0")
            _mk_run(tmp, "b", d=3, events=[("paso-ok", "p1")], tiempo="5 h",
                    tokens="est ~8,000 | searches=1 ig=1 posts=3 readkb=0")
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=2 w=1", out)
            self.assertIn("D=3 w=2", out)
            self.assertNotIn("*", out.split("Corridas cerradas")[0])
            self.assertNotIn("sobre-evidencia", out)

    def test_footer_ausente_N0(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=1, events=[("paso-ok", "p1")])
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertNotIn("sobre-evidencia", out)

    def test_ponderacion_usa_D_final_con_estrella(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "a", d=3, events=[("paso-ok", "p1")],
                    tiempo="1 h", tokens="est ~1,000")
            _mk_run(tmp, "b", d=1, events=[("paso-ok", "p1")])
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=3* w=2", out)
            # qw=2+0=2, pf=2 (a sin friccion), pt=2
            self.assertIn("0 friccion: 2/2 raw + 2/2 pond", out)
            self.assertIn("D* sobre-evidencia: 1 (prioritarias para sorteo-auditoría)", out)


def _journal_linea(ts, ev, msg):
    return json.dumps({"ts": ts, "event": ev, "msg": msg}, ensure_ascii=False)


def _mk_run_ts(base, name, d="ausente", tiempo="1 h", tokens="est ~1,000",
               lineas=(), cerrada=True):
    """Variante con timestamps explicitos: lineas = [(ts, ev, msg)].

    El brief es DISPLAY/auxiliar: si el journal trae wall (>=2 ts) o conteo
    API en oleadas, el journal manda y el brief se ignora en esa dimension.
    """
    rdir = Path(base) / name
    rdir.mkdir(parents=True)
    brief = f"# BRIEF\n\n## Presupuesto\n- Tiempo: {tiempo}\n- Tokens: {tokens}\n"
    if d == "invalida":
        brief += "- Dificultad: alta\n"
    elif d != "ausente":
        brief += f"- Dificultad: {d}  # 1|2|3\n"
    (rdir / "brief.md").write_text(brief, encoding="utf-8")
    with (rdir / "journal.jsonl").open("w", encoding="utf-8") as f:
        for ts, ev, msg in lineas:
            f.write(_journal_linea(ts, ev, msg) + "\n")
    if cerrada:
        (rdir / "closeout.md").write_text("cierre", encoding="utf-8")
    return rdir


class TestForjaNeutralizada(unittest.TestCase):
    """El brief es auxiliar: un `- Tiempo: 5 h` escrito a mano no sube D si el
    journal (hash-chain, append-only) prueba una corrida corta sin gate ni API."""

    def test_forja_tiempo_brief_no_sube(self):
        brief = ("# BRIEF\n\n## Presupuesto\n- Tiempo: 5 h\n"
                 "- Tokens: est ~1,000\n- Dificultad: 1  # 1|2|3\n")
        journal = (_journal_linea("2026-09-22T10:00:00", "paso-ok", "p1") + "\n"
                   + _journal_linea("2026-09-22T10:10:00", "paso-ok", "p2") + "\n")
        self.assertEqual(_piso_evidencia(brief, journal), 1)
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run_ts(tmp, "f", d=1, tiempo="5 h", tokens="est ~1,000",
                       lineas=[("2026-09-22T10:00:00", "paso-ok", "p1"),
                               ("2026-09-22T10:10:00", "paso-ok", "p2")])
            rc, out = _report(tmp)
            self.assertEqual(rc, 0)
            self.assertIn("D=1 w=0", out)
            self.assertNotIn("*", out.split("Corridas cerradas")[0])

    def test_wall_mas_3h_sube_desde_journal(self):
        brief = ("# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n"
                 "- Tokens: est ~1,000\n- Dificultad: 1  # 1|2|3\n")
        journal = (_journal_linea("2026-09-22T10:00:00", "paso-ok", "p1") + "\n"
                   + _journal_linea("2026-09-22T13:30:01", "paso-ok", "p2") + "\n")
        self.assertEqual(_piso_evidencia(brief, journal), 3)

    def test_api_oleada_sube_desde_journal(self):
        brief = ("# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n"
                 "- Tokens: est ~1,000\n- Dificultad: 1  # 1|2|3\n")
        journal = (_journal_linea("2026-09-22T10:00:00", "oleada", "w1: 2 frescos") + "\n"
                   + _journal_linea("2026-09-22T10:01:00", "oleada",
                                    "w1: 4 llamadas API acumuladas") + "\n")
        self.assertEqual(_piso_evidencia(brief, journal), 2)

    def test_api_cero_explicito_no_hace_fallback(self):
        # Journal nuevo prueba 0 API: el brief que declara ig=5 se ignora.
        brief = ("# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n"
                 "- Tokens: est ~5,000 | searches=0 ig=5 posts=0 readkb=0\n")
        journal = (_journal_linea("2026-09-22T10:00:00", "oleada", "w1: 1 frescos") + "\n"
                   + _journal_linea("2026-09-22T10:01:00", "oleada",
                                    "w1: 0 llamadas API acumuladas") + "\n")
        self.assertEqual(_piso_evidencia(brief, journal), 1)

    def test_fallback_vieja_sin_eventos(self):
        # Corrida vieja: 1 solo ts (sin wall) y sin oleadas -> manda el brief.
        brief = ("# BRIEF\n\n## Presupuesto\n- Tiempo: 5 h\n"
                 "- Tokens: est ~1,000\n- Dificultad: 1  # 1|2|3\n")
        journal = _journal_linea("2026-09-22T10:00:00", "paso-ok", "p1") + "\n"
        self.assertEqual(_piso_evidencia(brief, journal), 3)
        brief_api = ("# BRIEF\n\n## Presupuesto\n- Tiempo: 1 h\n"
                     "- Tokens: est ~5,000 | searches=0 ig=1 posts=0 readkb=0\n")
        self.assertEqual(_piso_evidencia(brief_api, journal), 2)


if __name__ == "__main__":
    unittest.main()
