#pragma once
#include <stdbool.h>
#include "module_abi.h"
/* Return 0 on success, negative on failure (port error space). */
/* Nonexecuting reservation: must precede network/other mappings at boot. */
int module_runtime_prepare(void);
/* Exact link bases, not allocation hints; text is zero until prepare succeeds. */
uintptr_t module_runtime_text_address(void);
uintptr_t module_runtime_data_address(void);
/* Executes entry: supervised worker only, never upload manager. */
int module_runtime_init(void);
bool module_runtime_ready(void);
bool module_runtime_busy(void);
/* Invalidates commit metadata, not live tasks or mappings; reset before upload. */
int module_runtime_invalidate(void);
int module_runtime_install_begin(size_t);
int module_runtime_install_write(size_t, const void *, size_t);
/* Commit only: successful installation never grants same-boot execution. */
int module_runtime_install_finish(const uint8_t expected_sha256[32]);
const char *module_runtime_info(void);
bool module_runtime_update_allowed(void);
bool module_runtime_code_pointer(const void *);
