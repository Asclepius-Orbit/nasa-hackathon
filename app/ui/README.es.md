[English](README.md) · **Español** · [Català](README.ca.md)

# spine-ui (la app de escritorio de a bordo)

App de escritorio que el astronauta usa en la tablet: el veredicto del día, el report diario, las sesiones de
ejercicio, el historial, el aviso de síntoma urgente y el resumen para Houston. Electron + React + TypeScript,
con Vite y pnpm.

Cada pantalla lee de un `DataSource` tipado (`src/renderer/data/`). Por defecto es el backend (`/ui` en 8000), al que
solo se llega a través del proceso main (modo real). El build `e2e` y `VITE_MOCK_DATA=true` usan el mock de a bordo;
`pnpm start` sin esa variable es modo real y necesita el stack de abajo.

## Requisitos

- Node 24 LTS (`.node-version`; `engines` rechaza otras versiones mayores).
- pnpm mediante corepack: `corepack enable pnpm`. La versión exacta (12.16.1, con su hash) sale de
  `packageManager` en `package.json`. Para Node se usa solo pnpm; nunca npm ni npx.

## Arrancarla

```bash
cd app/ui
pnpm start          # desde la raíz del repo: pnpm -C app/ui start
```

`pnpm start` instala lo que falte (con el lockfile congelado), descarga el binario de Electron si hace falta y
abre la app en modo desarrollo con recarga en caliente.

En desarrollo (y solo en las builds de e2e) existen dos tripulantes de demo: **Elena Ruiz** y **Marc Dubois**,
PIN **1234**. El menú "Mock screens" y "Mock: skip login or lock" también son solo de desarrollo. Una build de
producción arranca sin tripulantes: el primero se da de alta con "New crew member". Los arneses de los tripulantes de
demo son los del simulador (`sim-01` y `sim-02`, como en `spine_monitor/physiology/configs/example-mission.yaml`).
**Sign out** (en la franja superior y en la pantalla de bloqueo) cierra la sesión y vuelve al login, para que otro
tripulante use la tablet; también detiene el contador de repeticiones en vivo y cualquier chat abierto.

## Ejecutar la demo completa

Simulador, Mosquitto, engine, backend y la app en modo real, desde la raíz del repo (Docker, y `sh` de Git Bash en
Windows):

```bash
sh app/mosquitto/setup.sh                                          # una vez: credenciales del broker en app/.env
export SPINE_APP_UI_TOKEN=$(openssl rand -hex 24)                  # token compartido de la API /ui (o vacío, ver abajo)
export SPINE_APP_BASELINE_REQUIRED=1                               # opcional: una sesión preflight completa la baseline (por defecto 3)
docker compose -f app/docker-compose.yml --profile sim up -d --build
curl http://127.0.0.1:8000/health                                  # espera "mqtt_connected": true
ASCLEPIUS_BACKEND_TOKEN=$SPINE_APP_UI_TOKEN pnpm -C app/ui start   # modo real: sin VITE_MOCK_DATA
```

Sin `SPINE_APP_UI_TOKEN` el backend genera un token en su volumen; pásalo a la app con `ASCLEPIUS_BACKEND_TOKEN_FILE`
(una copia de `/data/ui-token`, por ejemplo con `docker compose cp backend:/data/ui-token .`). El token llega a la app
por el entorno del proceso, igual en Windows y en Linux. Entra como Elena Ruiz (PIN 1234), guarda el perfil con una
fecha de lanzamiento posterior a hoy (así hoy es preflight), guarda el informe diario e inicia la sesión
"Spine length check": el arnés simulado responde y emite durante unos 2 minutos. Para parar todo:
`docker compose -f app/docker-compose.yml --profile sim down`.

`pnpm e2e:real` hace todo esto sin supervisión y lo comprueba (solo en local, no en CI): ejecuta `setup.sh`, levanta
el stack en su propio proyecto de Compose (`asclepius-e2e`, que se descarta con `down -v` al acabar), espera al
backend y al arnés simulado, compila con `--mode e2e-real` (fuente de datos real, tripulación de demo), ejecuta
`e2e/real.spec.ts` y siempre baja el stack (`E2E_REAL_KEEP=1` lo conserva). Necesita Docker, `sh` y los puertos
1883, 8000 y 8001 libres.

## Scripts

