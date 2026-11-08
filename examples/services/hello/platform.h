#ifndef HELLO_PLATFORM_H
#define HELLO_PLATFORM_H
/* Platform loop invokes the explicit root-only heartbeat in task context.
 * It must never invoke a connection's active-lease feed. */
typedef void (*hello_heartbeat_fn)(void);
int hello_platform_start(hello_heartbeat_fn heartbeat);
void hello_platform_yield(void);
void hello_root_heartbeat(void);
#endif
