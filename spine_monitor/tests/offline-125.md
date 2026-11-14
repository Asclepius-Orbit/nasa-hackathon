# Opt-in offline integration check for #125

Prepare the images and model while online. From the repository root, create the broker credentials first (Compose reads them even when only preparing Ollama), build the quality image, and cache `qwen3.5:4b` in the `app_ollama-models` volume:

```sh
sh app/mosquitto/setup.sh
docker build -f spine_monitor/quality/Dockerfile -t spine-quality:dev .
docker compose -f app/docker-compose.yml --profile llm-prepare run --rm ollama-pull
```

Do not delete the `app_ollama-models` volume. The check never builds or pulls images; it starts both containers on an internal network. From the repository root:

```sh
docker compose -p spine125offline -f spine_monitor/tests/offline-125.compose.yml up -d ollama
docker compose -p spine125offline -f spine_monitor/tests/offline-125.compose.yml run --rm check
docker compose -p spine125offline -f spine_monitor/tests/offline-125.compose.yml down
```

Always run the final command, including after failures; cleanup is not automatic. It removes the test network and containers, not the external model volume. A missing model or index produces a failure or an explicit MCP error. The test checks blocked IP egress, the engine's stretch estimate and personal baseline/assessment, the real read API and MCP stdio server, missing evidence, and the authenticated HTTP `/report` endpoint with local Ollama. The input measurement and baseline sessions are prepared contract examples: this does not exercise acquisition or device calibration, and model output and timing can vary. The check is opt-in and is not part of the quality gate.
