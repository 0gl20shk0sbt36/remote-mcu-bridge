/* ESP32-C3 / ESP-IDF 5.5.5 fixed-board driver backend. */
#include "esp_idf_io.h"
#include <limits.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "soc/uart_reg.h"
#include "soc/gpio_reg.h"
#include "soc/gpio_sig_map.h"
#include "soc/io_mux_reg.h"
#include "freertos/task.h"

static spi_device_handle_t spi_device;
static bool spi_bus;
static bool spi_pending;
/* Do not replace with caller buffers or stack storage: a timed-out transaction
 * can still be owned by the driver, including after a failed cleanup. */
static spi_transaction_t spi_transaction;
static uint8_t spi_tx[64], spi_rx[64];

bool esp_io_spi_busy(void) {
    return spi_bus || spi_device != NULL || spi_pending;
}

static esp_io_spi_pad_snapshot spi_pad_before, spi_pad_during;
void esp_io_spi_pad_snapshot_get(esp_io_spi_pad_snapshot *s) {
    s->level = gpio_get_level(BOARD_SPI_MISO);
    s->iomux = REG_READ(IO_MUX_GPIO0_REG);
    /* FSPIQ is SPI2 MISO, not GPIO input selector 0. Registers stride 4. */
    s->matrix = REG_READ(GPIO_FUNC0_IN_SEL_CFG_REG + 4 * FSPIQ_IN_IDX);
}
void esp_io_spi_pad_evidence(esp_io_spi_pad_snapshot *before,
                           esp_io_spi_pad_snapshot *during) {
    *before = spi_pad_before; *during = spi_pad_during;
}
int esp_io_spi_pad_sample(unsigned pull, esp_io_spi_pad_result *r) {
    if (!r || pull > 2) return ESP_ERR_INVALID_ARG;
    if (esp_io_spi_busy()) return ESP_ERR_INVALID_STATE;
    memset(r, 0, sizeof *r);
    esp_io_spi_pad_snapshot_get(&r->before);
    /* gpio_reset_pin() used by SPI close DISABLES input. Explicitly enable
     * input-only; never output-enable GPIO0 or touch any other SPI pin. */
    gpio_config_t cfg = {.pin_bit_mask = UINT64_C(1) << BOARD_SPI_MISO,
        .mode = GPIO_MODE_INPUT, .pull_up_en = pull == 1,
        .pull_down_en = pull == 2, .intr_type = GPIO_INTR_DISABLE};
    esp_err_t rc = gpio_config(&cfg);
    if (rc == ESP_OK) {
        esp_io_spi_pad_snapshot_get(&r->sampled);
        for (unsigned i = 0; i < 16; ++i) {
            if (gpio_get_level(BOARD_SPI_MISO)) ++r->high; else ++r->low;
            vTaskDelay(pdMS_TO_TICKS(1) ? pdMS_TO_TICKS(1) : 1);
        }
    }
    /* Do not restore unknown disabled/output/peripheral state. Explicit idle
     * contract: input-only, no pull; next SPI initialize owns its configuration. */
    cfg.pull_up_en = 0; cfg.pull_down_en = 0;
    esp_err_t restore_rc = gpio_config(&cfg);
    esp_io_spi_pad_snapshot_get(&r->restored);
    return rc != ESP_OK ? rc : restore_rc;
}

int esp_io_spi_open(uint32_t hz, const char **error) {
    if (error) *error = "none";
    if (esp_io_spi_busy()) {
        if (error) *error = "previous SPI cleanup failed; reboot required";
        return ESP_ERR_INVALID_STATE;
    }
    esp_io_spi_pad_snapshot_get(&spi_pad_before);
    memset(&spi_pad_during, 0, sizeof spi_pad_during);
    spi_bus_config_t bus = {
        .mosi_io_num = BOARD_SPI_MOSI, .miso_io_num = BOARD_SPI_MISO,
        .sclk_io_num = BOARD_SPI_SCLK, .quadwp_io_num = -1,
        .quadhd_io_num = -1, .max_transfer_sz = 64
    };
    esp_err_t rc = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_DISABLED);
    if (rc != ESP_OK) {
        if (error) *error = "spi_bus_initialize";
        return rc;
    }
    spi_bus = true;
    esp_io_spi_pad_snapshot_get(&spi_pad_during);
    spi_device_interface_config_t dev = {
        .clock_speed_hz = (int)hz, .mode = 0,
        .spics_io_num = BOARD_SPI_CS, .queue_size = 1
    }; /* default flags = MSB-first, full duplex */
    rc = spi_bus_add_device(SPI2_HOST, &dev, &spi_device);
    if (rc != ESP_OK && error) *error = "spi_bus_add_device";
    return rc;
}

