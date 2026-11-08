#include "rmb/service.h"
extern int module_business_start(const rmb_host *);
extern void module_business_control(rmb_connection, void *);
extern void module_business_uart(rmb_connection, void *);
int module_entry(const rmb_host *h) {
    if (!h || h->abi != RMB_SERVICE_ABI || h->size < sizeof *h || !h->net ||
        h->net->abi != RMB_SERVICE_ABI || h->net->size < sizeof *h->net || !h->now_us ||
        !h->progress || !h->run || !h->net->listen || !h->net->exact || !h->net->read ||
        !h->net->write || !h->net->authenticated || !h->watchdog_open || !h->watchdog_feed ||
        !h->watchdog_close)
        return -1;
    if (module_business_start(h))
        return -1;
    if (h->net->listen(3333, RMB_NET_AUTH_REPLAY, module_business_control, 0))
        return -1;
    return h->net->listen(3336, RMB_NET_AUTH_REPLAY, module_business_uart, 0);
}
/* First 32 data bytes bind to exact resident ELF SHA256. */
const unsigned char module_base_binding[32] __attribute__((section(".module_base_id"), used)) = {1};
