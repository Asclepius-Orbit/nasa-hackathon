# syntax=docker/dockerfile:1
# Build reproducible del firmware del wearable (Arduino Uno / Nano, ATmega328P).
# Build context: raiz del repo (como el resto de imagenes). El .dockerignore raiz deja pasar firmware/.
#   docker build -f firmware/Dockerfile -t spine-firmware:dev .
#   docker run --rm spine-firmware:dev
#
# El comando por defecto hace dos cosas, en este orden, y falla si cualquiera falla:
#   1. test de paridad en host (gcc): protocol.h reproduce los vectores dorados byte a byte;
#   2. compilacion del sketch para arduino:avr:uno con todos los warnings y uso de memoria.

FROM debian:12.12-slim

# arduino-cli fijado y verificado por sha256 (release v1.6.0, Linux 64 bit)
ARG ARDUINO_CLI_VERSION=1.6.0
ARG ARDUINO_CLI_SHA256=321ee249f44c78053234fec0f36101092bc6021bf24939e15e0cc2449c5ed250
# Core AVR de Arduino (avr-gcc 7.3.0-atmel3.6.1-arduino7 incluido)
ARG ARDUINO_AVR_CORE=1.8.6

# gcc/libc6-dev: test de paridad en host. curl: solo para bajar arduino-cli.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/arduino-cli.tar.gz \
        "https://github.com/arduino/arduino-cli/releases/download/v${ARDUINO_CLI_VERSION}/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz" \
    && echo "${ARDUINO_CLI_SHA256}  /tmp/arduino-cli.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/arduino-cli.tar.gz -C /usr/local/bin arduino-cli \
    && rm /tmp/arduino-cli.tar.gz

RUN groupadd --system app && useradd --system --gid app --create-home app \
    && mkdir /work && chown app:app /work

USER app
WORKDIR /work

# Sin telemetria ni comprobaciones de actualizacion; datos del core en el HOME del usuario.
ENV ARDUINO_UPDATER_ENABLE_NOTIFICATION=false \
    ARDUINO_METRICS_ENABLED=false

# Solo se instala el core avr (Wire y EEPROM vienen con el): sin librerias de terceros.
RUN arduino-cli core update-index \
    && arduino-cli core install "arduino:avr@${ARDUINO_AVR_CORE}"

COPY --chown=app:app firmware /work/firmware

CMD ["sh", "-c", "set -e && gcc -std=c11 -Wall -Wextra -Werror -pedantic -I firmware/spine_wearable firmware/tests/host_protocol_test.c -o /tmp/host_protocol_test && /tmp/host_protocol_test firmware/tests/golden_lines.txt && arduino-cli compile --fqbn arduino:avr:uno --warnings all firmware/spine_wearable"]
