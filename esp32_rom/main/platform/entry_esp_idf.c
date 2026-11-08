/* ESP-IDF entry point for the portable resident.
 *
 * A port for another platform calls rmb_resident_main() from its own start-up
 * code; nothing else in esp32_rom/main is platform specific.
 */
#include "sdkconfig.h"

#include "rmb/port.h"

extern void rmb_resident_main(void);

#ifndef CONFIG_RMB_ENABLE
void app_main(void) {
    RMB_LOGW("rmb-base", "Disabled by default; configure wiring and secret before enabling");
}
#else
void app_main(void) {
    rmb_resident_main();
}
#endif
