# 跨平台移植手册 / Cross-platform porting handbook

## 中文

本页回答两个问题：**移植到新平台要做哪些事**、**每件事的硬性要求是什么**。它比 [底座平台移植](RESIDENT_PLATFORM.md)（讲契约与边界）更偏执行清单；实测依赖数据见 [依赖与平台耦合分析](DEPENDENCY_ANALYSIS.md)。

### 0. 移植后你会得到什么

底座（管理协议、鉴权、日志环、单槽更新、模块准入、监督与看门狗、发现）在新平台上原样可用；服务生态需要按新平台重建。**底座代码不改**，只新增平台实现。

### 1. 平台能力门槛（先自查，不满足要先解决）

| 能力 | 要求 | 不满足时 |
|---|---|---|
| 任务/同步原语 | 可创建任务、互斥、计数型/二值信号量、单槽队列、事件位；tick 或毫秒超时 | 需在端口层用现有调度器拼出来，否则底座监督无法工作 |
| 单调时钟 | 微秒级单调递增，含延迟函数 | 无替代方案，必须提供 |
| 可复位看门狗 | 能配置超时并**真正复位**芯片 | 不接受"只告警"；底座把复位当作恢复手段 |
| BSD 风格 socket | `socket/bind/listen/accept/select/recv/send/close/shutdown/setsockopt` | 需额外写适配层（当前不在契约内，属已知边界） |
| 掉电易失但软复位保持的 RAM | ≥ 一个故障日志记录（当前 512 B 区域） | 无则故障上报退化为"未知"，需在文档中明确降级 |
| 单一业务槽 | 可擦写的 Flash 区域，几何 = `0x100000` 字节 + 末尾 `4096` 字节 footer（或同步修改两侧常量） | 无独立槽可用需改分区方案与常量 |
| 可执行映射 | 能把槽内容映射为可执行并报告实际虚拟地址；数据窗口 32 KiB 可写不可执行 | 无 MMU/XIP 的 MCU 只能改成"静态链接服务"，放弃在线换服务 |
| 内存保护/锁定 | 能把代码映射与数据窗口锁定 | 无法锁定时 `rmb_port_slot_lock()` **必须返回失败**，不能假装成功 |
| SHA-256 | 任意实现（可软件） | 无替代方案，模块绑定与镜像校验依赖它 |

### 2. 必须实现的 46 个函数

语义在 `include/rmb/port.h` 里逐条写死，这里给分组与要点（顺序无关）：

| 组 | 函数（46） | 要点 |
|---|---|---|
| 时间 | `now_us`、`delay_ticks` | 单调；`delay_ticks` 接受 tick，`RMB_MS_TO_TICKS` 由平台头提供 |
| 日志 | `log(level, tag, fmt, ...)` | 不得分配内存/阻塞；格式化后必须回调 `rmb_log_capture(bytes, len)`（底座实现） |
| 复位 | `restart`、`reset_reason` | `reset_reason` 返回 `rmb_reset_reason`，数值**不可重编号**（见 §5） |
| 临界区 | `lock_enter`/`lock_exit` + `rmb_lock_t`/`RMB_LOCK_INIT` | 需屏蔽任务与中断抢占；只允许极短临界区 |
| 任务 | `task_create/current/end/terminate/wait_notify/notify/stack_min_free`、`in_isr` | `stack_bytes` 单位是**字节**；`task_terminate` 仅用于初始化回滚 |
| 同步 | `mutex_create/take/give/delete`、`sem_*`、`queue_create/send/receive`、`event_create/set/clear/get/wait` | tick 语义：0=不等待、`RMB_WAIT_FOREVER`=永久；`take/send/receive` 返回 0 表示成功 |
| 看门狗 | `wdt_configure/register_current/feed` | 看门狗由底座配置策略，服务显式喂；注册/喂狗在任务上下文 |
| 保留内存 | `retained(bytes, align)` | 软复位后内容保持；不承诺掉电保持 |
| 业务槽 | `slot_geometry/read/write/erase/sha256` | 偏移相对槽基址；几何必须与常量一致（§5） |
| 可执行映射 | `slot_map/unmap/lock` | `slot_map` 返回可执行基址；`lock` 无法保证时必须失败 |
| 身份 | `base_sha256` | 当前运行镜像的 32 字节摘要，必须与模块内嵌的绑定一致 |
| 网络 | `net_start(ssid, pass)`、`net_info` | 有地址才算可用；链路/地址变化时调用 `rmb_resident_net_event(0/1)` |

### 3. 必须提供的平台头

