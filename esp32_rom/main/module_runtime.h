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
int module_rom_validate(const rom_device *,const rom_source *,uint32_t,uint32_t,int);
int module_rom_download_traced(const rom_io *,const rom_device *,const rom_source *,uint32_t,uint32_t,int,rom_trace_fn,void *);
int module_rom_probe(const rom_io *,const rom_device *,rom_probe_result *,rom_trace_fn,void *);
int module_rom_restart(const rom_io *);

bool module_runtime_update_allowed(void);
int module_runtime_dispatch(uint32_t,void *);
