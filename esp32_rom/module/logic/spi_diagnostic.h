#ifndef BUSINESS_SPI_DIAGNOSTIC_H
#define BUSINESS_SPI_DIAGNOSTIC_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
/* Business-specific callbacks, not a universal peripheral HAL. UART remains
 * acquired until release; read_line returns 1 complete line, 0 timeout, -1 I/O.
 * Backend owns persistent transfer storage and retains it after stuck I/O. */
typedef struct {
 void *ctx;
 int64_t (*now_us)(void *);
 void (*progress)(void *);
 int (*open)(void *,uint32_t,const char **);
 int (*transfer)(void *,const uint8_t [64],uint8_t [64],const char **);
 int (*close)(void *);
 bool (*busy)(void *);
 bool (*acquire)(void *);
 void (*release)(void *);
 int (*prepare)(void *,const char **stage,const char **error);
 int (*arm)(void *,uint32_t);
 int (*read_line)(void *,char *,size_t,int64_t);
} spi_diagnostic_io;
typedef struct {
 bool ok;
 const char *stage,*error;
 int rc,cleanup_rc;
 uint32_t last_seq;
 unsigned completed,esp_rx_verified,stm_rx_verified,rx_mismatch_bytes;
 /* First mismatching completed transfer only; independent of UART DONE.
  * ESP driver RX bytes, not remote pad/electrical measurements. */
 bool rx_failure_captured,rx_constant;
 uint32_t rx_failure_seq;
 char rx_first16_hex[33],expected_first16_hex[33];
 unsigned first_mismatch_index,first_mismatch_actual,first_mismatch_expected;
 unsigned rx_min,rx_max;
 int64_t elapsed_ms,deadline_ms;
} spi_diagnostic_result;
bool spi_parameters_valid(uint32_t count,uint32_t hz);
void spi_patterns(uint32_t seq,uint8_t tx[64],uint8_t expected[64]);
int spi_parse_line(const char *line,uint32_t seq,bool done);
void spi_diagnostic_run(const spi_diagnostic_io *io,uint32_t count,uint32_t hz,
                        uint32_t *next_seq,spi_diagnostic_result *result);
#endif
