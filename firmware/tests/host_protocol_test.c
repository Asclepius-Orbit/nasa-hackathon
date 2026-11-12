/*
 * Test de paridad en host de protocol.h: codifica los vectores dorados con los mismos
 * valores que el protocolo cerrado y los compara BYTE A BYTE con tests/golden_lines.txt.
 * Sale con codigo != 0 si algo difiere.
 *
 *   gcc -std=c11 -Wall -Wextra -Werror -pedantic -I spine_wearable \
 *       tests/host_protocol_test.c -o host_protocol_test
 *   ./host_protocol_test tests/golden_lines.txt
 */
#include <stdio.h>
#include <string.h>

#include "protocol.h"

#define MAX_LINE 512

static int failures = 0;

typedef struct {
    char buf[MAX_LINE];
    size_t len;
} capture_t;

static void capture_putc(void *ctx, char c)
{
    capture_t *cap = (capture_t *)ctx;
    if (cap->len < MAX_LINE - 1) {
        cap->buf[cap->len++] = c;
        cap->buf[cap->len] = '\0';
    }
}

static void capture_reset(capture_t *cap)
{
    cap->len = 0;
    cap->buf[0] = '\0';
}

static void expect_str(const char *what, const char *got, const char *want)
{
    if (strcmp(got, want) != 0) {
        printf("FAIL %s\n  got : %s  want: %s\n", what, got, want);
        failures++;
    } else {
        printf("ok   %s\n", what);
    }
}

/* Lee la linea n-esima del fichero dorado (con su '\n'); 0 si no existe. */
static int read_golden(FILE *f, char *out, size_t cap)
{
    if (fgets(out, (int)cap, f) == NULL) {
        return 0;
    }
    return 1;
}

static void put_i16(uint8_t *dst, int16_t v)
{
    sp_put_le16(dst, (uint16_t)v); /* complemento a dos */
}

int main(int argc, char **argv)
{
    const char *golden_path = (argc > 1) ? argv[1] : "tests/golden_lines.txt";
    FILE *f = fopen(golden_path, "rb");
    char want[MAX_LINE];
    capture_t cap;
    uint8_t payload[64];
    size_t i;

    if (f == NULL) {
        printf("FAIL no se puede abrir %s\n", golden_path);
        return 2;
    }

    /* CRC-8/MAXIM: valor de comprobacion del catalogo. */
    {
        const char *check = "123456789";
        uint8_t crc = sp_crc8((const uint8_t *)check, strlen(check));
        if (crc != 0xA1) {
            printf("FAIL CRC8(\"123456789\") = 0x%02X, se esperaba 0xA1\n", crc);
            failures++;
        } else {
            printf("ok   CRC8(\"123456789\") == 0xA1\n");
        }
    }

    /* H */
    capture_reset(&cap);
    sp_emit_hello(capture_putc, &cap, 2893746120u, SP_FW_VERSION, 1234u);
    read_golden(f, want, sizeof want);
    expect_str("H hello", cap.buf, want);

    /* T */
    capture_reset(&cap);
    sp_emit_tick(capture_putc, &cap, 2893746120u, 5000u);
    read_golden(f, want, sizeof want);
    expect_str("T tick", cap.buf, want);

    /* D,E: seq 7, t0 1000, [0,1,512,1023] */
    {
        const uint16_t emg[4] = {0, 1, 512, 1023};
        for (i = 0; i < 4; i++) {
            sp_put_le16(&payload[i * 2], emg[i]);
        }
        capture_reset(&cap);
        sp_emit_data(capture_putc, &cap, 'E', 7, 1000u, 4, payload, 8);
        read_golden(f, want, sizeof want);
        expect_str("D,E", cap.buf, want);
    }

    /* D,I: seq 3, t0 2000, 2 muestras de 6 x int16 */
    {
        const int16_t imu[2][6] = {{100, -100, 8192, 0, -1, 32767},
                                   {-32768, 5, -5, 65, -65, 1000}};
        size_t k;
        for (i = 0; i < 2; i++) {
            for (k = 0; k < 6; k++) {
                put_i16(&payload[(i * 6 + k) * 2], imu[i][k]);
            }
        }
        capture_reset(&cap);
        sp_emit_data(capture_putc, &cap, 'I', 3, 2000u, 2, payload, 24);
        read_golden(f, want, sizeof want);
        expect_str("D,I", cap.buf, want);
    }

    /* D,S: seq 65535, t0 4294967295, [511,512] */
    {
        sp_put_le16(&payload[0], 511);
        sp_put_le16(&payload[2], 512);
        capture_reset(&cap);
        sp_emit_data(capture_putc, &cap, 'S', 65535, 4294967295u, 2, payload, 4);
        read_golden(f, want, sizeof want);
        expect_str("D,S", cap.buf, want);
    }

    /* El fichero dorado no debe tener mas lineas que vectores. */
    if (read_golden(f, want, sizeof want)) {
        printf("FAIL golden_lines.txt tiene lineas de mas: %s", want);
        failures++;
    }
    fclose(f);

    /* Base64: vectores de la RFC 4648 (cubren los tres tipos de padding). */
    {
        static const char *const plain[] = {"", "f", "fo", "foo", "foob", "fooba", "foobar"};
        static const char *const b64[] = {"", "Zg==", "Zm8=", "Zm9v", "Zm9vYg==", "Zm9vYmE=", "Zm9vYmFy"};
        for (i = 0; i < 7; i++) {
            sp_line_t ln;
            capture_reset(&cap);
            sp_line_begin(&ln, capture_putc, &cap);
            sp_line_put_b64(&ln, (const uint8_t *)plain[i], strlen(plain[i]));
            expect_str("base64 RFC 4648", cap.buf, b64[i]);
        }
    }

    /* Enteros: extremos y ausencia de ceros a la izquierda. */
    {
        static const uint32_t vals[] = {0u, 9u, 10u, 65535u, 4294967295u};
        static const char *const txt[] = {"0", "9", "10", "65535", "4294967295"};
        for (i = 0; i < 5; i++) {
            sp_line_t ln;
            capture_reset(&cap);
            sp_line_begin(&ln, capture_putc, &cap);
            sp_line_put_u32(&ln, vals[i]);
            expect_str("u32 decimal", cap.buf, txt[i]);
        }
    }

    if (failures != 0) {
        printf("%d comprobacion(es) fallida(s)\n", failures);
        return 1;
    }
    printf("host_protocol_test: todo ok\n");
    return 0;
}
