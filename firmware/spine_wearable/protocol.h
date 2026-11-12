/*
 * protocol.h - Codificacion PURA del protocolo serie del wearable (MQTT chunks v1).
 *
 * Sin dependencias de Arduino: solo <stdint.h> y <stddef.h>, para poder compilarla y
 * probarla en el host (firmware/tests/host_protocol_test.c) y que el codigo que se prueba
 * sea byte a byte el mismo que va en la placa.
 *
 * Formato (lineas ASCII terminadas en '\n'):
 *     <cuerpo>*<XX>\n
 * XX = 2 hex MAYUSCULA con el CRC-8/MAXIM (poli reflejado 0x8C, init 0, sin xorout) de los
 * bytes del cuerpo (todo lo anterior a '*'). Comprobacion: CRC8("123456789") = 0xA1.
 *
 *     H,1,<boot_id u32>,<fw_version>,<millis u32>          hello al arrancar
 *     T,<boot_id>,<millis>                                  tick cada 1000 ms
 *     D,<kind>,<frame_seq u16>,<t0_ms u32>,<n>,<b64>        lote de muestras
 *
 * Toda la salida va por un "sink" (callback que recibe un caracter): no hay buffer de linea
 * completa, el CRC y el base64 se calculan en streaming mientras se escribe.
 * Compatible con C99 y con C++ (se incluye desde el sketch .ino).
 */
#ifndef SPINE_PROTOCOL_H
#define SPINE_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define SP_FW_VERSION "0.1.0"
#define SP_PROTOCOL_VERSION 1

/* Escribe un caracter en el destino (Serial en la placa, un buffer en el test). */
typedef void (*sp_sink_fn)(void *ctx, char c);

typedef struct {
    sp_sink_fn sink;
    void *ctx;
    uint8_t crc; /* CRC-8 acumulado del cuerpo emitido hasta ahora */
} sp_line_t;

/* ---------- CRC-8/MAXIM ---------- */

/* Bit a bit: ~20 instrucciones, sin tabla (no gasta RAM ni flash de datos). */
static inline uint8_t sp_crc8_update(uint8_t crc, uint8_t byte)
{
    uint8_t i;
    crc = (uint8_t)(crc ^ byte);
    for (i = 0; i < 8; i++) {
        crc = (crc & 1u) ? (uint8_t)((crc >> 1) ^ 0x8Cu) : (uint8_t)(crc >> 1);
    }
    return crc;
}

static inline uint8_t sp_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0;
    size_t i;
    for (i = 0; i < len; i++) {
        crc = sp_crc8_update(crc, data[i]);
    }
    return crc;
}

/* ---------- Little-endian explicito (no depende del orden de bytes del host) ---------- */

static inline void sp_put_le16(uint8_t *dst, uint16_t v)
{
    dst[0] = (uint8_t)(v & 0xFFu);
    dst[1] = (uint8_t)(v >> 8);
}

/* ---------- Linea: cuerpo con CRC en streaming ---------- */

static inline void sp_line_begin(sp_line_t *ln, sp_sink_fn sink, void *ctx)
{
    ln->sink = sink;
    ln->ctx = ctx;
    ln->crc = 0;
}

/* Caracter del cuerpo: entra en el CRC y sale por el sink. */
static inline void sp_line_putc(sp_line_t *ln, char c)
{
    ln->crc = sp_crc8_update(ln->crc, (uint8_t)c);
    ln->sink(ln->ctx, c);
}

static inline void sp_line_puts(sp_line_t *ln, const char *s)
{
    while (*s != '\0') {
        sp_line_putc(ln, *s++);
    }
}

