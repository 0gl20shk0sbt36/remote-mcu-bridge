#ifndef ESP_MODULE_BOARD_CONFIG_H
#define ESP_MODULE_BOARD_CONFIG_H

#include "sdkconfig.h"
#include "driver/uart.h"

/* Build-owned feature, never inferred from resident sdkconfig. */
#ifndef RMB_BUILTIN_SPI
#define RMB_BUILTIN_SPI 0
#endif
#if RMB_BUILTIN_SPI != 0 && RMB_BUILTIN_SPI != 1
#error "RMB_BUILTIN_SPI must be 0 or 1"
#endif

/* Fixed wiring of this ESP32-C3 board, not a portable HAL configuration. */
#if RMB_BUILTIN_SPI
#define BOARD_SPI_SCLK 7
#define BOARD_SPI_MOSI 1
#define BOARD_SPI_MISO 0
#define BOARD_SPI_CS 10
#endif
#define BOARD_UART_TX 3
#define BOARD_UART_RX 4
#define BOARD_BOOT0 5
#define BOARD_NRST 6
#define BOARD_BOOT1 CONFIG_ROM_SERVICE_BOOT1
#define UART_PORT UART_NUM_1

/* Never silently compile the fixed-board backend against different wiring. */
_Static_assert(CONFIG_ROM_SERVICE_TX == BOARD_UART_TX, "board UART TX differs from sdkconfig");
_Static_assert(CONFIG_ROM_SERVICE_RX == BOARD_UART_RX, "board UART RX differs from sdkconfig");
_Static_assert(CONFIG_ROM_SERVICE_BOOT0 == BOARD_BOOT0, "board BOOT0 differs from sdkconfig");
_Static_assert(CONFIG_ROM_SERVICE_NRST == BOARD_NRST, "board NRST differs from sdkconfig");

#endif
