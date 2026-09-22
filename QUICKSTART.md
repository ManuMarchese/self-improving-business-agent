# QUICKSTART (5 minutos)

0. Requisitos: Python 3.10+, PowerShell 5.1+, `git`. Cero dependencias extra
   (stdlib-only). 5 minutos de reloj.
1. `powershell -ExecutionPolicy Bypass -File scripts/install-beta.ps1`
2. `python agent.py status` — ver tareas demo.
3. `python agent.py golden build` — validar el Golden Set sintetico (tiene que decir 0 problemas).
4. `python agent.py run new "mi primera corrida" --hours 2` — abrir una corrida.
5. `python agent.py run budget --slug <slug> --searches 0 --ig 0 --posts 0 --readkb 5`
6. Trabaja por pasos; cierra con `run close` y `run report`.
7. Cerra la sesion y abri otra: deci "en que estabamos" — el agente retoma sin contexto.
8. `python agent.py doctor` antes de cada push. Verde = push permitido.

Si algo falla 3 veces igual: para, lee el error, no insistas en rafaga.
