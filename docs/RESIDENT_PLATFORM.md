# 底座的平台移植 / Resident platform porting

## 中文

本页说明"换一台宿主平台"到底要写多少代码；**执行清单与逐项要求见 [跨平台移植手册](PORTING_GUIDE.md)**。底座逻辑（鉴权、管理协议、日志环、监督、单槽更新、模块准入）已经是平台无关 C；平台相关部分全部收敛在 `include/rmb/port.h` 一个契约里，ESP-IDF 只是其中一个实现。

### 1. 边界怎么划

| 归属 | 位置 | 说明 |
|---|---|---|
| 可移植底座 | `esp32_rom/main/resident.c`、`generic_network.c`、`module_runtime.c`、`streams.inc`、`supervisor.inc`、`service_watchdog.inc`、`core/update.c`、`core/supervisor.c` | 不出现任何 `esp_*`/FreeRTOS 标识符 |
| 平台契约 | `include/rmb/port.h` | 46 个函数 + 若干类型/属性宏 |
| 平台实现 | `esp32_rom/main/platform/` | 唯一允许 `#include "esp_*/freertos/*"` 的目录 |
| 服务（业务） | `esp32_rom/module/`、`examples/services/` | 按宿主 SDK 正常写，不属于底座移植范围 |

这条边界由 `esp32_rom/tests/test_port_boundary.py` 机器强制：可移植文件出现平台标识符、或契约函数没有被平台实现，测试直接失败。

### 2. 新平台需要实现什么

| 组 | 函数 | 语义要求 |
|---|---|---|
| 时间/日志 | `now_us`、`delay_ticks`、`log` | 单调微秒；日志不得分配内存或阻塞，格式化后回调 `rmb_log_capture` |
| 复位 | `restart`、`reset_reason` | 整机复位；复位原因映射到 `rmb_reset_reason`（数值见头文件注释，勿重编号） |
| 临界区 | `lock_enter`/`lock_exit`、`RMB_LOCK_INIT` | 屏蔽任务与中断抢占的最短临界区 |
| 任务 | `task_create/current/end/terminate/wait_notify/notify/stack_min_free` | `stack_bytes` 为字节；栈余量用于诊断 |
| 同步 | `mutex_*`、`sem_*`、`queue_*`、`event_*` | tick 语义与 FreeRTOS 一致；0=不等待，`RMB_WAIT_FOREVER`=永久 |
| 看门狗 | `wdt_configure/register_current/feed` | 由底座定策略，平台只执行；必须能触发复位而非仅告警 |
| 保留内存 | `retained` | 软复位后内容仍在；不承诺掉电保持 |
| 存储槽 | `slot_geometry/read/write/erase/sha256` | 相对槽基址的字节偏移；几何必须等于 `MODULE_SLOT_SIZE`/`MODULE_FOOTER_SIZE` |
| 可执行映射 | `slot_map/unmap/lock` | 返回可执行基址（模块在此后 40 字节起链接）；`lock` 不能实现时**必须失败**，不能假装成功 |
| 身份 | `base_sha256` | 当前运行镜像的 32 字节摘要，用于模块精确绑定 |
| 网络 | `net_start`、`net_info`、`rmb_resident_net_event` | 关联+地址就绪；有地址才算可用；平台在链路/地址变化时回调底座 |

另外契约默认要求 **BSD 风格 socket**（`socket/select/recv/send/close/shutdown`）：底座用它们做管理口、日志口与发现，这是 lwIP/POSIX 生态的公共子集。若平台没有 BSD socket，需要额外提供该适配层（当前不在契约内，属于已知边界）。

### 3. 参考实现（ESP-IDF）

| 契约 | ESP-IDF 实现 | 备注 |
|---|---|---|
| 时间/复位/临界区/任务/同步 | FreeRTOS + `esp_timer` | 直映射 |
| 看门狗 | `esp_task_wdt_*` + IWDT | 配置为 panic 复位 |
| 保留内存 | `RTC_NOINIT_ATTR` | 软复位保持 |
| 存储槽 | `esp_partition_*`（business 分区） | 地址/大小写死校验 |
| 可执行映射 | `esp_partition_mmap(... INST ...)` | 报告实际虚拟地址，模块按它链接 |
| 身份 | `esp_app_get_description()->app_elf_sha256` | 精确底座绑定 |
| 网络 | Wi-Fi STA + `esp_netif`，发现任务在 `net_esp_idf.c` | 换成以太网/其他栈只改这一层 |

### 4. 移植步骤

1. 复制 `esp32_rom/main/platform/` 为 `<your-platform>/`，实现 `port.h` 全部函数（`--help` 可用 `nm` 或直接编译验证）。
2. 提供平台头（`-DRMB_PORT_PLATFORM_HEADER="..."`），给出类型、`RMB_LOCK_INIT`、`RMB_RETAINED`、`RMB_DRAM_RESERVE_SECTION` 与 `RMB_CONFIG_*` 编译期配置。
3. 在自己的启动流程里调用 `rmb_resident_main()`；`app_main` 只做入口转发。
4. 跑 `esp32_rom/tests/test_port_boundary.py`，确认可移植文件没有泄漏平台 API，且契约函数齐全。
5. 按平台重做模块构建（可执行基址、段布局、重定位校验），底座本身不需要改。

