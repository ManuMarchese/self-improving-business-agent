# Self-Improving Business Agent (beta publica beta-1.2)

> Agente que aprende de vos y no te chamuya: cada propuesta de mejora
> pasa por un juez estadistico y un examen que el sistema nunca ve. Sin dependencias
> (stdlib), alumno 100% local via Ollama. Beta, sin SLA.

## El motor que aprende (lo nuevo en beta-1.2)

```
python -m motor eval    --task T --policy ID --split dev       # nota con IC 95% + integridad
python -m motor compare --a results_A.jsonl --b results_B.jsonl # juez pareado: ACEPTA / RECHAZA
python -m motor learn   --task T --parent v0 --rondas 3 --nonce R1   # ciclo ACE
python -m motor skills  --task T --parent v0 --desde runs_motor/<...-train>/results.jsonl
```

Probalo sin red: `set MOTOR_LLM=mock` y `set MOTOR_MOCK_FILE=tasks/eco_mock/mock.json`, despues
`python -m motor seal --task eco_mock` y `python -m motor eval --task eco_mock --policy v0 --split dev`.
Con modelo local: `ollama pull qwen2.5:1.5b` (ver `motor/models.json`).

Defensas contra la trampa, cada una con test: examen sellado por hash (exit 3), registro
encadenado que detecta insercion/edicion/truncado, corrector que exige tipos nativos (anti
objetos con `__eq__` trucho), guardia que anula la corrida si el modelo toca el repo (exit 5),
caidas del proveedor fuera del puntaje (exit 4).

**Resultados reales (honestos):** con un alumno de 1.5B, 4 de 4 propuestas de mejora fueron
rechazadas por el juez (reglas ACE y biblioteca de habilidades), y la decision tomada en dev
coincidio con el examen externo. El sistema mide sin enganarse; todavia no demostro aprender.
Detalle: docs/METODOLOGIA.md, docs/RESULTADOS.md y CHANGELOG.md.

> Los numeros y filas del golden de negocio son sinteticos (demo, NO verdad humana).
> ToS Instagram/Meta: usa solo API oficial Meta; el scraping riesgoso (bloqueos,
> 429, baja de cuenta) esta prohibido en este proyecto; respetar cuotas
> (Discovery 200/h, hashtag 30/7d con tope operativo 25/30).

Agente de negocio con auto-mejora, operado por archivos (el estado vive en disco, no en la ventana
de contexto). Investiga con subagentes, prospecta con evidencia y se evalua con un Golden Set con
veto: ninguna regla cambia sin pasar el examen. Sin dependencias (stdlib-only), sin nube obligatoria.

Licencia: MIT (ver `LICENSE`).

## Instalacion guiada (5 minutos)

1. `powershell -ExecutionPolicy Bypass -File scripts/install-beta.ps1` — verifica Python,
   crea credenciales locales y corre la auditoria.
2. Lee `QUICKSTART.md` — tu primera corrida paso a paso.
3. `python agent.py doctor` — verde antes de cada push.

## Que hace solo / que te pide

- Solo: investigar (lectura), puntuar con evidencia, correr oleadas, medir su autonomia,
  commitear local, revivirse en mobile.
- Te pide (gates): gastar dinero, enviar mensajes, publicar, borrar, credenciales,
  cualquier cosa irreversible. Eso no se automatiza por diseño.

## Estructura

- `agent.py` — CLI stdlib, cero dependencias.
- `AGENTS.md` — reglas y protocolo de reanudacion ("segui" / "en que estabamos").
- `workflows/` — subagentes deterministicos: investigar, prospectar, juzgar, sandbox
  (prompt + ambiente + tools fijos y versionados).
- `RUNSTATE.md` — punto unico de reanudacion (empeza por aca).
- `strategy.md` — loop de mejora + formato KEEP/REJECT.
- `golden/` — framework de evaluacion con veto (datasets DEMO sinteticos).
- `runs/` — templates de brief y closeout.
- `COMO_FUNCIONA.md` — explicacion simple del sistema.

## Defini tu negocio

Edita `RUNSTATE.md` (seccion "Tarea actual"), `tasks.txt` y `strategy.md` con tu
negocio (<TU-NEGOCIO>), tu cliente ideal (<TU-ICP>) y tu oferta. Nada de eso viene incluido.
