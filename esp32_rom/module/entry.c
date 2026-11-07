#include "module_abi.h"
extern int module_business_start(const module_host *);
extern int module_business_dispatch(uint32_t,void *);
static const module_ops ops={MODULE_ABI_VERSION,sizeof(module_ops),rom_validate,rom_download_traced,rom_probe,rom_restart,module_business_dispatch};
const module_ops *module_entry(const module_host *h){if(!h||h->abi!=MODULE_ABI_VERSION||h->size!=sizeof *h)return 0;if(module_business_start(h))return 0;return &ops;}

/* ABI3: first 32 data bytes are patched to exact base ELF SHA256. */
const unsigned char module_base_binding[32] __attribute__((section(".module_base_id"),used))={1};
