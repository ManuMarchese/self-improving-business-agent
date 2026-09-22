"""Tests metrica de autonomia (run intervene/step/report). Solo stdlib, dir temporal."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import _summarize_journal, cmd_run_intervene, cmd_run_report  # noqa: E402


def _mk_run(base, name, events=()):
    rdir = Path(base) / name
    rdir.mkdir(parents=True)
    (rdir / "brief.md").write_text("brief", encoding="utf-8")
    with (rdir / "journal.jsonl").open("w", encoding="utf-8") as f:
        for ev, msg in events:
            f.write(json.dumps({"ts": "2026-09-21T00:00:00", "event": ev, "msg": msg}) + "\n")
    return rdir


class TestMetricas(unittest.TestCase):
    def test_conteo_correcto(self):
        with tempfile.TemporaryDirectory() as tmp:
            jf = _mk_run(
                tmp, "r1",
                [("paso-ok", "p1"), ("paso-ok", "p2"),
                 ("intervencion", "[gate] ok operador"),
                 ("intervencion", "[friccion] bug"),
                 ("intervencion", "[friccion] otro"),
                 ("intervencion", "[limite-externo] cuota"),
                 ("log", "ruido viejo no cuenta")],
            ) / "journal.jsonl"
            s = _summarize_journal(jf)
            self.assertEqual(s["pasos"], 2)
            self.assertEqual(s["intervenciones"], {"gate": 1, "friccion": 2, "limite-externo": 1})
            self.assertTrue(s["instrumentada"])

    def test_sin_eventos_no_es_cero(self):
        with tempfile.TemporaryDirectory() as tmp:
            jf = _mk_run(tmp, "r2", [("log", "viejo"), ("new", "creada")]) / "journal.jsonl"
            s = _summarize_journal(jf)
            self.assertFalse(s["instrumentada"])
            self.assertEqual(s["pasos"], 0)

    def test_tipo_invalido_se_rechaza(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = cmd_run_intervene("cualquiera", "otro-tipo", "x")
            self.assertEqual(rc, 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_report_marca_sin_instrumentar(self):
        with tempfile.TemporaryDirectory() as tmp:
            _mk_run(tmp, "vieja", [("log", "nada nuevo")])
            _mk_run(tmp, "nueva", [("paso-ok", "p1"), ("intervencion", "[gate] ok")])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cmd_run_report(runs_dir=tmp)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("vieja: abierta | sin instrumentar", out)
            self.assertIn("nueva: abierta | pasos 1 | gate 1 friccion 0 limite-externo 0", out)


if __name__ == "__main__":
    unittest.main()
