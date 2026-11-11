[English](README.md) · **Español** · [Català](README.ca.md)

# spine-ui (la app de escritorio de a bordo)

App de escritorio que el astronauta usa en la tablet: el veredicto del día, el report diario, las sesiones de
ejercicio, el historial, el aviso de síntoma urgente y el resumen para Houston. Electron + React + TypeScript,
con Vite y pnpm.

Los datos son **mock por ahora**: cada pantalla lee de un `DataSource` tipado (`src/renderer/data/`) con los
valores del mock. Conectarlo a las APIs HTTP de la app (`/ui` en 8000 y la de solo lectura en 8002) es otro trabajo.

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

En desarrollo (y solo en la build de e2e) existen dos tripulantes de demo: **Elena Ruiz** y **Marc Dubois**,
PIN **1234**. El menú "Mock screens" y "Mock: skip login or lock" también son solo de desarrollo. Una build de
producción arranca sin tripulantes: el primero se da de alta con "New crew member".

## Scripts

| Script | Qué hace |
|---|---|
| `pnpm start` | Instala, descarga Electron y arranca la app en desarrollo |
| `pnpm dev` | Solo la app en desarrollo (da por instaladas las dependencias) |
| `pnpm build` | Build de producción en `out/` |
| `pnpm dist` | Instalador de Windows (NSIS, sin firmar) en `release/` |
| `pnpm test` / `pnpm test:cov` | Tests unitarios y de componentes con Vitest (cobertura ≥ 90 % de líneas, ramas, funciones y sentencias) |
| `pnpm e2e` | Compila en modo `e2e` y ejecuta los flujos de Playwright contra la app real |
| `pnpm e2e:packaged` | Empaqueta la app (`--dir`) y comprueba que el exe empaquetado muestra el login |
| `pnpm lint` / `pnpm typecheck` | ESLint (estricto, sin warnings) y `tsc -b` |
| `pnpm gen:types` / `pnpm check:types` | Regenera / comprueba `src/contracts/v1.ts` a partir de los JSON Schema |
| `pnpm check:bundle` | Falla si llega código de desarrollo (tripulantes de demo, URL del servidor de desarrollo, CSP relajada, devtools) al bundle de producción |
| `pnpm check` | El gate completo: lint, tipos, tipos de contratos, cobertura, `pnpm audit` (high) y `check:bundle` |

El mismo gate se ejecuta en Docker y en CI:

```bash
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
- Registro de tripulantes: el almacén cifrado de la tripulación es dueño de la lista, los ids `AST-NN`, los nombres y los PIN. El proceso main solo comunica al backend cada id (`PUT /ui/crew/{id}`, sin cuerpo), al arrancar y al crear un miembro; si el backend está caído el miembro se crea igual y el id se reintenta en el siguiente listado. El perfil del backend se completa aparte (`PUT /ui/crew/{id}/profile`); sin él `/assess` se omite.
- El estado de tripulantes y de autenticación se cifra con `safeStorage` de Electron y falla cerrado si no hay
  cifrado disponible.
- Fuses de Electron: sin `RunAsNode`, sin `NODE_OPTIONS`, sin flags de inspect, integridad del asar y carga solo
  desde el asar.
- Cadena de suministro: versiones exactas, lockfile, scripts de build permitidos solo para `electron` y una
  antigüedad mínima de publicación de un día (`pnpm-workspace.yaml`).

Límites conocidos, para trabajo posterior: la autorización vive en el renderer mientras los datos sean mock (los
datos de salud reales los tendrá que servir el proceso main, comprobados contra su sesión); no hay cierre de sesión
ni cambio de tripulante; PIN de 4 dígitos; instalador por usuario y sin firmar; el exe empaquetado acepta
`--remote-debugging-port`.