用 `-DRMB_PORT_PLATFORM_HEADER="<你的头>"` 指定，头里必须给出：

- 句柄类型：`rmb_lock_t`、`rmb_task_t`、`rmb_mutex_t`、`rmb_sem_t`、`rmb_queue_t`、`rmb_event_t`
- 宏：`RMB_LOCK_INIT`、`RMB_RETAINED`（保留 RAM 属性，可空）、`RMB_DRAM_RESERVE_SECTION`（数据窗口段属性，可空）、`RMB_WAIT_FOREVER`、`RMB_WAIT_NOW`、`RMB_MS_TO_TICKS(ms)`
- 结构：`rmb_wdt_config_t { timeout_ms, idle_core_mask, trigger_panic }`
- 编译期配置：`RMB_CONFIG_TOKEN`（16–128 B）、`RMB_CONFIG_SSID`、`RMB_CONFIG_PASSWORD`、`RMB_CONFIG_PORT`、`RMB_CONFIG_DISCOVERY_PORT`、`RMB_CONFIG_LISTENER_STACK_BYTES`

参考实现：`esp32_rom/main/platform/esp_idf_port.h`（54 行）。

### 4. 需要新增/修改的文件

| 动作 | 文件 | 说明 |
|---|---|---|
| 新增 | `<platform>/port_*.c` | 46 个函数实现（ESP 参考 352 行） |
| 新增 | `<platform>/net_*.c` | 关联/地址/发现；ESP 版 175 行 |
| 新增 | `<platform>/entry_*.c` | 启动后调用 `rmb_resident_main()`（ESP 版 20 行） |
| 新增 | `<platform>/port_*.h` | §3 的平台头（ESP 版 54 行） |
| 修改 | 构建系统 | 把上述文件编入、传 `-DRMB_PORT_PLATFORM_HEADER=...`、链接 BSD socket 与 SHA 实现 |
| 新增/修改 | `backends/<id>/backend.json` | `implementation`、`driver`、`capabilities`、`requirements`、`supported_targets` |
| 新增/修改 | `boards/<board>.json` | `target`、`target_device`、`pins`、`defaults`（指向唯一的 `config/build.defaults`） |
| 修改 | `config/build.defaults` | 新平台的编译期配置（单一文件原则） |
| **不改** | `esp32_rom/main/*.c`、`*.inc`、`core/`、`include/rmb/` | 可移植部分；改动会破坏边界测试 |

### 5. 不可更改的 ABI 常量与语义

| 项 | 值 | 原因 |
|---|---|---|
| 槽大小 / footer | `0x100000` / `4096` | 与底层分区、更新状态机两侧共享 |
| 镜像头 | 40 字节，`magic=0x314d5242`、ABI=4 | 已烧录设备的兼容基线 |
| 数据窗口 | 32 KiB | 模块 data/BSS 上限 |
| 复位原因数值 | 1 上电 / 3 软复位 / 4 panic / 5 中断看门狗 / 6 任务看门狗 / 7 其它 | 写入故障日志并原样上报给客户端 |
| 绑定语义 | 模块内嵌 32 字节运行镜像 SHA-256 | 换底座必须重建服务；不要弱化比较 |
| 错误语义 | 端口函数 0=成功、负=失败；`slot_lock` 不能假装成功 | 底座按此判定准入与故障 |

### 6. 模块构建器的移植

服务镜像不是普通 ELF：`esp32_rom/module/build_module.py` 用 `--just-symbols=<base ELF>` 导入底座地址、按预留地址链接、并校验导入/重定位。新平台需要等价物：

1. 从底座构建产物导出符号地址（等价 `--just-symbols` 或链接脚本 + 符号表）。
2. 用平台确切的代码/数据基址链接服务，生成 `module_header` 要求的布局（代码、data、bss、entry）。
3. 证明"导入的是底座符号而非同名本地符号"（按**重定位目标地址**判定，不只比名字）。
4. 记录基线哈希（`base_sha256`）、地址、抽取的库成员，产出 `proof.json` 供审计。

### 7. 服务的移植

服务按平台 SDK 正常写（UART/GPIO/SPI 等），通过 `rmb_host` 使用底座能力；不得新增对 `rmb_*` 底座私有符号的依赖（链接期会暴露）。默认 `stm-uart` 服务是 ESP-IDF 实现，换平台需按新 SDK 重写驱动层；内置 BL 抽象（统一串口烧录协议）尚未实现，属后续工作。

### 8. 验收清单（缺一项就不算移植完成）

