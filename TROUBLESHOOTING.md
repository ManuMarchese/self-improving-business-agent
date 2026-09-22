# TROUBLESHOOTING (beta)

Guia rapida cuando algo falla. Regla general: si algo falla 3 veces igual
(3-fallos), para y diagnostica; no insistas en rafaga.

## doctor rojo

- `python agent.py doctor` dice que falla: lee la linea exacta (secretos /
  higiene / integridad / skeleton). Verde = push permitido; rojo = frena el
  push por diseno.
- `skeleton desactualizado`: regenera con `python agent.py skeleton` (el
  generador es determinista: dos regens seguidas dan bytes identicos).

## golden build falla

- `python agent.py golden build` con problemas: lee la fila MAL (id / task /
  weight / decision / score). Las filas son inmutables: corregir = agregar fila
  nueva con `supersedes`.
- VETO en `golden diff`: regresion en peso 3 = KEEP bloqueado (el veredicto
  humano manda).

## 429 / limite de Meta (rate limit)

- HTTP 429 o "limite de Meta alcanzado": esperar y reintentar con backoff
  (tope 75s); hashtag 30/7d (tope operativo 25/30), Discovery 200/h,
  Voyage 1 req/min. Cuota escasa = esperar, no rafaguear.

## Auth (401 / clave)

- `Auth failed` o 401 sin auth = auth activa (esperado). Compara usuario/clave
  con `.env.mobile` y reconecta. Sin `.env.mobile` no hay serve protegido:
  no arrancar desprotegido.

## run close no registra costos

- `run close` aborta costs si no hay sesion `RUN:<slug>` (el meter solo ve
  sesiones delegadas). Documenta el costo narrado en el closeout.

## Si nada alcanza

- Corre `doctor`, guarda la salida, y pedi ayuda pegando: comando, esperado,
  real y `doctor` (sin secretos).
