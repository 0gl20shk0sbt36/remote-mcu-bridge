#pragma once
#include <stdbool.h>
#include "rmb/service.h"

/* INIT is a single boot-time transaction on the module-entry worker. Register
 * listeners between begin/commit; commit publishes all or aborts all. Abort is
 * terminal until reset, not a general shutdown API for active callbacks. */
extern const rmb_net_api rmb_network_api;
void rmb_network_init_begin(void);
bool rmb_network_init_commit(void);
void rmb_network_init_abort(void);

/* Borrowed handles may be used by a synchronous supervised job while its
 * callback waits for completion, never retained after callback return.
 * read/write(nonblocking=false) wait at most 5 seconds; true never waits for
 * a busy connection or socket. exact uses the caller's absolute monotonic
 * deadline, including connection-lock contention. Zero-length I/O returns 0.
 * Authentication: <=2048 bytes including LF, <=256 decoded token bytes,
 * <=16 JSON nesting levels, <=10 seconds total. No authentication ACK.
 */