| 项 | 方法 | 通过标准 |
|---|---|---|
| 边界 | `python3 esp32_rom/tests/test_port_boundary.py` | 可移植文件零平台标识符；46 个函数全部有实现 |
| 端口自测 | 自己的单元测试 + 现有原生测试（`ctest --preset native`） | 全部通过；注意 fixture 需要 `IDF_PATH` 才取得到 cJSON |
| 交叉构建 | 两个 profile（网络关闭/开启） | 都能编译链接，启用档配置需通过看门狗策略校验 |
| 模块链接 | 用真实底座 ELF + 实机上报地址构建服务 | 无未解析符号、无重复底座定义、绑定校验通过 |
| 实机-基础 | 串口烧录 + 整片校验 | 摘要一致 |
| 实机-服务 | 上传 → 重启 → `module_ready` | 服务真正运行；错误地址/旧底座镜像被拒绝 |
| 实机-业务 | 目标操作（如 STM probe/下载/UART） | 功能可用，非仅"能连上" |
| 实机-监督 | 停止喂狗、入口失败、长作业超时、关中断 | 均能复位并在重连后读到故障记录，序号确认不解除隔离 |
| 文档 | 平台头与移植差异、未验证项 | 明确写出降级点（如无保留 RAM、无锁定） |

### 9. 常见坑

1. **栈单位是字节**：FreeRTOS 原版 API 用 words，ESP-IDF 用字节；混用会让回调栈溢出（本项目实机遇到过 6 KiB 不够）。
2. **看门狗必须真复位**：只打日志的实现会让"服务卡死"变成永久假活。
3. **`slot_lock` 不许假装成功**：做不到就返回失败，底座会拒绝加载模块，这是正确行为。
4. **保留 RAM 不等于断电保持**：故障日志在断电后丢失要写进文档，别承诺。
5. **BSD socket 是隐含依赖**：没有 socket 的平台要先补适配层，否则管理/日志/发现全部不可用。
6. **地址必须来自设备**：写死或换算错会被准入拒绝（实测：取指地址写错一次即被拦下）。
7. **别在可移植文件里加平台判断**：边界测试会失败，且后续维护成本翻倍。

## English

This page answers two questions: **what a port must do** and **the hard requirement behind each item**. It is the execution checklist companion to [Resident platform porting](RESIDENT_PLATFORM.md) (contract and boundary) and to the measured [dependency analysis](DEPENDENCY_ANALYSIS.md).

### 0. What a port yields

The resident (management protocol, authentication, log ring, single-slot update, module admission, supervision, watchdog, discovery) works unchanged on the new platform; the service ecosystem is rebuilt for it. **Resident sources are not modified** — only a platform implementation is added.

### 1. Platform capability gate

| Capability | Requirement | If unavailable |
|---|---|---|
| Tasks and primitives | Task creation, mutex, binary/counting semaphore, single-slot queue, event bits, tick or millisecond timeouts | Build them on the existing scheduler in the port layer, otherwise supervision cannot work |
| Monotonic clock | Microsecond monotonic time plus a delay call | No substitute; mandatory |
| Resettable watchdog | Configurable timeout that **actually resets** the chip | "Log only" is rejected; the resident relies on reset for recovery |
| BSD-style sockets | `socket/bind/listen/accept/select/recv/send/close/shutdown/setsockopt` | Needs an extra adapter (a known boundary, not part of the contract today) |
| Reset-surviving RAM | At least one fault-journal record (512-byte region) | Fault reporting degrades to "unknown"; document the degradation |
| Single business slot | Erasable flash region of `0x100000` bytes with a trailing `4096`-byte footer (or change the constants on both sides) | Without a dedicated slot the partition scheme and constants must change |
| Executable mapping | Map the slot as executable and report the real virtual address; 32 KiB writable non-executable data window | MCUs without MMU/XIP can only fall back to statically linked services, losing in-place replacement |
| Protection/locking | Ability to lock the code mapping and data window | If it cannot be enforced, `rmb_port_slot_lock()` **must fail**; never pretend |
| SHA-256 | Any implementation, software is fine | Mandatory: module binding and image verification depend on it |

### 2. The 46 required functions

