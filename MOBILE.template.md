# Trabajar esta sesión desde Opencode Mobile

Esta repo ya está lista para usar desde el celular Android.

Detectado en esta PC (<FECHA>):
- opencode `<TU-VERSION>`
- LAN (Wi-Fi): `<TU-IP>`
- Tailscale PC (`<TU-PC>`): `<TU-IP>`
- Tailscale celu (`<TU-CELU>`): `<TU-IP>`, directo activo por `<TU-IP>`
- Puerto: `4096`

El celu y la PC ya están en el mismo tailnet → el método recomendado es **Tailscale**.

## 1. Servidor persistente (sin ventanitas, desde 2026-09-19)

El serve corre como **tarea programada de Windows** `SIAFB-Mobile-Serve`: arranca al iniciar sesión,
corre oculto (cero ventanas), y **se revive solo cada 5 minutos** si muere (probado matando el
proceso a mano). Desde 2026-09-21 escucha SOLO en la IP Tailscale (`<TU-IP>:4096`):
cero exposición LAN. La URL del celu/tablet NO cambia (misma IP directa). Logs en
`logs/serve-mobile.log` (nunca muestra la clave).

Comandos útiles (PowerShell, como usuario normal, sin admin):

```powershell
.\scripts\check-mobile.ps1                          # diagnóstico en 5 segundos
Start-ScheduledTask -TaskName "SIAFB-Mobile-Serve"   # arrancar ya (si está caído, no espera los 5 min)
Stop-ScheduledTask -TaskName "SIAFB-Mobile-Serve"    # detener (más: matar el proceso opencode serve)
Get-ScheduledTask -TaskName "SIAFB-Mobile-Serve"     # ver estado
Get-Content logs\serve-mobile.log -Tail 10           # últimas líneas del log
```

Notas:
- Credenciales en `.env.mobile` (nunca se commitea). Si cambia la clave, reiniciar la tarea.
- Requisito honest: PC prendida + sesión iniciada. Sin login no hay serve (AtStartup pediría tu
  clave de Windows al Scheduler: no se hace).
- El 2026-09-20 se jubiló `opencode-serve.vbs` del Startup (ataba el puerto con la clave VIEJA y
  bloqueaba a la tarea; backup en `~/.siafb-backup/`). También se borró la clave vieja de entorno.
  Si algún día vuelve un okupa, `serve-prebind.ps1` lo desaloja solo al arrancar.
- `tailscale serve` (URL MagicDNS) NO está habilitado en este tailnet (pide admin): usar IPs directas.
  Extra 2026-09-21: el comando `tailscale serve --bg` además SE CUELGA en esta PC (ver serve-mobile.log);
  el script ya no lo intenta (acceso remoto = IP Tailscale directa). Si <TU-NOMBRE> lo habilita en la consola
  admin, avisar para reactivar el túnel en `serve-mobile-task.ps1`.
- Método viejo con ventana (`start-mobile.ps1` / `stop-mobile.ps1`) queda como fallback manual.

## 1b. Iniciar el servidor (método manual con ventana, fallback)

Solo si la tarea programada no existe o falla. En PowerShell, desde la raíz:

```powershell
.\scripts\start-mobile.ps1          # Tailscale (escucha 127.0.0.1 + tailscale serve)
.\scripts\start-mobile.ps1 -Mode LAN # misma Wi-Fi (escucha 0.0.0.0)
.\scripts\stop-mobile.ps1            # dejar de exponer por Tailscale
```

OJO: este método muere si se cierra la ventana. Para persistencia real, volver a la sección 1
(registrar la tarea con los comandos de `scripts/serve-mobile-task.ps1` como acción).

## 2. Conectar la app (getopencode.app / OpenCode Mobile)

1. En Android abrí Tailscale y conectalo al mismo tailnet.
2. Abrí Opencode Mobile → Settings → Connection.
3. Server URL (probar en este orden):
   - `http://<TU-IP>:4096` (IP Tailscale directa, lo más estable)
   - o la URL HTTPS que muestra `tailscale serve status` (ej. `https://<TU-PC>.<tailnet>.ts.net`)
   - fallback LAN misma Wi-Fi: `http://<TU-IP>:4096`
