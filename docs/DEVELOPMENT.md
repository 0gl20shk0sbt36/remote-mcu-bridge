# 维护与二次开发 / Development and maintenance

## 中文

### 1. 开发边界与阅读路线

本项目不是把 STM32 ROM 固化在底座中的远程串口固件。当前架构为 ABI4 通用驻留底座、独立服务和 STM32 目标程序三层；只有 ESP-IDF 后端已实现，SDK 基线为 ESP-IDF 5.5.5，当前板组合是 ESP32-C3 + STM32F103C8。F407ZG 的设备几何存在于源码，不等于该板组合已验收。

建议依次阅读 [使用与部署](USER_GUIDE.md)、[服务 SDK 契约](SERVICE_AUTHORING.md)、[默认 STM 服务](DEFAULT_STM_FIRMWARE.md)、[更新边界](BASE_PLATFORM_BOUNDARY.md) 与 [看门狗](WATCHDOG_RECOVERY.md)。修改 ABI 前阅读 [ABI4 重构合同](ABI4_REFACTOR.md)；添加后端阅读 [后端移植](BACKEND_PORTING.md)。

| 层/文件 | 所有权与修改入口 |
|---|---|
| `include/rmb/service.h` | 服务 ABI：认证网络、单调时钟、受监督作业、角色租约；不含 STM/ESP 类型 |
| `include/rmb/update.h`、`core/update.c` | 无 SDK 的单槽提交状态机、几何与写入准入 |
| `include/rmb/supervisor.h`、`core/supervisor.c` | 可移植监督规则 |
| `esp32_rom/main/resident.c`、`supervisor.inc`、`service_watchdog.inc` | 管理协议、日志、监督任务与故障保留（平台无关） |
| `esp32_rom/main/generic_network.c` | 认证、借用 handle、监听 worker、初始化 gate（平台无关） |
| `include/rmb/port.h` | 平台契约：46 个函数（时间/日志/复位/临界区/任务/同步/看门狗/保留内存/业务槽/映射/身份/网络） |
| `esp32_rom/main/platform/` | 唯一的平台实现目录：`esp_idf_port.c`、`net_esp_idf.c`（Wi-Fi/NVS/发现）、`entry_esp_idf.c` |
| `esp32_rom/main/module_runtime.c`、`module_abi.h` | 镜像准入、精确 ELF 绑定与 32 KiB 数据窗口；存储/映射调用端口层（平台无关） |
| `esp32_rom/module/entry.c`、`business.c` | 默认 STM 服务的入口和业务；ROM 操作不应返回底座 |
| `rom/rom.h`、`rom/rom.c`、`rom/devices.c` | 可移植目标 ROM 算法、I/O 契约和几何 |
| `esp32_rom/module/platform/` | 服务自有 UART/GPIO、可选 SPI 与板级定义 |
| `examples/services/hello/` | 无 ROM、无 UART/SPI 的独立 A/B 服务样例 |
| `backends/`、`boards/`、`schemas/`、`tools/rmb.py` | 注册表验证与 SDK 构建入口，不提供自动烧写 |
| `tools/module_ctl.py` / `romctl.py` / `wifidebug.py` | 底座管理 / STM ROM / 日志和应用 UART，各有不同协议 |

### 2.5 平台抽象、依赖度量与板卡注册表

