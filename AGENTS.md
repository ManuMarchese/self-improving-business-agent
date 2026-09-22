# <TU-PROYECTO>

Agente de negocio con capacidad de auto-mejora.

## Estructura
- `README.md` — descripcion del proyecto
- `AGENTS.md` — instrucciones para agentes (este archivo)
- `opencode.json` — config del proyecto para opencode

## Convenciones
- Proponer plan antes de cambios grandes.
- Verificar cada paso antes de avanzar.
- No inventar datos: usar solo fuentes reales y verificables.
- Commits pequeños y mensajes concisos.

## Reanudacion (cualquier sesion nueva, cualquier modelo)

El estado NO vive en el contexto de la conversacion: vive en `RUNSTATE.md` (raiz).
Este archivo se lee siempre al arrancar una sesion sobre este proyecto.

**Triggers del usuario:**
- `"segui"` / `"continua"` / `"dale"` → protocolo de reanudacion y ejecutar el NEXT STEP directo.
- `"en que estabamos"` / `"en que vamos"` / `"que hay"` → protocolo de reanudacion, RESUME en ≤6 lineas,
  proponer proximo paso y pedir confirmacion. NO tocar archivos antes del OK.
- Cualquier otra cosa sin contexto → empezar por el protocolo igual (nunca asumir que el usuario trae contexto).

**Protocolo de reanudacion (en este orden):**
1. Leer `RUNSTATE.md` completo. Si indica un brief activo, leer `runs/<slug>/brief.md`.
2. Reconciliar contra disco real: `git status`, mtimes de `Archivos tocados`. Si el doc contradice el disco,
   corregir `RUNSTATE.md` con lo que la realidad dice.
3. Resumir: objetivo, paso actual M de N, ultimo resultado, proxima decision pendiente.
4. `"segui"` → ejecutar NEXT STEP y persistir el turnpoint con la tool de edicion DESPUES de cada paso.
5. `"en que estabamos"` → proponer el proximo paso y esperar confirmacion.
6. Divergencia irreconciliable doc↔disco → reportar ambas versiones, NUNCA adivinar.

**Regla de escritura (invariante):** nunca terminar un paso sin actualizar `RUNSTATE.md`.
Persistir el Estado ANTES de cada paso dificil/irreversible. Un paso es re-ejecutable sin daño (idempotente).

**Gates (lo unico que frena una corrida):** gastar dinero, enviar mensajes, publicar, borrar informacion,
modificar sistemas externos, usar credenciales, acciones irreversibles. Todo lo demas corre solo.

**Un agente, un proyecto:** el estado, las metricas y los costos de este agente se acotan a la carpeta
de ESTE repo. Sesiones que corrieron en otras carpetas son de OTRO proyecto: no se citan aqui.

**Presupuesto de tokens:** cada corrida lleva estimado en su brief (`python agent.py run budget`) y un
limite de 2x el estimado. Si el meter real lo supera, cortar en el proximo checkpoint. Al cerrar,
`python agent.py run close` mide el uso real y actualiza `costs.md`.
