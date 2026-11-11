[English](README.md) · [Español](README.es.md) · **Català**

# spine-ui (l'app d'escriptori de bord)

App d'escriptori que l'astronauta fa servir a la tauleta: el veredicte del dia, el report diari, les sessions
d'exercici, l'historial, l'avís de símptoma urgent i el resum per a Houston. Electron + React + TypeScript, amb
Vite i pnpm.

Cada pantalla llegeix d'un `DataSource` tipat (`src/renderer/data/`). Per defecte és el backend (`/ui` al 8000), on
només s'arriba a través del procés main; el build d'e2e i `VITE_MOCK_DATA=true` fan servir el mock de bord.

## Requisits

- Node 24 LTS (`.node-version`; `engines` rebutja altres versions majors).
- pnpm mitjançant corepack: `corepack enable pnpm`. La versió exacta (12.16.1, amb el seu hash) surt de
  `packageManager` a `package.json`. Per a Node només es fa servir pnpm; mai npm ni npx.

## Engegar-la

```bash
cd app/ui
pnpm start          # des de l'arrel del repo: pnpm -C app/ui start
```

`pnpm start` instal·la el que falti (amb el lockfile congelat), descarrega el binari d'Electron si cal i obre
l'app en mode desenvolupament amb recàrrega en calent.