4. Username: valor de `OPENCODE_SERVER_USERNAME` en `.env.mobile` (por defecto `opencode`).
5. Password: valor de `OPENCODE_SERVER_PASSWORD` en `.env.mobile`.
6. Tap Reconnect → debe decir Connected.
7. Workspace → elegí este proyecto (`<TU-PROYECTO>`).
8. Chat → abrí o creá una sesión y mandá una prueba sin cambios:
   `Inspeccioná este workspace y resumí su propósito. No cambies archivos.`

Notas de compatibilidad:
- Si usás `opencode serve`, la raíz ES la API. No agregues `/api`.
- Si alguna vez usás `opencode web` y la raíz devuelve HTML o 404, ahí sí la API suele estar en `<url>/api`.
- Verificación rápida desde el navegador del celu: abrir `<ServerURL>/global/health` debe devolver `{"healthy":true,...}`.

## 3. Si usás otro cliente (dzianisv/opencode-mobile, F-Droid)

Mismo servidor, cambia la pantalla de conexión:
- Add Connection → Local network o Tailscale/Tunnel según caso.
- URL: las mismas de arriba. Password: la de `.env.mobile`.

## 4. Seguridad

- Nunca commitear `.env.mobile` (ya está en `.gitignore`).
- Mantener auth siempre activada, incluso con Tailscale/HTTPS.
- En LAN: solo red privada, firewall de Windows activo, nunca abrir el puerto en el router.
- Si perdés el celu: rotá la password en `.env.mobile`, detené el servidor y apagá `tailscale serve`.
- Las credenciales quedan guardadas en el celu (AsyncStorage / Keystore según app) → usá bloqueo de pantalla y cifrado.

## 4b. Salir de casa (roaming con Tailscale)

- La URL `http://<TU-IP>:4096` **no cambia nunca**: las IPs `100.x` son de la red virtual de
  Tailscale, no de tu Wi-Fi. Andan con Wi-Fi de casa, de otro lado o datos móviles.
- Al cambiar de red hay un micro-corte (los sockets TCP viejos mueren): la app suele reconectar sola;
  si no, botón Reconnect. Es normal, no es caída del servidor.
- Condiciones: Tailscale activo en el celu Y en la PC (los dos nodos en `tailscale status`), y el
  serve corriendo en la PC (`.\scripts\check-mobile.ps1` → TODO OK).
- En redes raras (Wi-Fi público, datos con NAT estricta) Tailscale puede ir por relays (más lento pero
  conecta igual). Si va muy lento, prefiere datos móviles a Wi-Fi público.

## 5. Troubleshooting

- `404 / HTML / JSON parse error` → estás apuntando a una web UI, no a la API. Verificá `/global/health` exacto.
- `Auth failed` → compará usuario/clave con `.env.mobile`, Reconnect después de editar. Si el serve se
  reinició sin `.env.mobile`, la clave del celu ya no vale: leé la nueva de `.env.mobile` en la PC.
- La app dice "desconectado" pero el servidor está vivo → es corte a nivel app (SSE), no del serve.
  Verificá con `netstat -ano | Select-String 4096`: si ves `ESTABLISHED` con la IP del celu, el servidor
  está bien → Reconnect desde la app (o cerrarla y reabrirla). Causa típica: cambio Wi-Fi↔datos o
  Android durmiendo el socket (sacar optimización de batería a ambas apps).
- No conecta por MagicDNS pero sí por IP → `tailscale serve` requiere habilitar Serve en el admin del
  tailnet; sin eso solo funcionan las IPs directas (Tailscale IP y LAN).
- No conecta en LAN → misma Wi-Fi en ambos, `ping <TU-IP>`, regla de Firewall para puerto 4096 en perfil privado.
- SSE/terminal cortados detrás de proxy → el proxy debe reenviar streaming y WebSockets.
- Diagnóstico en 5 segundos (desde la PC): `.\scripts\check-mobile.ps1` → puerto, auth y tailnet.
- `opencode` en Windows es un shim `.ps1` (npm), NO un exe: `Start-Process opencode` falla. Lanzar con
  `Start-Process powershell -ArgumentList "-NoExit","-Command","opencode serve ..."`.
- Las env (`OPENCODE_SERVER_*`) no cruzan entre shells: setear, lanzar y testear deben ir en UN solo comando.
- `Get-NetTCPConnection` una vez dio falso negativo (puerto vacío con el serve vivo): ante duda, `netstat -ano`.

Referencias: `https://getopencode.app/docs/getting-started/`, `https://opencode.ai/docs/server/`.
