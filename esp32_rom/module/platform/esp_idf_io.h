#ifndef ESP_MODULE_ESP_IDF_IO_H
#define ESP_MODULE_ESP_IDF_IO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "board_config.h"

/* Single control-task caller; no internal ownership/mutex policy.
 * SPI functions return ESP_OK or a driver/error code. Error strings are static.
 * A failed open can own a bus: always close after attempting open, except when
 * busy was already true. Busy means resources remain allocated, not wire BSY.
 * A failed completion preserves all driver-visible storage until close succeeds.
 */
int esp_io_spi_open(uint32_t hz, const char **error);
int esp_io_spi_transfer(const uint8_t tx[64], uint8_t rx[64], const char **error);
int esp_io_spi_close(void);
bool esp_io_spi_busy(void);
/* C3-only digital-pad evidence; register reads do not change routing. */
typedef struct {
    int level;
    uint32_t iomux, matrix;
} esp_io_spi_pad_snapshot;
typedef struct {
    unsigned low, high;
    esp_io_spi_pad_snapshot before, sampled, restored;
} esp_io_spi_pad_result;
void esp_io_spi_pad_snapshot_get(esp_io_spi_pad_snapshot *s);
int esp_io_spi_pad_sample(unsigned pull, esp_io_spi_pad_result *r);
void esp_io_spi_pad_evidence(esp_io_spi_pad_snapshot *before,
                           esp_io_spi_pad_snapshot *during);


esp_err_t esp_io_uart_mode(unsigned baud, bool rom);
esp_err_t esp_io_target_gpio_init(void);
esp_err_t esp_io_uart_init(QueueHandle_t *events, size_t rx_size);

#endif