int esp_io_spi_transfer(const uint8_t tx[64], uint8_t rx[64], const char **error) {
    if (error) *error = "none";
    /* Never overwrite the static transaction/buffers after completion failed. */
    if (!spi_device || spi_pending) {
        if (error) *error = "SPI completion timeout/error";
        return ESP_ERR_INVALID_STATE;
    }
    if (!tx || !rx) {
        if (error) *error = "SPI queue";
        return ESP_ERR_INVALID_ARG;
    }
    esp_io_spi_pad_snapshot_get(&spi_pad_during);
    memcpy(spi_tx, tx, sizeof spi_tx);
    memset(spi_rx, 0, sizeof spi_rx);
    memset(&spi_transaction, 0, sizeof spi_transaction);
    spi_transaction.length = 512;
    spi_transaction.rxlength = 512;
    spi_transaction.tx_buffer = spi_tx;
    spi_transaction.rx_buffer = spi_rx;
    esp_err_t rc = spi_device_queue_trans(spi_device, &spi_transaction, pdMS_TO_TICKS(100));
    if (rc != ESP_OK) {
        if (error) *error = "SPI queue";
        return rc;
    }
    spi_pending = true;
    spi_transaction_t *result = NULL;
    rc = spi_device_get_trans_result(spi_device, &result, pdMS_TO_TICKS(100));
    if (rc != ESP_OK || result != &spi_transaction) {
        if (error) *error = "SPI completion timeout/error";
        return rc != ESP_OK ? rc : ESP_FAIL;
    }
    spi_pending = false;
    memcpy(rx, spi_rx, sizeof spi_rx);
    return ESP_OK;
}

int esp_io_spi_close(void) {
    esp_err_t rc = ESP_OK;
    /* A failed wait is not proof the driver released the transaction. Reap
     * only an already-completed result; otherwise retain bus/device/storage. */
    if (spi_device && spi_pending) {
        spi_transaction_t *result = NULL;
        rc = spi_device_get_trans_result(spi_device, &result, 0);
        if (rc != ESP_OK || result != &spi_transaction)
            return rc != ESP_OK ? rc : ESP_FAIL;
        spi_pending = false;
    }
    if (spi_device) {
        rc = spi_bus_remove_device(spi_device);
        if (rc == ESP_OK) {
            spi_device = NULL;
            spi_pending = false;
        }
    }
    if (spi_bus && !spi_device) {
        rc = spi_bus_free(SPI2_HOST);
        if (rc == ESP_OK) spi_bus = false;
    }
    return rc;
}

esp_err_t esp_io_uart_mode(unsigned baud, bool rom) {
    esp_err_t rc = uart_set_baudrate(UART_PORT, baud);
    if (rc == ESP_OK) rc = uart_set_parity(UART_PORT, rom ? UART_PARITY_EVEN : UART_PARITY_DISABLE);
    if (rc == ESP_OK) rc = gpio_set_pull_mode(BOARD_UART_RX, GPIO_PULLUP_ONLY);
    return rc;
}

static uint64_t pin_mask(int pin) {
    return pin >= 0 && pin < 64 ? UINT64_C(1) << (unsigned)pin : 0;
}

esp_err_t esp_io_target_gpio_init(void) {
    esp_err_t rc = gpio_set_level(BOARD_BOOT0, 0);
    if (rc != ESP_OK) return rc;
    rc = gpio_set_level(BOARD_NRST, 1);
    if (rc != ESP_OK) return rc;
    uint64_t boot_pins = pin_mask(BOARD_BOOT0);
    if (BOARD_BOOT1 >= 0) {
        rc = gpio_set_level(BOARD_BOOT1, 0);
        if (rc != ESP_OK) return rc;
        boot_pins |= pin_mask(BOARD_BOOT1);
    }
    gpio_config_t gpio = {
        .pin_bit_mask = boot_pins, .mode = GPIO_MODE_INPUT_OUTPUT,
        .intr_type = GPIO_INTR_DISABLE
    };
    rc = gpio_config(&gpio);
    if (rc != ESP_OK) return rc;
    gpio.pin_bit_mask = pin_mask(BOARD_NRST);
    gpio.mode = GPIO_MODE_INPUT_OUTPUT_OD;
    return gpio_config(&gpio);
}

esp_err_t esp_io_uart_init(QueueHandle_t *events, size_t rx_size) {
    if (!events || rx_size > INT_MAX) return ESP_ERR_INVALID_ARG;
    uart_config_t uart = {
        .baud_rate = 115200, .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_EVEN, .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT
    };
    esp_err_t rc = uart_param_config(UART_PORT, &uart);
    if (rc == ESP_OK) rc = uart_set_pin(UART_PORT, BOARD_UART_TX, BOARD_UART_RX,
                                      UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (rc == ESP_OK) rc = uart_driver_install(UART_PORT, (int)rx_size, 0, 64, events, 0);
    if (rc == ESP_OK) rc = gpio_set_pull_mode(BOARD_UART_RX, GPIO_PULLUP_ONLY);
    /* IDF 5.5.5's default UART event mask omits frame errors. */
    if (rc == ESP_OK) rc = uart_enable_intr_mask(UART_PORT, UART_FRM_ERR_INT_ENA_M);
    return rc;
}
