#pragma once
#include <stdbool.h>
#include "esp_err.h"
#include "module_abi.h"
/* Nonexecuting reservation: must precede NVS/Wi-Fi mappings at boot. */
esp_err_t module_runtime_prepare(void);
uintptr_t module_runtime_text_address(void);
uintptr_t module_runtime_data_address(void);
/* Executes entry: supervised worker only, never upload manager. */
esp_err_t module_runtime_init(void);
bool module_runtime_ready(void);
bool module_runtime_busy(void);
esp_err_t module_runtime_invalidate(void);
esp_err_t module_runtime_install_begin(size_t);
esp_err_t module_runtime_install_write(size_t,const void *,size_t);
esp_err_t module_runtime_install_finish(const uint8_t expected_sha256[32]);
const char *module_runtime_info(void);
bool module_runtime_update_allowed(void);
bool module_runtime_code_pointer(const void *);
