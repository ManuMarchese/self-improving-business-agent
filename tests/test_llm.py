"""Tests del adaptador LLM (motor/llm.py): sin red, con fakes y tmp_path."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from motor import llm  # noqa: E402
from motor.llm import LLMError, complete, parse_run_output  # noqa: E402
from retry_policy import INTENTOS_MAX  # noqa: E402  (retry_policy intacto; el motor usa MOTOR_INTENTOS)

FIXTURE = ROOT / "motor" / "_descubrimiento" / "run_json.txt"


def _tmp_paths(tmp):
    cache_dir = tmp / "cache" / "llm"
    usage = tmp / "cache" / "llm_usage.jsonl"
    return mock.patch.multiple(
        llm,
        _CACHE_DIR=cache_dir,
        _CACHE_ROOT=tmp / "cache",
        _USAGE_FILE=usage,
    )


class TestParser(unittest.TestCase):
    def test_fixture_real(self):
        raw = FIXTURE.read_text(encoding="utf-8")
        p = parse_run_output(raw)
        self.assertEqual(p["text"], "OK")
        self.assertEqual(p["tokens_in"], 11301)
        self.assertEqual(p["tokens_out"], 3)
        self.assertIsNone(p["error"])

    def test_lineas_basura_se_ignoran(self):
        p = parse_run_output("no-json\n\n" + FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(p["text"], "OK")

    def test_error_evento(self):
        raw = json.dumps({
            "type": "error",
            "error": {"name": "FreeTierError",
                      "data": {"message": "403 can only be used", "statusCode": 403}}},
            ensure_ascii=False)
        p = parse_run_output(raw)
        self.assertEqual(p["error"]["data"]["statusCode"], 403)
        self.assertEqual(p["text"], "")


class TestCache(unittest.TestCase):
    def test_miss_luego_hit(self):
        calls = []

        def fake_run_once(model, message, timeout):
            calls.append(model)
            return {"text": "hola", "tokens_in": 10, "tokens_out": 2}

        with mock.patch.object(llm, "_run_once", side_effect=fake_run_once):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                with _tmp_paths(Path(td)):
                    r1 = complete("p", rol="rapido", nonce="n1")
                    r2 = complete("p", rol="rapido", nonce="n1")
        self.assertFalse(r1["cached"])
        self.assertTrue(r2["cached"])
        self.assertEqual(r2["text"], "hola")
        self.assertEqual(len(calls), 1)

    def test_nonce_distinto_es_miss(self):
        calls = []

        def fake_run_once(model, message, timeout):
            calls.append(message)
            return {"text": "x", "tokens_in": 1, "tokens_out": 1}

        with mock.patch.object(llm, "_run_once", side_effect=fake_run_once):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                with _tmp_paths(Path(td)):
                    complete("p", rol="rapido", nonce="a")
                    complete("p", rol="rapido", nonce="b")
        self.assertEqual(len(calls), 2)

    def test_usage_log_solo_llamada_real(self):
        import tempfile

        def fake_run_once(model, message, timeout):
            return {"text": "ok", "tokens_in": 7, "tokens_out": 1}

        with mock.patch.object(llm, "_run_once", side_effect=fake_run_once):
            with mock.patch.object(llm, "_usage_log") as slog:
                with tempfile.TemporaryDirectory() as td:
                    with _tmp_paths(Path(td)):
                        complete("p", rol="rapido", nonce="z")
                        complete("p", rol="rapido", nonce="z")  # hit: no vuelve a "llamar"
        # miss real pasa por _real_complete (que llama _usage_log dentro de _run_once
        # parcheado: acá medimos que el hit NO re-invoca _run_once)
        self.assertEqual(slog.call_count, 0)  # _run_once fake no loguea por defecto
        # y el contrato de _usage_log real: linea valida
        with tempfile.TemporaryDirectory() as td:
            with _tmp_paths(Path(td)):
                llm._usage_log("m", 7, 1, 0.01, True)
                usage = Path(td) / "cache" / "llm_usage.jsonl"
                rec = json.loads(usage.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["tokens_in"], 7)
        self.assertTrue(rec["ok"])

    def test_run_once_real_loguea_usage(self):
        import tempfile
        from types import SimpleNamespace

        proc = SimpleNamespace(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"),
                               stderr="")
        with tempfile.TemporaryDirectory() as td:
            with _tmp_paths(Path(td)):
                with mock.patch.object(llm.subprocess, "run", return_value=proc):
                    with mock.patch.object(llm, "_opencode_exe", return_value="opencode"):
                        out = llm._run_once("opencode/mimo-v2.6-flash-free", "p", 10)
                usage = Path(td) / "cache" / "llm_usage.jsonl"
                rec = json.loads(usage.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(out["text"], "OK")
        self.assertEqual(out["tokens_in"], 11301)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["tokens_in"], 11301)


class TestReintentos(unittest.TestCase):
    def test_permanente_no_reintenta_y_no_cae_a_respaldo(self):
        llamadas = []

        def fake_run_once(model, message, timeout):
            llamadas.append(model)
            raise LLMError("403 FreeTier", code=403)

        import tempfile
        with mock.patch.object(llm, "_run_once", side_effect=fake_run_once):
            with mock.patch("retry_policy.time.sleep"):
                with tempfile.TemporaryDirectory() as td:
                    with _tmp_paths(Path(td)):
                        with self.assertRaises(LLMError) as ctx:
                            complete("p", rol="rapido", nonce="perm")
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(len(llamadas), 1)  # sin reintento, sin respaldo

    def test_transitorio_reintenta_sin_dormir_real(self):
        llamadas = []

        def fake_run_once(model, message, timeout):
            llamadas.append(model)
            if len(llamadas) < 3:
                raise LLMError("504 A Timeout Occurred", code=504)
            return {"text": "ok", "tokens_in": 1, "tokens_out": 1}

        sleeps = []
        import tempfile
        with mock.patch.object(llm, "_run_once", side_effect=fake_run_once):
            with mock.patch("retry_policy.time.sleep", side_effect=sleeps.append):
                with tempfile.TemporaryDirectory() as td:
                    with _tmp_paths(Path(td)):
                        r = complete("p", rol="rapido", nonce="trans")
        self.assertEqual(r["text"], "ok")
        self.assertEqual(len(llamadas), 3)
        self.assertEqual(len(sleeps), 2)  # backoff parcheado, no duerme de verdad

    def test_caida_al_respaldo_tras_agotar(self):
        llamadas = []

        def fake_run_once(model, message, timeout):
            llamadas.append(model)
            if "fake-rapido" in model:  # primario (conftest) agota siempre
                raise LLMError("504", code=504)
            return {"text": "del respaldo", "tokens_in": 5, "tokens_out": 2}

        import tempfile
        with mock.patch.object(llm, "_run_once", side_effect=fake_run_once):
            with mock.patch("retry_policy.time.sleep"):
                with tempfile.TemporaryDirectory() as td:
                    with _tmp_paths(Path(td)):
                        r = complete("p", rol="rapido", nonce="fb")
        self.assertEqual(r["text"], "del respaldo")
        self.assertIn("fake-respaldo", r["model"])  # respaldo_rapido (conftest)
        # F2.0: el motor reintenta MOTOR_INTENTOS (default 3, no INTENTOS_MAX=5)
        # por candidato: 3 en el primario + 1 en el respaldo.
        self.assertEqual(len(llamadas), llm._intentos_motor() + 1)

    def test_rol_invalido(self):
        with self.assertRaises(ValueError):
            complete("p", rol="no-existe")


class TestMock(unittest.TestCase):
    def test_mock_sin_red(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "mock.json"
            table.write_text(
                json.dumps({"Respondé exactamente: OK": "OK"}, ensure_ascii=False),
                encoding="utf-8",
            )
            mock_file = str(table)
            with mock.patch.dict("os.environ", {"MOTOR_LLM": "mock", "MOTOR_MOCK_FILE": mock_file}):
                with mock.patch.object(llm, "_run_once",
                                       side_effect=AssertionError("no debe llamar a opencode")):
                    with tempfile.TemporaryDirectory() as cache_td:
                        with _tmp_paths(Path(cache_td)):
                            r = complete("Respondé exactamente: OK", rol="rapido", nonce="m")
        self.assertEqual(r["text"], "OK")
        self.assertFalse(r["cached"])


class TestFixWindows(unittest.TestCase):
    """PASO 6: BUG 1 (.cmd trunca multilinea) y BUG 2 (cwd dentro del repo)."""

    def test_opencode_exe_resuelve_exe_real_cuando_which_da_cmd(self):
        with tempfile.TemporaryDirectory() as td:
            npm = Path(td)
            cmd = npm / "opencode.cmd"
            cmd.write_text("@echo off\r\n", encoding="utf-8")
            exe = npm / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"MZ-fake")
            with mock.patch.object(llm.shutil, "which",
                                   side_effect=lambda n: str(cmd) if n == "opencode" else None):
                got = llm._opencode_exe()
        self.assertEqual(got, str(exe))
        self.assertTrue(got.lower().endswith(".exe"))

    def test_opencode_exe_sin_exe_real_usa_el_cmd(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = Path(td) / "opencode.cmd"
            cmd.write_text("@echo off\r\n", encoding="utf-8")
            with mock.patch.object(llm.shutil, "which",
                                   side_effect=lambda n: str(cmd) if n == "opencode" else None):
                got = llm._opencode_exe()
        self.assertEqual(got, str(cmd))

    def test_cwd_del_llm_esta_fuera_del_repo(self):
        cwd = llm._LLM_CWD.resolve()
        root = ROOT.resolve()
        self.assertFalse(str(cwd).startswith(str(root)),
                         f"_LLM_CWD ({cwd}) NO debe estar dentro del repo ({root})")
        self.assertEqual(cwd.name, "siafb_llm_cwd")

    def test_run_once_usa_cwd_fuera_del_repo_y_exe(self):
        """_run_once arma el cmd con el .exe resuelto y cwd fuera del repo."""
        import tempfile as tf
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as td:
            fake_exe = str(Path(td) / "opencode.exe")
            proc = SimpleNamespace(returncode=0,
                                   stdout=FIXTURE.read_text(encoding="utf-8"),
                                   stderr="")
            with _tmp_paths(Path(td)):
                with mock.patch.object(llm, "_opencode_exe", return_value=fake_exe):
                    with mock.patch.object(llm.subprocess, "run", return_value=proc) as run:
                        llm._run_once("opencode/mimo-v2.6-flash-free",
                                      "Linea uno.\nLinea dos: ZANAHORIA", 10)
            cmd = run.call_args[0][0]
            kwargs = run.call_args[1]
        self.assertEqual(cmd[0], fake_exe)
        self.assertIn("Linea uno.\nLinea dos: ZANAHORIA", cmd)
        cwd = Path(kwargs["cwd"]).resolve()
        self.assertFalse(str(cwd).startswith(str(ROOT.resolve())))


@unittest.skipUnless(
    os.environ.get("MOTOR_SMOKE") == "1",
    "smoke real: MOTOR_SMOKE=1 python -m pytest tests/test_llm.py -k smoke",
)
class TestSmokeReal(unittest.TestCase):
    def test_prompt_multilinea_ve_la_linea_2(self):
        """BUG 1 en vivo: la 2da linea del prompt debe llegar al modelo.

        La palabra secreta esta SOLO en la linea 2: si cmd.exe trunca el
        argumento multilinea, el modelo no puede saberla.
        """
        r = complete(
            "Linea uno: este es un mensaje de prueba ordinario, sin accion.\n"
            "Linea dos: la palabra secreta es ZANAHORIA. "
            "Respondé solo con esa palabra, sin nada mas.",
            rol="rapido",
            nonce="smoke-multilinea-paso6-v2",
        )
        self.assertIn("ZANAHORIA", r["text"])
        tok = r["tokens_in"]
        self.assertLess(tok, 10000, "tokens_in=" + str(tok) + ": probablemente cargo el repo")


if __name__ == "__main__":
    unittest.main()


def test_env_llm_fuerza_pwd_fuera_del_repo(monkeypatch):
    """BUG 3: opencode usa PWD heredado por encima del cwd -> debe apuntar al cwd temporal."""
    monkeypatch.setenv("PWD", str(llm._ROOT))
    monkeypatch.setenv("OLDPWD", str(llm._ROOT))
    env = llm._env_llm()
    assert env["PWD"] == str(llm._LLM_CWD)
    assert env["INIT_CWD"] == str(llm._LLM_CWD)
    assert "OLDPWD" not in env
    assert not str(llm._LLM_CWD).startswith(str(llm._ROOT))


def test_backend_ollama_despacha_local_y_semilla_del_nonce(monkeypatch):
    llamados = {}

    def fake_ollama(model, message, timeout, nonce=None):
        llamados["model"], llamados["nonce"] = model, nonce
        return {"text": "ok", "tokens_in": 1, "tokens_out": 1}

    monkeypatch.setattr(llm, "_run_ollama", fake_ollama)
    out = llm._una_llamada("ollama/qwen2.5:1.5b", "hola", 5, "N1")
    assert out["text"] == "ok" and llamados == {"model": "ollama/qwen2.5:1.5b", "nonce": "N1"}
    assert llm._seed_de("N1") == llm._seed_de("N1") != llm._seed_de("N2")


def test_respaldo_vacio_por_rol_no_cae_a_otro_modelo(monkeypatch):
    monkeypatch.setattr(llm, "_load_models", lambda: {
        "rapido": "ollama/qwen2.5:1.5b", "respaldo_rapido": "",
        "reflector": "openrouter/x:free", "respaldo_reflector": "atria/y",
    })
    assert llm._model_for("rapido") == ["ollama/qwen2.5:1.5b"]
    assert llm._model_for("reflector") == ["openrouter/x:free", "atria/y"]