- **边界**：底座平台无关部分只依赖 `include/rmb/port.h` 的 46 个函数；平台实现全部在 `esp32_rom/main/platform/`。`esp32_rom/tests/test_port_boundary.py` 强制两点：可移植文件不得出现平台标识符、契约函数必须有实现。改底座前先跑它。
- **服务侧**：服务只拿到 `rmb_host` 函数表（`net`、`now_us`、`progress`、`run`、`watchdog_*`），**不链接任何 `rmb_*` 底座符号**；它与底座共享 IDF/RTOS/libc 运行时，因此底座一变（含监听栈大小等编译期配置）所有服务必须重建。
- **度量**：`python3 tools/analyze_dependencies.py` 输出各目录的平台引用密度；加 `--proof <service>/module-link/proof.json` 可得到该服务实际导入的底座符号分类。当前实测：服务导入的私有 API 符号 0 个，底座平台代码 443 行且只在一个目录。
- **板卡是数据**：`boards/*/board.json` 描述目标、型号与引脚，后端用 `supported_targets` 声明支持范围；新增板卡只加 JSON。安全性由"描述必须等于编译出的镜像"保证——`verify_board_wiring()` 在配置后逐根比对 `CONFIG_ROM_SERVICE_*`，不一致直接失败。
- 详见 [底座平台移植](RESIDENT_PLATFORM.md) 与 [依赖与平台耦合分析](DEPENDENCY_ANALYSIS.md)。

### 2. 可复现开发环境

原生需要 C11 编译器、CMake、Ninja 和 Python 3。依赖引导脚本可能需要网络；不要把下载失败替换为伪造依赖。

```sh
python3 tools/native_deps.py
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
python3 tools/rmb.py backends
python3 tools/rmb.py boards
```

ESP 开发另外安装 ESP-IDF 5.5.5 及其 ESP32-C3 RISC-V 工具链，激活官方环境后检查 `idf.py --version`。`IDF_PATH` 指向 SDK，编译器在 `PATH`；模块工具还接受 `--tool-prefix`，然后依次使用 `RISCV_TOOL_PREFIX`、`CROSS_COMPILE`、`riscv32-esp-elf-`。脚本不自动 source 环境或安装 SDK。

```sh
python3 tools/rmb.py configure --profile safe
python3 tools/rmb.py build --profile safe
python3 tools/rmb.py configure --profile compile-enabled
python3 tools/rmb.py build --profile compile-enabled
```

编译期配置只有一个受版本控制的文件：`config/build.defaults`。它由板卡注册表的 `defaults` 指向，包含 IDF 基线、看门狗/panic 策略、RMB 端口与监听栈、STM 引脚与型号。构建驱动按优先级追加生成层：profile 覆盖（safe 关闭 `CONFIG_RMB_ENABLE`）→ `--credentials-file`（`wifi.pw` 两行位置式或 `key=value`，令牌缺失时回退 `RMB_TOKEN`）→ `--user-config`（最终覆盖）。凭据层写在构建目录内并设 0600，不进入源码树；成功构建只打印脱敏摘要（是否配置、字节数、占位符状态、栈、STM 型号）。

两个 profile 使用不同构建目录，不应混用。私有部署与烧录流程见 [使用手册](USER_GUIDE.md)。构建驱动检查 SDK 版本、注册表固定板型、生成配置的启用状态、IWDT/TWDT panic 和 panic reboot，并在启用档缺少真实 SSID/令牌时直接失败。修改配置后，已有 `sdkconfig` 不一定自动更新；用新目录重新配置并审核生成值。

### 3. 改服务与改底座不是同一件事

- 只改服务：保留已部署底座的最终 ELF、`compile_commands.json`、SDK 静态库和生成配置；从目标管理 `status` 获取地址，重新构建服务再通过更新启动安装。
- 改底座、栈配置、SDK、链接布局或预留 DRAM：先重新构建/部署底座，再获取地址；**所有服务均须重新绑定新的最终 ELF**。ABI 数字相同不代表二进制可互换。
- 不要复制旧报告中的虚拟地址或 ELF 哈希。text 是目标实际 XIP 映射的镜像正文地址，data 必须匹配该 ELF 的 `module_data_reserve[32768]`。
- `tools/check_module_link.py --base-elf "$BASE_ELF"` 是无硬件链接检查，会生成带 `NOT-DEPLOYABLE` 标识的产物；其合成 text 地址不能烧录或上传。

模块构建器从底座真实编译数据库复用配置，用 `--just-symbols` 导入底座地址；FreeRTOS/libc/newlib/heap/startup 等进程级运行时只有一份。按 manifest 选择外设驱动并做命名空间处理，检查未解析导入、重复驻留定义、GP 相对重定位、未序列化可分配段、镜像边界和 ELF SHA。不要为让链接通过而跳过这些检查。