En desenvolupament (i només a la build d'e2e) hi ha dos tripulants de demo: **Elena Ruiz** i **Marc Dubois**,
PIN **1234**. El menú "Mock screens" i "Mock: skip login or lock" també són només de desenvolupament. Una build de
producció arrenca sense tripulants: el primer es dona d'alta amb "New crew member".

## Scripts

| Script | Què fa |
|---|---|
| `pnpm start` | Instal·la, descarrega Electron i arrenca l'app en desenvolupament |
| `pnpm dev` | Només l'app en desenvolupament (dona per instal·lades les dependències) |
| `pnpm build` | Build de producció a `out/` |
| `pnpm dist` | Instal·lador de Windows (NSIS, sense signar) a `release/` |
| `pnpm test` / `pnpm test:cov` | Tests unitaris i de components amb Vitest (cobertura ≥ 90 % de línies, branques, funcions i sentències) |
| `pnpm e2e` | Compila en mode `e2e` i executa els fluxos de Playwright contra l'app real |
| `pnpm e2e:packaged` | Empaqueta l'app (`--dir`) i comprova que l'exe empaquetat mostra el login |
| `pnpm lint` / `pnpm typecheck` | ESLint (estricte, sense warnings) i `tsc -b` |
| `pnpm gen:types` / `pnpm check:types` | Regenera / comprova `src/contracts/v1.ts` a partir dels JSON Schema |
| `pnpm check:bundle` | Falla si arriba codi de desenvolupament (tripulants de demo, URL del servidor de desenvolupament, CSP relaxada, devtools) al bundle de producció |
| `pnpm check` | El gate complet: lint, tipus, tipus de contractes, cobertura, `pnpm audit` (high) i `check:bundle` |

El mateix gate s'executa a Docker i a CI:

```bash
docker compose -f app/docker-compose.yml --profile quality run --rm ui-quality
```

## Estructura

```
src/main/       Procés main d'Electron: finestra enfortida, protocol app://, IPC, PIN, magatzems xifrats
src/preload/    L'únic pont: window.asclepius (crew, auth, settings, views) i res més
src/shared/     Contracte del pont (api.ts) i noms de canals IPC + esquemes zod
src/renderer/   App React: router, layout, pantalles a features/<nom>/, strings/, data/, components/ui/
src/contracts/  Tipus generats des de spine_monitor/contracts/schemas/v1 (no editar)
e2e/            Playwright: smoke, fluxos, captures visuals, app empaquetada
scripts/        Generador de tipus de contractes, descàrrega d'Electron, comprovació del bundle, fuses (after-pack)
```

Els textos viuen a `src/renderer/strings/` (en anglès, un fitxer per pantalla) per poder afegir altres idiomes.

## Seguretat

- Renderer: `sandbox`, `contextIsolation`, sense integració de Node, sense webviews, finestres noves denegades,
  navegació limitada a l'app i tots els permisos denegats.
- La interfície se serveix per un esquema privilegiat `app://asclepius` limitat a `out/renderer`, amb una CSP
  estricta (etiqueta meta i capçalera de resposta). Sense recursos remots: la font hi va inclosa.
- IPC: cada crida comprova que el remitent és el frame principal de la mateixa app i valida l'entrada amb un
  esquema zod estricte; els errors arriben al renderer com a missatges genèrics.
- PIN: scrypt amb salt per tripulant, comprovat al procés main. Tres errors bloquegen aquell tripulant cinc minuts,
  i el bloqueig sobreviu als reinicis; l'intent es registra abans de comprovar el PIN.
- Credencials del backend: cada petició a `/ui` porta el token compartit (`ASCLEPIUS_BACKEND_TOKEN`, o el fitxer que indiqui `ASCLEPIUS_BACKEND_TOKEN_FILE`, p. ex. el `ui-token` que el backend escriu al costat de la seva base de dades) i l'astronauta desbloquejat (`X-Astronaut-Id`). Tots dos es queden al procés main; sense token el backend respon 401 i les pantalles mostren `unavailable`.
- Vistes del backend: el renderer mai parla amb el backend (el CSP `connect-src 'self'` no canvia). El procés main demana les vistes `/ui/...` només de l'astronauta desbloquejat, per HTTP a loopback (`ASCLEPIUS_BACKEND_URL`, per defecte `http://127.0.0.1:8000`; altres hosts s'ignoren), amb timeout de 5 s, sense redireccions i sense cau. Els errors arriben al renderer com `locked`, `not_found` o `unavailable`.
- Registre de tripulants: el magatzem xifrat de la tripulació és amo de la llista, els ids `AST-NN`, els noms i els PIN. El procés main només comunica al backend cada id (`PUT /ui/crew/{id}`, sense cos), en arrencar i en crear un membre; si el backend està caigut el membre es crea igualment i l'id es reintenta en el llistat següent. El perfil del backend es completa a la pantalla Perfil (a sota); sense ell `/assess` s'omet.
- Escriptures al backend: el procés main també escriu només per a l'astronauta desbloquejat, amb les mateixes regles que les vistes (loopback, token, `X-Astronaut-Id`, timeout de 5 s, sense redireccions). Una escriptura respon amb un resultat, mai amb un rebuig: `ok`, o `invalid` amb els punters de camp del backend (422), `locked` (401/403), `conflict` (409) o `unavailable`. La UI diu "Saved" només amb `ok`.
- Perfil i context diari (`/profile`, també des d'Ajustos): la pantalla edita part del perfil (data de naixement, sexe en néixer, alçades, pes, dates d'enlairament i aterratge) i el context d'avui (fase, gravetat, dia de missió). Main conserva la resta de camps desats. Al perfil mai hi va un nom. Si avui (UTC) no hi ha context i el perfil té data d'enlairament, main n'escriu un quan s'accepta el PIN i en desar el perfil: la fase i el dia de missió surten de les dates (el dia de l'enlairament és l'1, no hi ha dia 0), amb microgravetat en vol i 1 g fora. Mai sobreescriu un context desat. Today i First day demanen el que falti.
- Aparellament de l'arnès (Settings > Harness): llista els dispositius que han publicat estat per a l'astronauta (`GET /ui/crew/{id}/devices`), i el `device_id` triat es desa només al magatzem xifrat de la tripulació. El backend no aparella: la franja mostra l'últim dispositiu que ha informat.
- Sessions (pantalla Session): "Start session" amb el protocol triat (`lat_pulldown_standing_v1` o `spine_length_rest_v1`). Main envia l'arnès aparellat com a `device_id`. El tipus de sessió, la banda i les repeticions surten del catàleg de protocols. Les declaracions de streams surten d'una plantilla per protocol copiada dels exemples del contracte, pendent del protocol estàndard (#12). Sense arnès aparellat no s'envia res; sense context diari d'avui el backend refusa (422) i la pantalla porta a Profile. La pantalla segueix la sessió de `starting` a `running`, `rejected` o el final. Recorda l'última sessió, de manera que una `processing_failed` es pot reprocessar. Amb la sessió en `running`, Finish l'atura com a `completed`, Calibrate demana un calibratge MVC de l'EMG i "Mark an artefact" marca la repetició actual. Stop: pain envia `aborted_pain` amb la repetició actual i obre la pantalla Stopped només si el backend ho ha desat.
- Autoinformes: l'informe diari i l'Oswestry es desen amb `POST self-reports` (tipus `daily` i `weekly_odi`), i l'exercici del dia s'afegeix al context diari d'avui. Els sis ítems de l'Oswestry són provisionals, pendents de validar per l'Aguado (#48). "Saved" surt només amb `ok`.
- Símptoma urgent: un informe `red_flag`. Amb algú desbloquejat s'envia per aquella persona. Des del login o el bloqueig s'envia sense PIN per al tripulant triat al diàleg: és l'única escriptura permesa així, perquè és una emergència, i no es llegeix res. Només mostra Queued si s'ha desat.
- Pantalla Stopped: la repetició en què es va registrar l'aturada, l'enllaç amb terra, la trucada a l'oficial mèdic (`POST contacts`, que també va al resum per a Houston) i els contactes registrats. No hi ha enquesta després de l'aturada: els símptomes van per Urgent i el dolor per l'informe diari.
- L'estat de tripulants i d'autenticació es xifra amb `safeStorage` d'Electron i falla tancat si no hi ha xifratge
  disponible.
- Fuses d'Electron: sense `RunAsNode`, sense `NODE_OPTIONS`, sense flags d'inspect, integritat de l'asar i càrrega
  només des de l'asar.
- Cadena de subministrament: versions exactes, lockfile, scripts de build permesos només per a `electron` i una
  antiguitat mínima de publicació d'un dia (`pnpm-workspace.yaml`).

Límits coneguts, per a feina posterior: no hi ha tancament de sessió ni
canvi de tripulant; PIN de 4 dígits; instal·lador per usuari i sense signar; l'exe empaquetat accepta
`--remote-debugging-port`.