| Script | Qué hace |
|---|---|
| `pnpm start` | Instala, descarga Electron y arranca la app en desarrollo |
| `pnpm dev` | Solo la app en desarrollo (da por instaladas las dependencias) |
| `pnpm build` | Build de producción en `out/` |
| `pnpm dist` | Instalador de Windows (NSIS, sin firmar) en `release/` |
| `pnpm test` / `pnpm test:cov` | Tests unitarios y de componentes con Vitest (cobertura ≥ 90 % de líneas, ramas, funciones y sentencias) |
| `pnpm e2e` | Compila en modo `e2e` y ejecuta los flujos de Playwright contra la app real |
| `pnpm e2e:real` | La demo completa sin supervisión: levanta el stack de Docker (simulador, broker, engine, backend), compila en modo `e2e-real` y ejecuta `e2e/real.spec.ts` en modo real (solo local) |
| `pnpm e2e:packaged` | Empaqueta la app (`--dir`) y comprueba que el exe empaquetado muestra el login |
| `pnpm lint` / `pnpm typecheck` | ESLint (estricto, sin warnings) y `tsc -b` |
| `pnpm gen:types` / `pnpm check:types` | Regenera / comprueba `src/contracts/v1.ts` a partir de los JSON Schema |
| `pnpm check:bundle` | Falla si llega código de desarrollo (tripulantes de demo, URL del servidor de desarrollo, CSP relajada, devtools) al bundle de producción |
| `pnpm check` | El gate completo: lint, tipos, tipos de contratos, cobertura, `pnpm audit` (high) y `check:bundle` |

El mismo gate se ejecuta en Docker y en CI:

```bash
sh app/mosquitto/setup.sh   # una vez: credenciales del broker en app/.env
docker compose -f app/docker-compose.yml --profile quality run --rm ui-quality
```

## Estructura

```
src/main/       Proceso main de Electron: ventana endurecida, protocolo app://, IPC, PIN, almacenes cifrados
src/preload/    El único puente: window.asclepius (crew, auth, settings, views) y nada más
src/shared/     Contrato del puente (api.ts) y nombres de canales IPC + esquemas zod
src/renderer/   App React: router, layout, pantallas en features/<nombre>/, strings/, data/, components/ui/
src/contracts/  Tipos generados desde spine_monitor/contracts/schemas/v1 (no editar)
e2e/            Playwright: smoke, flujos, capturas visuales, app empaquetada
scripts/        Generador de tipos de contratos, descarga de Electron, comprobación del bundle, fuses (after-pack)
```

Los textos viven en `src/renderer/strings/` (en inglés, un fichero por pantalla) para poder añadir otros idiomas.

## Seguridad

- Renderer: `sandbox`, `contextIsolation`, sin integración de Node, sin webviews, ventanas nuevas denegadas,
  navegación limitada a la app y todos los permisos denegados.
- La interfaz se sirve por un esquema privilegiado `app://asclepius` limitado a `out/renderer`, con una CSP
  estricta (etiqueta meta y cabecera de respuesta). Sin recursos remotos: la fuente va incluida.
- IPC: cada llamada comprueba que el remitente es el frame principal de la propia app y valida la entrada con un
  esquema zod estricto; los errores llegan al renderer como mensajes genéricos.
- PIN: scrypt con salt por tripulante, comprobado en el proceso main. Tres fallos bloquean a ese tripulante cinco
  minutos, y el bloqueo sobrevive a reinicios; el intento se registra antes de comprobar el PIN.
