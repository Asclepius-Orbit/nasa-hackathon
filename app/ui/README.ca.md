[English](README.md) · [Español](README.es.md) · **Català**

# spine-ui (l'app d'escriptori de bord)

App d'escriptori que l'astronauta fa servir a la tauleta: el veredicte del dia, el report diari, les sessions
d'exercici, l'historial, l'avís de símptoma urgent i el resum per a Houston. Electron + React + TypeScript, amb
Vite i pnpm.

Les dades són **mock de moment**: cada pantalla llegeix d'un `DataSource` tipat (`src/renderer/data/`) amb els
valors del mock. Connectar-lo a les API HTTP de l'app (`/ui` al 8000 i la de només lectura al 8002) és una altra
feina.

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
src/preload/    L'únic pont: window.asclepius (crew, auth, settings) i res més
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
- L'estat de tripulants i d'autenticació es xifra amb `safeStorage` d'Electron i falla tancat si no hi ha xifratge
  disponible.
- Fuses d'Electron: sense `RunAsNode`, sense `NODE_OPTIONS`, sense flags d'inspect, integritat de l'asar i càrrega
  només des de l'asar.
- Cadena de subministrament: versions exactes, lockfile, scripts de build permesos només per a `electron` i una
  antiguitat mínima de publicació d'un dia (`pnpm-workspace.yaml`).

Límits coneguts, per a feina posterior: l'autorització viu al renderer mentre les dades siguin mock (les dades de
salut reals les haurà de servir el procés main, comprovades contra la seva sessió); no hi ha tancament de sessió ni
canvi de tripulant; PIN de 4 dígits; instal·lador per usuari i sense signar; l'exe empaquetat accepta
`--remote-debugging-port`.