底座**不**要求平台提供：STM32 相关任何东西、SPI/UART 目标驱动、具体的烧录协议——那些属于服务/后续内置 BL 抽象。

## English

This page states how much code a host port actually needs; **the execution checklist and per-item requirements are in the [porting handbook](PORTING_GUIDE.md)**. The resident logic (authentication, management protocol, log ring, supervision, single-slot update, module admission) is platform-free C; everything platform-specific is behind one contract, `include/rmb/port.h`, and ESP-IDF is just one implementation.

### 1. Where the boundary sits

| Owner | Location | Rule |
|---|---|---|
| Portable resident | `esp32_rom/main/resident.c`, `generic_network.c`, `module_runtime.c`, `streams.inc`, `supervisor.inc`, `service_watchdog.inc`, `core/update.c`, `core/supervisor.c` | No `esp_*`/FreeRTOS identifiers |
| Platform contract | `include/rmb/port.h` | 46 functions plus type/attribute macros |
| Platform implementation | `esp32_rom/main/platform/` | The only directory allowed to include platform headers |
| Services (business logic) | `esp32_rom/module/`, `examples/services/` | Written against the host SDK; not part of a resident port |

`esp32_rom/tests/test_port_boundary.py` enforces this mechanically: a platform identifier in a portable file, or a contract function the platform does not implement, fails the test.

### 2. What a new platform implements

| Group | Functions | Requirement |
|---|---|---|
| Time/log | `now_us`, `delay_ticks`, `log` | Monotonic microseconds; logging must not allocate or block and must hand formatted bytes to `rmb_log_capture` |
| Reset | `restart`, `reset_reason` | Whole-chip reset; map causes onto `rmb_reset_reason` (numeric values are persisted — do not renumber) |
| Critical section | `lock_enter`/`lock_exit`, `RMB_LOCK_INIT` | Shortest section that excludes task and interrupt preemption |
| Tasks | `task_create/current/end/terminate/wait_notify/notify/stack_min_free` | `stack_bytes` is bytes; free-stack readout is diagnostic |
| Synchronisation | `mutex_*`, `sem_*`, `queue_*`, `event_*` | FreeRTOS tick semantics: 0 = no wait, `RMB_WAIT_FOREVER` = block |
| Watchdog | `wdt_configure/register_current/feed` | The resident sets policy, the platform executes it and must be able to reset |
| Retained RAM | `retained` | Survives a software reset; power loss is not promised |
| Business slot | `slot_geometry/read/write/erase/sha256` | Byte offsets from the slot base; geometry must equal `MODULE_SLOT_SIZE`/`MODULE_FOOTER_SIZE` |
| Executable mapping | `slot_map/unmap/lock` | Reports the executable base (module text starts 40 bytes later); if `lock` cannot be enforced it must fail, never pretend |
| Identity | `base_sha256` | Digest of the running image for exact module binding |
| Network | `net_start`, `net_info`, `rmb_resident_net_event` | Usable only once an address exists; the platform reports link/address changes |

The contract additionally expects **BSD-style sockets** (`socket/select/recv/send/close/shutdown`) for management, logs and discovery — the common subset of lwIP/POSIX stacks. A platform without them needs an extra adapter, which is a known boundary rather than part of the contract today.

### 3. Reference implementation (ESP-IDF)

| Contract | ESP-IDF | Note |
|---|---|---|
| Time/reset/critical/tasks/sync | FreeRTOS + `esp_timer` | Direct mapping |
| Watchdog | `esp_task_wdt_*` + IWDT | Configured to panic-reset |
| Retained RAM | `RTC_NOINIT_ATTR` | Kept across software reset |
| Business slot | `esp_partition_*` on the `business` partition | Address and size checked |
| Executable mapping | `esp_partition_mmap(... INST ...)` | Reports the real virtual address; modules link against it |
| Identity | `esp_app_get_description()->app_elf_sha256` | Exact resident binding |
| Network | Wi-Fi STA + `esp_netif`; discovery inside `net_esp_idf.c` | Ethernet or another stack replaces only this layer |

### 4. Porting steps

1. Copy `esp32_rom/main/platform/` and implement every `port.h` function.
2. Provide a platform header via `-DRMB_PORT_PLATFORM_HEADER="..."` with the handle types, `RMB_LOCK_INIT`, `RMB_RETAINED`, `RMB_DRAM_RESERVE_SECTION` and the `RMB_CONFIG_*` build-time values.
3. Call `rmb_resident_main()` from your start-up code; the platform entry only forwards.
4. Run `esp32_rom/tests/test_port_boundary.py` to prove no platform API leaked and no contract function is missing.
5. Redo module building for the platform's executable base, section layout and relocation checks — the resident itself does not change.

A platform is **not** required to provide anything STM32-related, target SPI/UART drivers, or a specific flashing protocol: those belong to services (and to the later built-in bootloader abstraction).
