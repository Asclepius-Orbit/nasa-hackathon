# Validación del resumen y del informe PDF para tierra (Houston)

Para: **Aguado**. Revisa con Uri y se vuelca al código después (issue #62: resumen diario a tierra; el envío urgente real es #63).

Cómo rellenarlo: en cada tabla, la casilla viene **premarcada con nuestra propuesta**. Cámbiala si no estás de acuerdo y escribe tu redacción o criterio en la columna de nota. Lo que no se decide aquí queda como `pendiente de X`; se abrirá una issue por cada punto pendiente.

Fuentes (si discrepan con este documento, manda el código): `spine_monitor/contracts/schemas/v1/app/ground-summary.schema.json`, `spine_monitor/contracts/ui-openapi.yaml` (`getHoustonWeeklyReport`), `spine_monitor/backend/src/monitor/views/ground_summary.py`, `spine_monitor/backend/src/monitor/report/`, `spine_monitor/backend/src/monitor/ground/`, `app/ui/src/renderer/strings/codes.ts` y `app/ui/src/renderer/strings/urgent.ts`.

**Principio de partida (vale para el resumen y para el PDF):** solo códigos y números. Nada de texto libre (las notas de los informes y las descripciones de las reglas no se copian), nada del perfil del astronauta (nombre, edad, etc.), y el único identificador es el `astronaut_id` seudónimo. Todo lo desconocido es un dato ausente explícito. Una semana o un día sin evaluación sale como `nodata`, nunca con un verde por defecto.

## 0. Dos productos distintos para tierra

| | Resumen diario (#62) | Informe PDF semanal |
|---|---|---|
| Qué es | Mensaje JSON `ground-summary`, una revisión por cambio de la evaluación del día | Documento PDF con texto, tablas y gráficas |
| Quién lo dispara | Automático: bandeja de salida y enlace con tierra | El astronauta, con el botón «Generar informe para Houston» de la pantalla de inicio |
| Ventana | Un día UTC | Los últimos 7 días UTC (hasta `end`, por defecto hoy). Nunca el historial entero |
| Endpoint | (bandeja `ground_outbox`) | `GET /ui/crew/{astronaut_id}/houston-report.pdf?end=YYYY-MM-DD` |
| Estado | Contenido provisional | Contenido provisional |

El PDF **no sustituye** al resumen diario ni lo modifica. Se asume un envío semanal del PDF; el guardado y la transmisión del archivo a tierra no están automatizados (hoy lo guarda el astronauta en el equipo).

## 1. Qué viaja a tierra

### 1.1 Resumen diario: campos que ya están en el esquema

| Campo | Qué lleva hoy | Decisión (Aguado) | Nota |
|---|---|---|---|
| `schema_version`, `astronaut_id`, `date`, `mission_day`, `revision`, `as_of` | Técnicos y de identificación (id seudónimo; `mission_day` es `null` sin fecha de lanzamiento) | [x] va | |
| `level`, `assessment_id`, `confidence`, `data_available`, `data_total` | Nivel `green`/`amber`/`red`/`nodata` y confianza de la evaluación | [x] va | `nodata` si no hay evaluación. |
| `drivers[]` | `feature_id` y `deviation_z`, de más a menos influyente | [x] va | Códigos del catálogo. |
| `red_flags` | Banderas rojas del día (catálogo cerrado) | [x] va | |
| `other_symptoms` | `very_severe_pain`, `other`; solo el código, nunca la descripción | [x] va | Si escalan como bandera roja: pendiente de Aguado. |
| `sessions[]` | `session_id`, `state`, `protocol_id`, `pain_stop` (repetición e instante, no la intensidad) | [x] va | |
| `recommendations` | Códigos `REC_*` | [x] va | |
| `escalation` | `required`, `urgency` (`routine`/`priority`/`immediate`), `reasons` (`RULE_*`) | [x] va | Textos en la sección 3. |
| `contacts[]` | `contact_id`, `at`, `contact` (rol); la nota se queda a bordo | [x] va | Hecho en #63. |

### 1.2 Informe PDF semanal: contenido propuesto

Origen de los datos: lo que el astronauta registra en la UI (informe diario, síntomas urgentes, Oswestry semanal), las sesiones de ejercicio con sus mediciones, y los datos del motor.

| Bloque | Qué lleva el PDF | Decisión (Aguado) | Nota |
|---|---|---|---|
| Cabecera | `astronaut_id`, rango de fechas UTC, día de misión, instante de generación | [x] va | Sin nombre ni perfil. El perfil solo se lee para numerar los días de misión. |
| Estado actual | Nivel de la última evaluación de la semana, confianza, completitud de datos, escalada requerida | [x] va | Sin evaluación: «NO DATA», gris. |
| Resumen de la semana | Frases generadas con plantilla a partir de los datos (sin IA): evolución del nivel, escalada y reglas, días con dolor, síntomas, sesiones, Oswestry | [x] va | |
| Dolor | Gráfica diaria de dolor (`pain_nrs`) y peor de 24 h (`pain_nrs_worst_24h`), con marca de bandera roja o síntoma urgente | [x] va | Cambia la propuesta anterior: **el dolor sí viaja**, porque son números del informe diario. Confirmar valor (actual, peor 24 h o ambos): pendiente de Aguado. |
| Nivel de riesgo por día | Banda diaria con el último nivel del motor, e índice de riesgo y confianza | [x] va | |
| Informe diario | Tabla por día: dolor, peor 24 h, rigidez, sueño, nº de sesiones, banderas rojas | [x] va | Solo valores numéricos y códigos. Las notas libres no salen. |
| Síntomas | Tabla de banderas rojas y síntomas urgentes con los días en que se reportaron | [x] va | Etiqueta legible del código, nunca la descripción libre de `other`. |
| Oswestry | Último Oswestry registrado esa semana, por ítem y como porcentaje | [x] va | **Solo informativo**: sin papel en el triaje (#107). |
| Sesiones de ejercicio | Estado, protocolo, parada por dolor (repetición) y mediciones: repeticiones, tiempo por repetición, elongación, rango de movimiento, EMG (%MVC) | [x] va | Incluye la longitud lumbar si la sesión la mide: **la longitud lumbar sí viaja** en el PDF. Valor absoluto o cambio respecto a la referencia: pendiente de Aguado. |
| Motor | Evolución del nivel y de la confianza, drivers con z, recomendaciones `REC_*`, escalada y reglas `RULE_*` | [x] va | Solo códigos. |
| Del agente | Nada del MCP ni de Ollama: ni chat, ni explicaciones generadas, ni texto del agente | [ ] no va | Decidido: no se incluye. |
| Texto libre que acompaña a una escalada | No existe y no se propone | [x] no va | Pendiente de Aguado: si hace falta un motivo clínico legible, ¿basta un código nuevo de un catálogo cerrado? |

Una semana sin datos sigue generando un PDF, con mensajes explícitos de «sin datos», nunca con un verde por defecto. El pie del PDF dice «Provisional content, pending clinical validation» hasta que Aguado valide.

## 2. Cuándo se envía

**Resumen diario (hoy):** se encola una revisión por cada cambio de la evaluación del día. Si la última revisión sigue en cola, se sustituye; si ya fue enviada, se abre otra. Un día con bandera roja o síntoma urgente cuya evaluación se omitió se encola con nivel `nodata` y la bandera; lo mismo para una parada por dolor o un contacto médico. El emisor vacía la cola solo con enlace, por orden cronológico, con reintentos con retroceso exponencial. Hoy **una escalada no adelanta a un resumen rutinario más antiguo**.

**Informe PDF:** se asume envío **semanal**. Lo genera el astronauta a demanda con el botón y cubre siempre los 7 últimos días.

| Decisión | Opciones | Decisión (Aguado) |
|---|---|---|
| Cadencia del PDF | Semanal a demanda (propuesta) / recordatorio o generación automática semanal | pendiente de Aguado |
| Cómo llega el PDF a tierra | Hoy lo guarda el astronauta. ¿Se transmite por el enlace con tierra como el resumen? | pendiente de Aguado |
| Política de envío del resumen diario | [ ] La actual: cronológica. [ ] Envío prioritario de escaladas y banderas rojas | pendiente de Aguado |
| Qué cuenta como prioritario | Propuesta: `escalation.required = true` o alguna bandera roja. ¿También `other_symptoms`? ¿`pain_stop`? | pendiente de Aguado |

## 3. Textos de urgencia en la app

Fuente: `app/ui/src/renderer/strings/codes.ts` (`urgencyText`, `escalationRuleText`). Se muestran al astronauta tal cual. Un código desconocido se muestra como el propio código.

| Código | Redacción actual | Redacción de Aguado |
|---|---|---|
| `routine` | routine, can wait for the next scheduled contact | pendiente de Aguado |
| `priority` | priority, needs the flight surgeon soon | pendiente de Aguado |
| `immediate` | immediate, needs the flight surgeon now | pendiente de Aguado |
| `RULE_RED_FLAG_NEURO` | warning neurological symptom | pendiente de Aguado |
| `RULE_SYMPTOM_PAIN_TREND` | pain trend rising | pendiente de Aguado |
| `RULE_INCOMPLETE_DATA` | data too incomplete to assess | pendiente de Aguado |
| `RULE_UNKNOWN_RED_FLAG` | unrecognised warning symptom | pendiente de Aguado |

Pregunta abierta: ¿hay más reglas de escalada que deban tener texto propio? Las demás se muestran hoy como su código crudo. Nota: el PDF muestra los `RULE_*` como código; si Aguado redacta los textos, ¿deben aparecer también en el PDF? (pendiente de Aguado).

## 4. Urgente en modo real

Hasta que exista el envío real (#63) la app no puede mandar avisos urgentes y lo dice, pidiendo avisar al oficial médico en persona. Textos actuales en `app/ui/src/renderer/strings/urgent.ts`:

- `introNotSent`: «Mark what applies. This app cannot send urgent reports: tell the crew medical officer now, in person or by voice.»
- `notRecordedBody`: «The report could not be saved. Tell the crew medical officer now, in person or by voice.»
- `noCrew`: «The crew list could not be read, so this report cannot be sent from here. Tell the crew medical officer now, in person or by voice.»

| Pregunta | Decisión (Aguado) |
|---|---|
| ¿Es adecuado este texto mientras no exista el envío? | [ ] sí, tal cual. [ ] no, redacción propia: pendiente de Aguado |
| ¿Hay que nombrar también otra vía (p. ej. radio o voz con Houston)? | pendiente de Aguado |

El informe PDF **no es un aviso urgente**: no sustituye al aviso en persona al oficial médico.

## Decisiones pendientes

Una issue por punto.

- [ ] 1a. Dolor en el PDF: confirmar valor (actual, peor 24 h o ambos) y si viaja también en el resumen diario (pendiente de Aguado)
- [ ] 1b. Longitud lumbar: valor absoluto o cambio respecto a la referencia, en PDF y en resumen diario (pendiente de Aguado)
- [ ] 1c. Texto que acompaña a una escalada: confirmar que no viaja, o definir un catálogo cerrado de motivos (pendiente de Aguado)
- [ ] 1d. `other_symptoms`: confirmar que viajan solo como código y si escalan como bandera roja (pendiente de Aguado)
- [ ] 1e. Oswestry: confirmar que va en el PDF solo como dato informativo, sin papel en el triaje (pendiente de Aguado)
- [ ] 2a. Cadencia del PDF y cómo llega a tierra (pendiente de Aguado)
- [ ] 2b. Política de envío del resumen diario: cronológica o prioritaria para escaladas y banderas rojas, y qué es prioritario (pendiente de Aguado)
- [ ] 3a. Redacción de `routine`, `priority`, `immediate` (pendiente de Aguado)
- [ ] 3b. Redacción de los cuatro motivos de escalada y reglas que faltan, y si salen en el PDF (pendiente de Aguado)
- [ ] 4. Texto de «no se puede enviar, avisa en persona» hasta #63 (pendiente de Aguado)

## Cómo se vuelca al código

Lo hace Uri tras revisar este documento con Aguado.

| Decisión | Ficheros que cambian |
|---|---|
| 1a, 1b, 1e: qué lleva el PDF | `spine_monitor/backend/src/monitor/report/` (`week.py` datos, `charts.py` gráficas, `houston_pdf.py` maquetación) y `spine_monitor/backend/tests/test_houston_week.py` |
| 1a, 1b, 1c: campos del resumen diario | Esquema `ground-summary.schema.json` (y su descripción «PROVISIONAL»); ejemplos `contracts/examples/valid/app/ground-summary*.json`; vista `ground_summary.py`; tests `test_ground_summary.py` y `contracts/tests/`. Regla del repo: contrato primero (skill `contract-first`), luego los modelos generados |
| 1d: `other_symptoms` | `ground_summary.py` (`_day_other_symptoms`), `domain/self-report.schema.json` si cambia el catálogo, tests de la vista |
| 2a: cadencia y entrega del PDF | `api.py` (endpoint), `app/ui/src/main/views/houston-report-service.ts` y `app/ui/src/renderer/features/today/HoustonReportCard.tsx` |
| 2b: prioridad del resumen diario | `db.py` (`next_ground_item`, `upsert_ground_summary`), `ground/enqueue.py`, `ground/sender.py`; tests `test_ground_flow.py`, `test_ground_sender.py` |
| 3a, 3b: textos de urgencia | `app/ui/src/renderer/strings/codes.ts` y tests de UI que los citen (p. ej. `TodayView.test.tsx`); `report/labels.py` si salen en el PDF |
| 4: aviso en persona | `app/ui/src/renderer/strings/urgent.ts` y `UrgentDialog.test.tsx` |
| Cualquier validación | Quitar las notas «PROVISIONAL / pending validation by Aguado» del esquema, de `ground_summary.py` y el pie del PDF |