默认 `--builtin-service stm-uart` 只选 STM ROM + UART；SPI 必须显式选择 `--builtin-service stm-spi`，不是默认能力。自定义服务使用 `--module-manifest`，与 `--builtin-service` 互斥。每个服务构建使用独立输出目录，避免共享 `module-link/` 被覆盖。

### 4. 生命周期、并发和资源审查

入口 `module_entry(const rmb_host *)` 校验 ABI/size，初始化驱动、长期有效上下文及监听，然后返回。仅初始化阶段可注册监听；全部成功才开放业务 gate。失败初始化必须走恢复/整机复位，不热重入，不热卸载。

同一监听的回调串行，不同监听并发。最多四个监听；3337 管理、3335 日志由底座保留。共享 UART、staging、状态和驱动必须由服务串行化；不要在持锁状态等待需要同一锁的 worker。监听栈 `CONFIG_RMB_LISTENER_STACK_BYTES` 默认 **12288 字节/监听**，不是 FreeRTOS words；预算包含驱动、日志、libc 的完整调用链。观察 `stack_min_free_bytes`，不能只看业务函数栈帧。

连接是 opaque 借用 handle，不是 fd；回调返回即失效，不能自行 close 或留给后台任务。`read/write` 可短传输，0 read 为 EOF，`RMB_IO_AGAIN` 为暂不可用；`exact` 使用 `now_us()` 同一时钟上的绝对微秒期限，不可每次短传输重新计时。

`run` 的 context、任务参数和 DMA/ISR 缓冲区须保持有效直至使用结束或整机复位，不能借用已返回的栈；受监督作业最长 1800 秒，嵌套 run 拒绝，长操作维护 `progress()`。普通 SDK ISR/驱动仍遵循 SDK 规则：这不是新的 cache-off ISR 框架，不能在 ISR 中调用任务上下文的网络/租约 API。分区/footer 和 NVS 写入可能延迟普通 IRQ。

根租约由服务实际事件/空闲循环维护，活动控制/UART 角色各自持有任务所有的租约；健康空闲心跳不得代喂卡住的角色。超时后不能迟到 feed/close 续命。保留 IWDT/TWDT、MEMPROT+LOCK 与 panic reboot，不能靠关闭保护掩盖问题。

### 5. 单槽更新与故障诊断

更新顺序固定为：失效 footer → **整机复位停止旧任务/ISR** → 本次启动允许更新 → 擦写正文 → 校验大小、哈希、ABI、地址和底座绑定 → 最后提交 footer → 再复位加载。失效 footer 不会撤销本次启动的不可变写入准入闩锁；旧服务本启动运行过，就不能擦其正文。不是 A/B 回滚，也没有恶意模块隔离或数字签名。

故障启动隔离业务，管理仍用于读取/更新。先保存 `get-fault` 的完整报告，再按该报告准确 seq 执行 `ack-fault --fault-seq`；过期 ACK 应被拒绝。ACK 是确认诊断，不自动解除隔离。依据状态选择修复模块、`enter-update`、`restart`；`clear-fault` 不能越过更新启动/入口已尝试的 gate。`test-hang` 会故意触发看门狗，只能在明确授权的测试设备使用。

### 6. 提交与验证清单

1. C/C++ 遵循仓库 LLVM 风格、4 空格、100 列；Python 遵循 Ruff 的 100 列配置。使用仓库当前格式工具，不做顺带语义重构。
   格式工具独立安装，不是主机 CLI 的运行期依赖：

   ```sh
   python3 -m venv .venv-style
   .venv-style/bin/python -m pip install -r tools/format-requirements.txt
   python3 tools/format_code.py --check
   # 确认需要写回时：python3 tools/format_code.py
   ```

   固定版本为 clang-format 23.1.5、Ruff 0.17.4。工具优先使用 `.venv-style`；Windows 用 `.venv-style\Scripts\python.exe` 执行 pip。不要全局升级工具后把版本差异混入格式提交。