- Credenciales del backend: cada petición a `/ui` lleva el token compartido (`ASCLEPIUS_BACKEND_TOKEN`, o el fichero que indique `ASCLEPIUS_BACKEND_TOKEN_FILE`, p. ej. el `ui-token` que el backend escribe junto a su base de datos) y el astronauta desbloqueado (`X-Astronaut-Id`). Ambos se quedan en el proceso main; sin token el backend responde 401 y las pantallas muestran `unavailable`.
- Vistas del backend: el renderer nunca habla con el backend (el CSP `connect-src 'self'` no cambia). El proceso main pide las vistas `/ui/...` solo del astronauta desbloqueado, por HTTP a loopback (`ASCLEPIUS_BACKEND_URL`, por defecto `http://127.0.0.1:8000`; otros hosts se ignoran), con timeout de 5 s, sin redirecciones y sin caché. Los errores llegan al renderer como `locked`, `not_found` o `unavailable`.
- Registro de tripulantes: el almacén cifrado de la tripulación es dueño de la lista, los ids `AST-NN`, los nombres y los PIN. El proceso main solo comunica al backend cada id (`PUT /ui/crew/{id}`, sin cuerpo), al arrancar y al crear un miembro; si el backend está caído el miembro se crea igual y el id se reintenta en el siguiente listado. El perfil del backend se completa en la pantalla Perfil (abajo); sin él `/assess` se omite.
- Escrituras al backend: el proceso main también escribe solo para el astronauta desbloqueado, con las mismas reglas que las vistas (loopback, token, `X-Astronaut-Id`, timeout de 5 s, sin redirecciones). Una escritura responde con un resultado, nunca con un rechazo: `ok`, o `invalid` con los punteros de campo del backend (422), `locked` (401/403), `conflict` (409) o `unavailable`. La UI dice "Saved" solo con `ok`.
- Perfil y contexto diario (`/profile`, también desde Ajustes): la pantalla edita parte del perfil (fecha de nacimiento, sexo al nacer, alturas, peso, fechas de lanzamiento y aterrizaje) y el contexto de hoy (fase, gravedad, día de misión). Main conserva el resto de campos guardados. En el perfil nunca va un nombre. Si hoy (UTC) no hay contexto y el perfil tiene fecha de lanzamiento, main escribe uno al aceptarse el PIN y al guardar el perfil: la fase y el día de misión salen de las fechas (el día del lanzamiento es el 1, no hay día 0), con microgravedad en vuelo y 1 g fuera. Nunca sobrescribe un contexto guardado. Today y First day piden lo que falte.
- Emparejado del arnés (Settings > Harness): lista los dispositivos que publicaron estado para el astronauta (`GET /ui/crew/{id}/devices`), y el `device_id` elegido se guarda solo en el almacén cifrado de la tripulación. El backend no empareja: la franja muestra el último dispositivo que informó.
- Sesiones (pantalla Session): "Start session" con el protocolo elegido (`lat_pulldown_standing_v1` o `spine_length_rest_v1`). Main manda el arnés emparejado como `device_id`. El protocolo estándar (#12) es un jalón de dorsal de pie con la banda verde (25 kg nominales), 4 series de 15 repeticiones (60 en total), 30 s nominales de descanso entre series, agarre en pronación y ritmo moderado. Main toma el tipo de sesión, la banda, las series y las repeticiones del catálogo de protocolos. Las declaraciones de streams salen de una plantilla por protocolo copiada de los ejemplos del contrato: EMG en cada dorsal ancho (10 cm entre electrodos, de T11 a L2, tierra en la cresta ilíaca), IMU en T9 y el cordón de estiramiento de C3-C4 a L5-S1; el spine check se hace de pie, justo antes y justo después de la sesión. La pantalla de sesión muestra la serie y la repetición dentro de ella ("Set 2 of 4") y un estado de descanso entre series. La calibración MVC, la del cordón (unos 650 mm de cordón) y el descanso entre series son provisionales, pendientes de Aguado. Sin arnés emparejado no se envía nada; sin contexto diario de hoy el backend rechaza (422) y la pantalla lleva a Profile. La pantalla sigue la sesión de `starting` a `running`, `rejected` o el final. Recuerda la última sesión, así que una `processing_failed` se puede reprocesar. Con la sesión en `running`, Finish la para como `completed`, Calibrate pide una calibración MVC del EMG y "Mark an artefact" marca la repetición actual. Stop: pain manda `aborted_pain` con la repetición actual y abre la pantalla Stopped solo si el backend lo guardó.
- Autoinformes: el informe diario y el Oswestry se guardan con `POST self-reports` (tipos `daily` y `weekly_odi`), y el ejercicio del día se añade al contexto diario de hoy. Los seis ítems del Oswestry son provisionales. El motor no los usa: `symptoms.odi_pct` se quitó del catálogo en #11. "Saved" sale solo con `ok`.
- Síntoma urgente: un informe `red_flag`. Con alguien desbloqueado se envía por esa persona. Desde el login o el bloqueo se envía sin PIN para el tripulante elegido en el diálogo: es la única escritura permitida así, por ser una emergencia, y no se lee nada. Solo muestra Queued si se guardó.
- Pantalla Stopped: la repetición en la que se registró la parada, el enlace con tierra, la llamada al oficial médico (`POST contacts`, que también va en el resumen para Houston) y los contactos registrados. No hay encuesta tras la parada: los síntomas van por Urgent y el dolor por el informe diario.
- Pantalla Ask (chat con el agente de columna): `POST /chat` como flujo server-sent, solo desde el proceso main. La URL del agente es `ASCLEPIUS_AGENT_URL` (solo loopback, por defecto `http://127.0.0.1:8003`) y su token `ASCLEPIUS_AGENT_TOKEN` o el fichero `ASCLEPIUS_AGENT_TOKEN_FILE`; ninguno llega al renderer. El astronauta es el desbloqueado aquí, nunca un argumento; los argumentos de las tools del modelo se descartan en main (solo se muestra el nombre de la tool). No hay reintento: una respuesta fallida la repite el usuario. Las primeras palabras pueden tardar hasta un minuto en CPU (se admiten hasta 120 s de silencio). El asistente no diagnostica y la pantalla lo dice; un `fallback` se muestra como "Summary without the assistant". Bloquear o cerrar sesión corta las respuestas en curso; "New conversation" olvida la anterior en el agente. En modo de datos mock una respuesta de ejemplo guionizada sustituye al agente.
- El estado de tripulantes y de autenticación se cifra con `safeStorage` de Electron y falla cerrado si no hay
  cifrado disponible.
- Fuses de Electron: sin `RunAsNode`, sin `NODE_OPTIONS`, sin flags de inspect, integridad del asar y carga solo
  desde el asar.
- Cadena de suministro: versiones exactas, lockfile, scripts de build permitidos solo para `electron` y una
  antigüedad mínima de publicación de un día (`pnpm-workspace.yaml`).

Límites conocidos, para trabajo posterior: PIN de 4 dígitos; instalador por usuario y sin firmar; el exe empaquetado acepta
`--remote-debugging-port`.