/* Entero sin signo en decimal, sin ceros a la izquierda ("0" para 0). */
static inline void sp_line_put_u32(sp_line_t *ln, uint32_t v)
{
    char digits[10]; /* 4294967295 tiene 10 cifras */
    uint8_t n = 0;
    do {
        digits[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    } while (v != 0u);
    while (n > 0) {
        sp_line_putc(ln, digits[--n]);
    }
}

/* Base64 estandar (RFC 4648) con padding, en streaming: no guarda nada entre llamadas. */
static inline void sp_line_put_b64(sp_line_t *ln, const uint8_t *data, size_t len)
{
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t i = 0;
    while (len - i >= 3) {
        uint32_t v = ((uint32_t)data[i] << 16) | ((uint32_t)data[i + 1] << 8) | data[i + 2];
        sp_line_putc(ln, alphabet[(v >> 18) & 0x3Fu]);
        sp_line_putc(ln, alphabet[(v >> 12) & 0x3Fu]);
        sp_line_putc(ln, alphabet[(v >> 6) & 0x3Fu]);
        sp_line_putc(ln, alphabet[v & 0x3Fu]);
        i += 3;
    }
    if (len - i == 1) {
        uint32_t v = (uint32_t)data[i] << 16;
        sp_line_putc(ln, alphabet[(v >> 18) & 0x3Fu]);
        sp_line_putc(ln, alphabet[(v >> 12) & 0x3Fu]);
        sp_line_putc(ln, '=');
        sp_line_putc(ln, '=');
    } else if (len - i == 2) {
        uint32_t v = ((uint32_t)data[i] << 16) | ((uint32_t)data[i + 1] << 8);
        sp_line_putc(ln, alphabet[(v >> 18) & 0x3Fu]);
        sp_line_putc(ln, alphabet[(v >> 12) & 0x3Fu]);
        sp_line_putc(ln, alphabet[(v >> 6) & 0x3Fu]);
        sp_line_putc(ln, '=');
    }
}

/* Cierra la linea: '*', CRC en 2 hex mayuscula (ya no entra en el CRC) y '\n'. */
static inline void sp_line_end(sp_line_t *ln)
{
    static const char hex[] = "0123456789ABCDEF";
    uint8_t crc = ln->crc;
    ln->sink(ln->ctx, '*');
    ln->sink(ln->ctx, hex[crc >> 4]);
    ln->sink(ln->ctx, hex[crc & 0x0Fu]);
    ln->sink(ln->ctx, '\n');
}

/* ---------- Tramas ---------- */

/* H,1,<boot_id>,<fw_version>,<millis> */
static inline void sp_emit_hello(sp_sink_fn sink, void *ctx, uint32_t boot_id,
                                 const char *fw_version, uint32_t millis_now)
{
    sp_line_t ln;
    sp_line_begin(&ln, sink, ctx);
    sp_line_puts(&ln, "H,");
    sp_line_put_u32(&ln, SP_PROTOCOL_VERSION);
    sp_line_putc(&ln, ',');
    sp_line_put_u32(&ln, boot_id);
    sp_line_putc(&ln, ',');
    sp_line_puts(&ln, fw_version);
    sp_line_putc(&ln, ',');
    sp_line_put_u32(&ln, millis_now);
    sp_line_end(&ln);
}

/* T,<boot_id>,<millis> */
static inline void sp_emit_tick(sp_sink_fn sink, void *ctx, uint32_t boot_id,
                                uint32_t millis_now)
{
    sp_line_t ln;
    sp_line_begin(&ln, sink, ctx);
    sp_line_puts(&ln, "T,");
    sp_line_put_u32(&ln, boot_id);
    sp_line_putc(&ln, ',');
    sp_line_put_u32(&ln, millis_now);
    sp_line_end(&ln);
}

/*
 * D,<kind>,<frame_seq>,<t0_ms>,<n>,<b64>
 * payload: bytes ya en little-endian (E: n x u16, I: n x 6 x i16, S: n x u16).
 */
static inline void sp_emit_data(sp_sink_fn sink, void *ctx, char kind, uint16_t frame_seq,
                                uint32_t t0_ms, uint8_t n, const uint8_t *payload,
                                size_t payload_len)
{
    sp_line_t ln;
    sp_line_begin(&ln, sink, ctx);
    sp_line_puts(&ln, "D,");
    sp_line_putc(&ln, kind);
    sp_line_putc(&ln, ',');
    sp_line_put_u32(&ln, frame_seq);
    sp_line_putc(&ln, ',');
    sp_line_put_u32(&ln, t0_ms);
    sp_line_putc(&ln, ',');
    sp_line_put_u32(&ln, n);
    sp_line_putc(&ln, ',');
    sp_line_put_b64(&ln, payload, payload_len);
    sp_line_end(&ln);
}

#endif /* SPINE_PROTOCOL_H */