2. 注释解释所有权、绝对期限、状态转换、硬件约束和不可省略的检查；不要逐行复述代码。公共契约改动同步更新头文件、测试与手册。
3. 运行原生套件、safe/enabled 交叉构建和模块链接检查；服务 manifest 改动还运行 hello 的 manifest/原生测试。以 CMake 注册与实际测试输出为准，不在文档写死新的通过数量。
4. 故障修复要添加反例：错误认证、REPLAY 原字节、短 I/O、绝对期限、过期 handle、监听并发、初始化失败、超时隔离及过期 ACK。
5. 检查 diff 不含令牌、Wi-Fi 配置、私有 ELF/bin/map/日志。哈希绑定不意味着产物可公开。
6. 记录命令、配置类别、产物哈希和证据边界；未经授权不连接设备、不烧录、不执行破坏性 STM 操作。

原生通过只证明模拟路径，交叉构建只证明编译/链接。既有证据见 [测试](TESTING.md) 与 [ABI4 实机记录](validation/abi4-hardware-20261108.md)。历史 SPI 600 次结果属于当时可选诊断服务，不是默认 UART 服务的能力或本次新验收。

---

## English

### 1. Scope and reading order

ABI4 separates a generic resident, independently linked services, and target STM32 applications. ESP-IDF is the only implemented deployment backend; the SDK baseline is ESP-IDF 5.5.5 and the current bench combination is ESP32-C3 + STM32F103C8. F407ZG geometry in source does not establish hardware acceptance.

Read [User guide](USER_GUIDE.md), [Service SDK contract](SERVICE_AUTHORING.md), [Default STM service](DEFAULT_STM_FIRMWARE.md), [Resident boundary](BASE_PLATFORM_BOUNDARY.md), and [Watchdog recovery](WATCHDOG_RECOVERY.md). Before ABI or backend changes, read [ABI4](ABI4_REFACTOR.md) and [Backend porting](BACKEND_PORTING.md).

| Location | Responsibility |
|---|---|
| `include/rmb/service.h` | Generic service ABI; no STM/ESP types |
| `include/rmb/update.h`, `core/update.c` | SDK-independent single-slot update and admission |
| `include/rmb/supervisor.h`, `core/supervisor.c` | Portable supervision policy |
| `esp32_rom/main/resident.c`, `supervisor.inc`, `service_watchdog.inc` | Management protocol, logs, supervision and fault retention (platform-free) |
| `esp32_rom/main/generic_network.c` | Authentication, borrowed handles, listener workers, init gate (platform-free) |
| `include/rmb/port.h` | Platform contract: 46 functions (time, log, reset, critical section, tasks, sync, watchdog, retained RAM, slot, mapping, identity, network) |
| `esp32_rom/main/platform/` | The only platform implementation directory: `esp_idf_port.c`, `net_esp_idf.c` (Wi-Fi/NVS/discovery), `entry_esp_idf.c` |
| `esp32_rom/main/module_runtime.c`, `module_abi.h` | Admission, exact ELF binding and the 32 KiB data window; storage/mapping go through the port (platform-free) |
| `esp32_rom/module/entry.c`, `business.c` | STM service entry and business; never move ROM operations into the resident |
| `rom/` | Portable ROM protocol, I/O contracts, geometry and native simulation |
| `esp32_rom/module/platform/` | Service-owned UART/GPIO and optional SPI |
| `examples/services/hello/` | Independent A/B service without ROM or peripheral drivers |
| `backends/`, `boards/`, `schemas/`, `tools/rmb.py` | Registry validation and builds, not automatic flashing |
| `tools/module_ctl.py`, `romctl.py`, `wifidebug.py` | Resident management, target ROM, logs/application UART |

### 2.5 Platform abstraction, dependency metrics and the board registry

