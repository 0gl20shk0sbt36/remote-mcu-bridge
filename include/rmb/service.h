#ifndef RMB_SERVICE_H
#define RMB_SERVICE_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define RMB_SERVICE_ABI 4u
#define MODULE_ABI_VERSION RMB_SERVICE_ABI
#define RMB_NET_AUTH_CONSUME 0u
#define RMB_NET_AUTH_REPLAY 1u
#define RMB_IO_ERROR (-1)
#define RMB_IO_AGAIN (-2)
typedef int32_t rmb_connection;
typedef void (*rmb_connection_fn)(rmb_connection,void *);
typedef int (*rmb_job_fn)(void *);
typedef struct {
 uint32_t abi,size;
 int (*listen)(uint16_t,uint32_t,rmb_connection_fn,void *);
 bool (*exact)(rmb_connection,void *,size_t,bool,int64_t);
 int (*read)(rmb_connection,void *,size_t,bool);
 int (*write)(rmb_connection,const void *,size_t,bool);
 bool (*authenticated)(const char *);
} rmb_net_api;
typedef struct {
 uint32_t abi,size;
 const rmb_net_api *net;
 int64_t (*now_us)(void);
 void (*progress)(void);
 int (*run)(rmb_job_fn,void *,int64_t);
 int (*watchdog_open)(const char *role,uint32_t timeout_ms);
 int (*watchdog_feed)(int lease,uint32_t checkpoint);
 int (*watchdog_close)(int lease);
} rmb_host;
/* Borrowed handles expire on callback return. Blocking read/write <=5s;
 * exact uses absolute monotonic deadline. run retains context until completion
 * or reset; nesting rejected. Independent listeners need service serialization.
 * Watchdog leases: task-context only, root opened during entry persists to reset;
 * separate task-owned leases prevent healthy workers masking a stuck role. */
typedef int (*module_entry_fn)(const rmb_host *);
#endif