Semantics are pinned in `include/rmb/port.h`; the grouping below lists the essentials: time (`now_us`, `delay_ticks`), logging (`log`, which must also call `rmb_log_capture`), reset (`restart`, `reset_reason` handing back `rmb_reset_reason` whose numbering must not change), critical section (`lock_enter/exit` plus `rmb_lock_t`/`RMB_LOCK_INIT`), tasks (`task_create/current/end/terminate/wait_notify/notify/stack_min_free`, `in_isr` — `stack_bytes` is in bytes), synchronisation (`mutex_*`, `sem_*`, `queue_*`, `event_*` with FreeRTOS tick semantics and 0 on success), watchdog (`wdt_configure/register_current/feed`), retained memory (`retained`), business slot (`slot_geometry/read/write/erase/sha256`), executable mapping (`slot_map/unmap/lock`), identity (`base_sha256`) and network (`net_start`, `net_info`, plus `rmb_resident_net_event` on link changes).

### 3. Required platform header

Selected with `-DRMB_PORT_PLATFORM_HEADER="<your header>"`; it must provide the handle types (`rmb_lock_t`, `rmb_task_t`, `rmb_mutex_t`, `rmb_sem_t`, `rmb_queue_t`, `rmb_event_t`), the macros `RMB_LOCK_INIT`, `RMB_RETAINED`, `RMB_DRAM_RESERVE_SECTION`, `RMB_WAIT_FOREVER`, `RMB_WAIT_NOW`, `RMB_MS_TO_TICKS(ms)`, the `rmb_wdt_config_t` struct, and the build-time configuration (`RMB_CONFIG_TOKEN` 16..128 bytes, `RMB_CONFIG_SSID`, `RMB_CONFIG_PASSWORD`, `RMB_CONFIG_PORT`, `RMB_CONFIG_DISCOVERY_PORT`, `RMB_CONFIG_LISTENER_STACK_BYTES`). The ESP-IDF reference header is 54 lines.

### 4. Files to add or change

Add `<platform>/port_*.c` (the 46 functions; ESP reference 352 lines), `net_*.c` (175 lines), `entry_*.c` (20 lines) and the platform header (54 lines); wire them into the build system with the header define and BSD sockets/SHA linkage; add or extend `backends/<id>/backend.json` (`implementation`, `driver`, `capabilities`, `requirements`, `supported_targets`) and `boards/<board>.json` (`target`, `target_device`, `pins`, `defaults` pointing at the single `config/build.defaults`), and update that one configuration file for the platform. **Do not** modify `esp32_rom/main/*.c`, the `.inc` units, `core/` or `include/rmb/` — the boundary test guards them.

### 5. Frozen ABI constants and semantics

Slot `0x100000` with a `4096`-byte footer; 40-byte image header with `magic=0x314d5242` and ABI 4; 32 KiB data window; reset-cause numbering (1 power-on, 3 software, 4 panic, 5 interrupt watchdog, 6 task watchdog, 7 other) because it is persisted and reported verbatim; the 32-byte running-image SHA-256 binding (never weaken the comparison); and port error semantics (0 = success, negative = failure, `slot_lock` must fail rather than pretend).

### 6. Porting the module builder

A service image is not an ordinary ELF. `build_module.py` imports base addresses via `--just-symbols=<base ELF>`, links at the reserved addresses and verifies imports/relocations. A port needs the equivalent: export base symbol addresses, link services at the platform's exact code/data bases with the header layout, prove that imports really target base symbols by **relocation address** rather than name, and record the base hash, addresses and extracted archive members in a `proof.json`.

### 7. Porting services

Services are written against the host SDK (UART/GPIO/SPI) and use resident capabilities only through `rmb_host`; they must not add dependencies on private `rmb_*` symbols (the linker exposes any attempt). The default `stm-uart` service is ESP-IDF code and needs a new driver layer per platform; the built-in bootloader abstraction (one serial protocol) does not exist yet.

### 8. Acceptance checklist

Boundary test (`test_port_boundary.py`: zero platform identifiers in portable files, all 46 functions implemented), native tests (`ctest --preset native`, with `IDF_PATH` so cJSON is found), both build profiles compiling, module cross-link against the real base ELF and device-reported addresses, serial flash with whole-image verification, service upload to `module_ready` (wrong address/old base rejected), real business function on the target, supervision cases (stopped feed, failed entry, timed-out job, disabled interrupts) each resetting and producing a readable fault record whose sequence acknowledgement does not lift quarantine, and documentation of any degradation (no retained RAM, no locking).

### 9. Common pitfalls

Stack units are bytes here (mixing words causes overflow — observed on hardware at 6 KiB); a watchdog that only logs turns a stuck service into a permanent zombie; `slot_lock` must not claim success it cannot enforce; retained RAM is not power-loss persistence; BSD sockets are an implicit dependency for management/logs/discovery; addresses must come from the device (a wrong text address was rejected on hardware); and platform conditionals inside portable files fail the boundary test.