- **Boundary**: the platform-free half of the resident depends only on the 46 functions in `include/rmb/port.h`; every platform implementation lives in `esp32_rom/main/platform/`. `esp32_rom/tests/test_port_boundary.py` enforces both directions: no platform identifier may appear in a portable file, and every contract function must have an implementation. Run it before touching the resident.
- **Service side**: a service receives only the `rmb_host` table (`net`, `now_us`, `progress`, `run`, `watchdog_*`) and imports **no `rmb_*` base symbols**; it shares the IDF/RTOS/libc runtime, so any resident change (including compile-time settings such as the listener stack size) requires rebuilding every service.
- **Metrics**: `python3 tools/analyze_dependencies.py` prints platform-reference density per area; adding `--proof <service>/module-link/proof.json` classifies the symbols a service really imports. Measured today: zero base-private API symbols, and only 443 lines of platform code in the resident, all in one directory.
- **Boards are data**: `boards/*/board.json` describes target, device and pins; the backend declares `supported_targets`, so a new board is a JSON file. Safety comes from "the descriptor must equal the compiled image" — `verify_board_wiring()` compares every `CONFIG_ROM_SERVICE_*` pin after configure and fails on mismatch.
- See [Resident platform porting](RESIDENT_PLATFORM.md) and [Dependency and platform coupling](DEPENDENCY_ANALYSIS.md).

### 2. Reproducible environment

Native development requires a C11 compiler, CMake, Ninja and Python 3. Dependency bootstrap may need network access; never replace unavailable dependencies with invented output.

```sh
python3 tools/native_deps.py
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
python3 tools/rmb.py backends
python3 tools/rmb.py boards
```

Install ESP-IDF 5.5.5 and its ESP32-C3 RISC-V toolchain, activate the official environment and check `idf.py --version`. Set `IDF_PATH` and toolchain `PATH`. Module prefix precedence is `--tool-prefix`, `RISCV_TOOL_PREFIX`, `CROSS_COMPILE`, then `riscv32-esp-elf-`. Scripts do not activate or install the SDK.

```sh
python3 tools/rmb.py configure --profile safe
python3 tools/rmb.py build --profile safe
python3 tools/rmb.py configure --profile compile-enabled
python3 tools/rmb.py build --profile compile-enabled
```

Compile-time configuration has one tracked source, `config/build.defaults`, referenced by the board registry. The driver appends generated layers in increasing priority: profile (safe disables `CONFIG_RMB_ENABLE`) → `--credentials-file` (the `wifi.pw` positional shape or `key=value`; a missing token may come from `RMB_TOKEN`) → `--user-config` (final override). The credential layer is written mode 0600 inside the build directory, never into the tree, and a successful build prints only a redacted summary. Keep profile directories separate. The driver validates the fixed registry board, SDK version, generated enable state, IWDT/TWDT panic and reboot-on-panic, and fails an enabled build without a real SSID/token. Existing sdkconfig may retain old values after a config change: configure a new directory and inspect it. See [deployment](USER_GUIDE.md) for the private flow.

### 3. Service versus resident changes

For service-only changes retain the deployed final resident ELF, compile database, generated configuration and SDK archives. Obtain actual addresses from resident `status`, then rebuild/install the service. Any resident, stack, SDK or layout change requires a new resident deployment and rebuilding **every** service against its exact final ELF. Matching ABI numbers are insufficient.

Never copy historical addresses or ELF hashes. Text is the actual mapped module body address; data must match `module_data_reserve[32768]` in that ELF. `tools/check_module_link.py --base-elf "$BASE_ELF"` uses a synthetic text mapping and produces explicitly **NOT-DEPLOYABLE** artifacts.

The builder reuses real compile commands, imports resident symbols with `--just-symbols`, and retains a single FreeRTOS/libc/newlib/heap/startup runtime. It selects/namespaces peripheral drivers and audits undefined imports, duplicate resident definitions, GP-relative relocations, unserialized allocatable sections, bounds and ELF SHA. Do not bypass audits to make a build pass.

