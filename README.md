# Self-Improving Business Agent (beta publica beta-1.1)

> Beta beta-1.1: la ingenieria esta probada en produccion privada; los numeros y
> filas de ejemplo son sinteticos (demo, NO verdad humana), sin SLA.
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