Default `--builtin-service stm-uart` selects ROM/UART only. Explicit `--builtin-service stm-spi` enables optional diagnostics. Custom `--module-manifest` and builtin selection are mutually exclusive. Use separate artifact directories because each build writes `module-link/`.

### 4. Lifecycle and resource review

Entry validates ABI/size, initializes drivers and persistent contexts, registers listeners, and returns. Only initialization may register listeners; admission opens after all initialization succeeds. Failure requires recovery/reset, not hot reentry or unloading.

Callbacks serialize within a listener but different listeners run concurrently. At most four listeners are available; 3337 management and 3335 logs are reserved. Services serialize shared UART, staging, state and driver ownership. Avoid lock/worker deadlocks. `CONFIG_RMB_LISTENER_STACK_BYTES` defaults to **12288 bytes per listener**, not FreeRTOS words. Include SDK/logging/libc nesting and inspect `stack_min_free_bytes`.

Connections are borrowed opaque handles, not fds; they expire when the callback returns and must not be closed or retained. Read/write may be short, read zero means EOF, and `RMB_IO_AGAIN` means retry later. `exact` uses an absolute microsecond deadline on the `now_us()` clock; do not extend it after partial I/O.

Job contexts, task arguments and DMA/ISR buffers remain valid until completion or full reset; never leave a background task holding a returned stack address. `run` allows at most 1800 seconds, rejects nesting, and long jobs maintain progress. SDK ISR rules still apply: no new cache-off ISR framework exists and network/watchdog lease APIs are task-context only. Footer/NVS writes can delay ordinary IRQs.

Maintain a root lease from the actual service event/idle loop and separate task-owned leases for active critical roles. An idle heartbeat must not mask a stuck callback. Late feed/close cannot revive an expired lease. Retain IWDT/TWDT, MEMPROT+LOCK and panic reboot.

### 5. Update and recovery invariants

Invalidate footer → **reset the entire ESP to stop old tasks/ISRs** → boot into immutable update permission → erase/write body → validate size/hash/ABI/addresses/base binding → commit footer last → reset to load. Footer invalidation cannot change the current boot's write-admission latch. This is neither A/B rollback, malicious-module isolation nor signing.

Fault boots quarantine business while retaining management. Save `get-fault`, then ACK its exact sequence with `ack-fault --fault-seq`; stale ACKs fail. ACK preserves the separation between diagnostic acknowledgment and recovery. Repair/update/restart according to status; `clear-fault` cannot bypass update-boot or already-attempted-entry gates. `test-hang` intentionally triggers watchdog recovery and requires explicit test-device authorization.

### 6. Change checklist and evidence

Use repository formatting: LLVM-based C/C++, four spaces/100 columns; Ruff Python, 100 columns. Install the pinned style-only dependencies (clang-format 23.1.5 and Ruff 0.17.4) separately from stdlib host CLI runtime:

```sh
python3 -m venv .venv-style
.venv-style/bin/python -m pip install -r tools/format-requirements.txt
python3 tools/format_code.py --check
# Write changes only when intended: python3 tools/format_code.py
```

The wrapper prefers `.venv-style`; Windows uses `.venv-style\Scripts\python.exe` for pip. Do not mix tool-version churn into formatting changes. Explain ownership, deadlines, transitions and hardware constraints rather than paraphrasing statements. Keep public headers, tests and manuals aligned.

Run native tests, safe/enabled cross-builds and real module link checks; run hello manifest/native tests when relevant. Add regression cases for authentication, byte-exact replay, short I/O, absolute deadlines, stale handles/ACKs, concurrency, init failure and quarantine. Review diffs for tokens, private Wi-Fi configuration, ELF/bin/map and logs; binding does not make artifacts publishable. Record commands, configuration class, artifact hashes and limits. Do not connect/flash without authorization.

Native tests prove simulated paths; cross-builds prove compilation/linking. See [Testing](TESTING.md) and [existing ABI4 hardware evidence](validation/abi4-hardware-20261108.md). Historical 600-transaction SPI results belong to optional diagnostics, not default UART capability or new validation.
