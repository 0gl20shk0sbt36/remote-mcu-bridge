# 验证 / Verification

## 中文

### ABI4 本地结果（2026-11-08）

- 完整 CTest **28/28 通过**，包含 ROM E2E、生产通用网络、通用监督/更新、服务看门狗、故障客户端及 hello A/B；未因迁移而跳过已注册的失败项。
- 真实 ESP-IDF 5.5.5 `safe` 与 `compile-enabled` 底座构建通过；启用配置要求 IWDT、TWDT panic 和 panic 后重启，防止只报警不恢复。
- STM 服务、hello A/B 对 ABI4 底座完成真实 RISC-V 编译/链接；均无未解析符号、无重复底座定义、无隐藏底座网络函数导入。配置中的非空网络名称/令牌字节未进入这些模块。
- 早期合成取指地址产物仍 **NOT DEPLOYABLE**；其原始证据标为 pre-hardware baseline。随后根据真实设备地址另行构建并部署 ABI4，独立记录如下。
- 看门狗原生测试直接编译生产实现，覆盖分角色超时、截止点拒绝续命、根角色不可关闭、陈旧 ID、大量槽复用、CRC、跨模拟复位保留、精确序号确认、新硬件事件替换旧待确认事件，以及更新/隔离启动不产生重启循环。它不等于实际芯片 WDT 和 RTC 验收。
- hello A/B 的实际 C/ASan/UBSan 测试与 13 项 manifest/链接证明测试通过。管理客户端另有 6 项实际 localhost TCP/参数测试。

### ABI4 独立实机验收（2026-11-08）

- 用户授权串口刷入新底座，整片校验通过；全部 Wi-Fi 业务更新结束后，对底座启动/分区区间及完整 factory 区间再次实际校验，摘要与刷入镜像一致。
- hello A→B 的行为、计数重置、错误令牌拒绝、正常启动拒绝覆写、半包上传+重启恢复、错误底座绑定拒绝均通过。
- 五种真实故障：停止喂狗、通用作业迟到、入口失败、入口卡死/TWDT、关中断/IWDT；均恢复独立管理、保留 RTC 报告，精确序号确认不解除隔离。硬卡死原因/年龄未知时不伪造。
- 修复实机发现的 **6144 字节 listener 栈溢出**：默认改为可配置 12288 字节。代表负载最小剩余量为控制口 **5940 B**、UART 口 **7184 B**。修复后再次完整 28/28 CTest、safe/enabled 构建和硬件回归通过。
- STM 服务 SRAM 下载、全读回校验、Go、同一 Linux PTY 跨下载暂停/恢复通过。UART 长连接保持时 ESP 业务槽更新/重启，然后 Wi-Fi 重装 STM 服务，目标 SRAM 应用仍能回复 PONG。
- SPI 在恢复服务后及活动 UART 更新/重装后分别执行三档速率，各 100 次：累计 **600×64 字节双向事务**，两端全部校验通过，零错字节。历史 ABI3 全 FF 唯一根因仍未坐实。
- 本轮**没有改写 STM Flash**；没有实测擦写中突然断电，RTC 不保证断电保留，未验证其他板或 cache-off 自定义 ISR。GitHub 发布与远程 CI 仍暂停。
- [独立实机报告](validation/abi4-hardware-20261108.md) / [脱敏结构化证据](validation/abi4-hardware-20261108.json)。生产凭据、镜像和原始串口/Wi-Fi 日志保持私有。

### 代码整理与默认服务分离（2026-11-08，本地）

- 统一格式：LLVM 风格 C/C++（4 空格、100 列）、Ruff Python（100 列），由 `tools/format_code.py` 只处理自有源码，不碰 SDK/依赖/构建产物；工具版本在 `tools/format-requirements.txt` 固定，安装在隔离虚拟环境。
- 格式变化后，原先依赖逐字空白的源码契约测试改为分隔符容忍的锚点（`at()`）与 token 归一化比较（`flat()`），安全性质未放宽；完整 CTest **29/29** 通过（新增 CLI 令牌文件测试）。
- 删除两份无构建/测试依赖的 ABI3 遗留 `esp32_rom/main/uart_{priority,framed}.inc`；现役实现只有 `esp32_rom/module/` 下的副本。
- 默认内置服务改为 **`stm-uart`（无 SPI）**：默认构建只含 UART 驱动、无 SPI handler/诊断/GP-SPI 驱动；SPI 仅在显式 `--builtin-service stm-spi` 时编入。与 `--module-manifest` 互斥。
- 对同一启用版底座真实交叉链接：`stm-uart` 镜像 52,820 B、5 个源文件、链接库不含 SPI、镜像内无 `spi-test`/`spi-pad-sample`/`pad_scope` 等标记；`stm-spi` 85,748 B、6 个源文件。两者均无未解析符号，合成取指地址下 **NOT DEPLOYABLE**。
- 本轮未烧录、未改 STM Flash、未做新实机验证；默认服务是否可用仍以既有 ABI4 实机记录为准，SPI 历史 600 次结果为可选服务记录。

### 编译期配置收敛与凭据注入（2026-11-08，本地）

- 原先分散在 `esp32_rom/sdkconfig.defaults`、`config/ci-{enabled,disabled}.defaults` 与驱动内联字符串的编译期设置，全部合并为受版本控制的 `config/build.defaults`；板卡注册表只引用这一个文件，三份旧文件已删除。
- 构建驱动按优先级追加生成层：profile（safe 关闭 `CONFIG_RMB_ENABLE`）→ `--credentials-file` → `--user-config`；凭据层写入构建目录内 `rmb-generated.defaults` 并设 0600，不进入源码树。
- 凭据文件支持 `wifi.pw` 两行位置式（SSID、密码，可选第三行令牌）与 `key=value`；文件无令牌时可用 `RMB_TOKEN` 补足；缺真实 SSID 或令牌少于 16 字节时启用档**直接失败**，不产出可部署镜像。
- 真实执行：`safe` 与 `compile-enabled` 均重新完整构建通过；`compile-enabled` 分别用合成凭据与真实 `wifi.pw` 构建成功，生成 `sdkconfig` 中占位符被替换、STM 型号/引脚为 F103C8 与 3/4/5/6/-1、监听栈 12288。构建输出只有脱敏摘要，不含 SSID/密码/令牌原文；含真实凭据的构建目录验证后已删除。
- 新增 `tests/test_build_config.py`（15 项）覆盖单文件契约、凭据解析/拒绝、覆盖优先级、脱敏摘要与 CLI 参数面；完整 CTest **30/30** 通过。
- 深入扫描：受版本控制文件内无 `/home/...`、`C:\`、`/Users/...` 绝对路径，无 `wifi.pw` 引用，无个人 IP/用户名/MAC；凭据路径只以参数形式出现。

### 底座平台抽象与注册表化板卡（2026-11-08，本地）

- 底座逻辑改为平台无关：`resident.c`、`generic_network.c`、`module_runtime.c`、`streams.inc`、`supervisor.inc`、`service_watchdog.inc` 全部通过 `include/rmb/port.h` 的 **46 个端口函数**访问时间/日志/复位/临界区/任务/同步/看门狗/保留内存/业务槽/可执行映射/基座身份/网络；ESP-IDF 实现集中在 `esp32_rom/main/platform/`。
- 新增 `esp32_rom/tests/test_port_boundary.py` 并由 CTest 执行：可移植文件出现任何 `esp_*`/FreeRTOS 标识符、或契约函数未被平台实现，测试直接失败。这是"移植只需实现最小集合"的机器保证。
- 复位原因保持 ESP 数值（1/3/4/5/6/7），因为该值写入故障日志并按原样上报；`port.h` 注明勿重编号。
- 板卡不再硬编码：后端声明 `supported_targets`，驱动按注册表接受板卡；新增 `verify_board_wiring()` 把 `board.json` 的引脚与目标型号和生成的 `sdkconfig` 逐项比对，不一致即失败。**新板卡=新增 JSON**，而错接线不会被静默烧录。
- 真实执行：完整 CTest **31/31** 通过（含新增 port_boundary、build_config 20 项、registry 11 项）；ESP-IDF `safe` 与 `compile-enabled`（带合成凭据）均重新完整构建通过；默认 `stm-uart` 与可选 `stm-spi` 对新底座的真实交叉链接通过（合成取指地址，NOT DEPLOYABLE）。
- 抽取型原生夹具迁移完成：9 个夹具改用共享端口测试桩（`port_native.{h,c}`、`port_mock_platform.h`、`port_compat.h`），断言未放宽（计数、暂停/恢复 epoch、拒绝事务、截止期边界、故障日志语义均保留）。
- 模块构建器改为在编译数据库中识别 `resident.c`（兼容旧的 `service.c` 名）。
- 端口重构后的固件已完成重新烧录与实机验收（见本文上方"端口层固件实机验收"）；本轮仍未擦写 STM Flash、未做物理断电与 SPI 服务验证。

### 端口层固件实机验收（2026-11-08）

- 重构后固件重新烧录并通过整片校验：底座 ELF `9823c74c…`、合并镜像 `5a5b79b5…`。
- 默认 `stm-uart` 服务（52,772 B）按设备上报地址构建，安装后 `module_ready=true`；错误取指地址的镜像被设备拒绝，证明准入校验有效。
- STM：`status`/`probe`（ACK 0x79、Get=0x10、GetVersion=0x22、无擦写）、10,620 B SRAM 下载+回读+Go、raw 与 framed 两种会话的 `PONG SRAM`。
- 监督：`module-test-hang` 触发 TWDT 复位（`reset_reason=6`），重连读到 seq=1/reason=2 故障，错误序号被拒、正确确认通过、`clear-fault` 恢复；恢复后 STM SRAM 重载并再次 PONG。
- 未验证：STM Flash 擦写（本轮仅 SRAM）、物理断电、逻辑分析仪时序、其它板卡、SPI 服务。见 [实机记录](validation/port-layer-20261108.md)。

### 历史 ABI3 本地执行结果（2026-11-07）

- 整理后的源码正本：**22/22 CTest 通过**，包含原工程适用测试、注册表、客户端、发现、probe、SPI 原始数据/采样接口及最小服务 A/B 的原生检查。最后完整运行在新增深度诊断与服务清单支持后执行。
- 更新核心的严格 GCC / ASan / UBSan 检查通过：270 个场景、225765 次断言、179 次注入故障；覆盖回读错误、部分擦写、分片、对齐、不可变启动许可和提交失败。模拟摘要只测试回调/范围/比较，不冒充密码学实现验证。
- 修复存储回调返回 `BRIDGE_E_STATE` 时被误当作标记内容不匹配的问题；现在回调状态和读取结果分开，任何回读错误都不能授予更新或执行许可。
- 客户端完整套件 17/17 通过；正常关闭及早期握手中断两个 Ctrl-C 场景各重复 50 次，共 100/100 通过。
- 从已有归档恢复并校验 ESP-IDF **v5.5.5** 与 RISC-V GCC **esp-14.2.0_20260121 / 14.2.0**，Python 依赖安装在独立虚拟环境，没有全局安装。
- 通过统一后端入口完成 `safe` 和 `compile-enabled` 两个配置的真实本地 ESP32-C3 编译；生成配置的启用状态和固定板级引脚均通过校验。
- 业务模块对启用版底座 ELF 的真实独立链接、镜像头和精确底座哈希绑定检查通过；无未解析符号、无重复底座定义。链接检查使用合成取指地址，产物明确标记 **NOT DEPLOYABLE**，不是可烧录或已运行的业务模块。
- 私有工作树的生产配置底座也已重新构建。生产凭据、固件及旧开发历史不进入这份干净源码仓库。

**后续用户重新连线并授权后，已完成新底座实机回归**：串口刷写校验、绑定新底座的模块更新、正常启动拒绝覆写、半包上传后重启恢复、真实模块入口死循环的看门狗复位及 Wi-Fi 修复、STM32 Flash/SRAM 编程、UART PONG/心跳全部通过。SPI 100 kHz / 500 kHz / 1 MHz 各 100 次双向 64 字节校验零错。曾出现 MISO 全 FF，重插 PA6↔GPIO0 后不改软件、不复位即恢复。见 [实机记录](validation/hardware-20261107.json)。**真实写入中途突然断电仍未测试，GitHub 发布与远程 CI 继续暂停。**

### 原生复现

```sh
python3 tools/native_deps.py
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

`native_deps.py` 只获取固定提交 `b2890c8d76bbb64e710585ebc0a917196b9c67e7` 的 cJSON，不下载完整 SDK。已有依赖可以通过 `RMB_CJSON_PATH` 或 `IDF_PATH/components/json/cJSON` 使用；`--cache` 接受本地 Git 对象库。

激活 ESP-IDF 5.5.5 环境后，可复现无需硬件的交叉编译：

```sh
python tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 --profile safe
python tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 --profile compile-enabled
python tools/check_module_link.py --base-elf out/esp-idf/esp32c3-stm32f103/compile-enabled/stm32_rom_wifi.elf
```

`safe` 关闭服务，`compile-enabled` 使用公开占位凭据，只用于编译；不要烧录占位配置。首次本地构建的缺失 `ESP_ROM_ELF_DIR` 提示只影响 GDB 初始化文件生成，命令退出成功，不是编译或模块链接失败；本地源码正本最后构建已补全该变量，两个配置均无此错误提示。

### 测试边界与竞争修复

原生测试使用本地回环 socket 和模拟 I/O，不验证真实 Flash 时序、接线、无线链路或硬件看门狗。`apps_f103` 依赖未随本仓提供的旧 CubeMX 工程，明确排除；ROM E2E 的可选真实 CubeMX 镜像子检查未执行，合成镜像的 TCP/UDP/C 核心路径执行。

Ctrl-C 的旧测试在服务端发出 ACK 后立即中断客户端，不能保证客户端已读 ACK，因而可能正常地产生 TCP reset。修复后的正常关闭测试等待真实 CLI 经 stdin 回传标记，证明 ACK 已被处理，再发送 SIGINT，仍严格要求 EOF 和退出码 130。独立早期握手中断测试只在该明确场景接受 EOF 或 `ConnectionResetError`，不放宽其他错误。最后完整 CTest 同样通过。

板卡 JSON 校验唯一受支持板卡的固定接线，不会自动生成新板实现。网络、任务/队列、XIP 加载与实际看门狗服务仍属于 ESP 适配；不能把通用接口声明说成已经接入了所有运行路径。其他后端尚未实现。

## English

### ABI4 local results (2026-11-08)

- Full **28/28 CTest** passed, including ROM E2E, production generic networking/supervision, watchdog, management client and hello services. No failing registered fixture was skipped to make migration pass.
- Real ESP-IDF safe and enabled builds passed. Enabled configuration requires IWDT, TWDT panic and reboot-on-panic.
- STM, hello A and hello B really cross-linked against the ABI4 resident, without undefined symbols, duplicate resident definitions or hidden resident network-helper imports. Nonempty configured network-name/token bytes were absent from modules.
- Earlier synthetic-address artifacts remain **NOT DEPLOYABLE** and are labeled pre-hardware baseline. Separate modules were subsequently rebuilt for real reserved addresses and physically deployed as recorded below.
- Production watchdog C passed native ASan/UBSan tests for independent roles, exact deadlines, stale IDs, root protection, CRC/retention, sequence ACK and new hardware incidents. Native simulated resets do not validate real chip WDT/RTC behavior. Hello variants and 13 manifest/link-proof tests passed; six client tests exercise actual localhost TCP and argument validation.

### Independent ABI4 hardware validation (2026-11-08)

- Authorized serial resident deployment and whole-image verification passed. After all Wi-Fi module replacements, serial digest verification again matched the boot/partition range and full factory partition against the deployed resident image.
- Hello A→B behavior/counter reset, invalid-token rejection, normal-boot overwrite refusal, interrupted-upload reboot recovery and mismatched-base binding rejection passed.
- Five physical faults recovered to management/quarantine with RTC reports: missing service feed, late generic job, entry failure, entry hang/TWDT and interrupts-disabled stall/IWDT. Sequence ACK is exact and does not release quarantine; unknown hard-stall age/offender remains unknown.
- Serial evidence identified a 6144-byte listener stack overflow. The configurable default is now 12288 bytes; measured minimum headroom was 5940 B on control and 7184 B on UART. All 28 CTest entries, both ESP profiles and hardware regressions passed again after the fix.
- STM SRAM programming/full readback/Go, one Linux PTY across preempting SRAM download, and resident reset/module replacement while UART was active passed. The STM SRAM application still returned PONG after service reinstall.
- Two rounds of 100 transactions at each of 100 kHz, 500 kHz and 1 MHz passed: 600 bidirectional 64-byte transactions, both receivers verified, zero mismatched bytes. This does not establish the cause of the historical ABI3 all-FF incident.
- STM Flash was not rewritten in this session. Physical mid-write power loss, other boards, complete-power-loss RTC retention and custom cache-off ISRs remain untested. Publication and remote CI remain paused.
- See the [hardware report](validation/abi4-hardware-20261108.md) and [sanitized JSON evidence](validation/abi4-hardware-20261108.json). Production credentials/images and raw serial/Wi-Fi logs remain private.

### Code organisation and default-service split (2026-11-08, local)

- One formatter contract: LLVM-style C/C++ (four spaces, 100 columns) and Ruff Python (100 columns), applied only to owned sources by `tools/format_code.py`, never SDK/dependency/build output. Tool versions are pinned in `tools/format-requirements.txt` and installed in an isolated virtual environment.
- Source-contract fixtures that previously depended on exact whitespace now use delimiter-tolerant anchors (`at()`) and token-normalised comparison (`flat()`); no safety property was relaxed. Full CTest: **29/29** (new CLI token-file suite included).
- Removed two ABI3 leftovers with no build or test dependency: `esp32_rom/main/uart_{priority,framed}.inc`. The live implementations are the `esp32_rom/module/` copies.
- The built-in default is now **`stm-uart` (no SPI)**: only the UART driver is compiled, with no SPI handlers, diagnostics or GP-SPI driver. SPI requires an explicit `--builtin-service stm-spi` and is mutually exclusive with `--module-manifest`.
- Real cross-links against the same enabled resident: `stm-uart` is 52,820 B from five sources, links no SPI archive and contains no `spi-test`/`spi-pad-sample`/`pad_scope` markers; `stm-spi` is 85,748 B from six sources. Both have no undefined symbols and are **NOT DEPLOYABLE** at the synthetic text address.
- No flashing, no STM Flash write and no new hardware claim in this round; default-service behaviour still rests on the existing ABI4 hardware record, and the historical 600-transfer SPI measurement belongs to the optional service.

### Compile-time configuration consolidation and credential injection (2026-11-08, local)

- Settings previously spread across `esp32_rom/sdkconfig.defaults`, `config/ci-{enabled,disabled}.defaults` and inline driver strings now live in the single tracked file `config/build.defaults`; the board registry references only that file and the three old files are gone.
- The driver appends generated layers in increasing priority: profile (safe disables `CONFIG_RMB_ENABLE`) → `--credentials-file` → `--user-config`. The credential layer becomes `rmb-generated.defaults` inside the build directory (mode 0600) and never enters the source tree.
- Credential files accept the two-line `wifi.pw` shape (SSID, password, optional third line token) or `key=value`; a missing token may come from `RMB_TOKEN`. An enabled build without a real SSID or with a token under 16 bytes fails instead of producing a deployable image.
- Executed for real: both `safe` and `compile-enabled` rebuilt end to end; `compile-enabled` also built from a synthetic file and from the real `wifi.pw`. The generated sdkconfig had no placeholders, selected F103C8 with pins 3/4/5/6/-1 and a 12288-byte listener stack. Output was a redacted summary only; the build directory holding real credentials was deleted after verification.
- New `tests/test_build_config.py` (15 checks) covers the single-file contract, credential parsing/rejection, override priority, redaction and the CLI surface. Full CTest: **30/30**.
- Deep scan: no `/home/...`, `C:\` or `/Users/...` absolute paths, no `wifi.pw` reference and no personal IP/user/MAC in tracked files; credential paths appear only as arguments.

### Resident platform abstraction and registry-driven boards (2026-11-08, local)

- The resident logic is platform-free: `resident.c`, `generic_network.c`, `module_runtime.c`, `streams.inc`, `supervisor.inc` and `service_watchdog.inc` reach the platform only through the **46 functions** of `include/rmb/port.h` (time, log, reset, critical section, tasks, synchronisation, watchdog, retained RAM, business slot, executable mapping, base identity, network); the ESP-IDF implementation lives in `esp32_rom/main/platform/`.
- New `esp32_rom/tests/test_port_boundary.py` (run by CTest) fails if a portable file names any `esp_*`/FreeRTOS identifier or if a contract function is not implemented by the platform. That is the mechanical guarantee behind "a port implements a minimal set".
- Reset causes keep the ESP numeric values (1/3/4/5/6/7) because they are persisted in the fault journal and reported verbatim; `port.h` documents that they must not be renumbered.
- Boards are no longer hardcoded: the backend declares `supported_targets` and the driver accepts any registered board, while `verify_board_wiring()` compares every `board.json` pin and the target device against the generated `sdkconfig` and fails on mismatch. **A new board is JSON**, and wrong wiring cannot be flashed silently.
- Executed for real: full CTest **31/31** (including the new port_boundary, 20 build_config checks and 11 registry checks); ESP-IDF `safe` and `compile-enabled` (with synthetic credentials) rebuilt end to end; the default `stm-uart` and optional `stm-spi` services cross-linked against the new base (synthetic text address, NOT DEPLOYABLE).
- The nine extraction fixtures were migrated onto shared port test doubles (`port_native.{h,c}`, `port_mock_platform.h`, `port_compat.h`) with assertions unchanged in strength (counters, pause/resume epochs, rejected transactions, deadline boundaries and fault-journal semantics all preserved).
- The module builder now recognises `resident.c` in the compile database, falling back to the old `service.c` name.
- No flashing, no STM Flash writes and no new hardware claim this round; the earlier ABI4 hardware evidence applies to the previous image, and the ported firmware still requires its own hardware acceptance.

### Port-layer firmware hardware acceptance (2026-11-08)

- The refactored firmware was re-flashed and the whole image verified: resident ELF `9823c74c…`, merged image `5a5b79b5…`.
- The default `stm-uart` service (52,772 B) was built against the device-reported addresses and installed to `module_ready=true`; an image built for a wrong text address was rejected by the device, proving the admission check.
- STM: `status`/`probe` (ACK 0x79, Get=0x10, GetVersion=0x22, no erase/write), a 10,620-byte SRAM download with readback and Go, and `PONG SRAM` over both raw and framed sessions.
- Supervision: `module-test-hang` produced a TWDT reset (`reset_reason=6`); after reconnect the device reported seq=1/reason=2, a wrong sequence was rejected, the exact one accepted, and `clear-fault` restored `module_ready=true`; the SRAM application was then reloaded and answered again.
- Not covered: STM Flash erase/write (SRAM only), physical power loss, logic-analyzer timing, other boards, the SPI service. See the [hardware record](validation/port-layer-20261108.md).

### Historical ABI3 local results (2026-11-07)

- The clean source tree passed **22/22 CTest entries after the deep SPI diagnostics and service-manifest extension**, including applicable original host fixtures, registry/client/discovery/probe checks, SPI report/pad request tests and native hello A/B checks.
- Strict GCC / ASan / UBSan checks passed for the update core: 270 scenarios, 225765 assertions and 179 injected failures. The mock digest tests callback ranges and comparisons, not cryptographic correctness.
- Fixed a fail-closed bug where a storage callback returning `BRIDGE_E_STATE` was confused with an observed footer mismatch. Callback failures now deny both update and execution permission.
- All 17 client tests passed; the normal-close and early-handshake Ctrl-C cases each passed 50 repetitions (100/100 total).
- Restored and checksum-verified ESP-IDF **v5.5.5** and RISC-V GCC **esp-14.2.0_20260121 / 14.2.0**. Python requirements use an isolated virtual environment, not global installation.
- Both `safe` and `compile-enabled` ESP32-C3 builds passed locally through the common backend CLI, including generated configuration and fixed-wiring validation.
- The business module completed a real independent link against the enabled resident ELF; header and exact base-hash binding validation passed, with no undefined symbols or duplicate resident definitions. The check uses a synthetic instruction address: its outputs are explicitly **NOT DEPLOYABLE**, not runtime validation.
- The private production-configured resident was also rebuilt. Production credentials, firmware and old development history are excluded from this clean source repository.

**After the user rewired the boards and authorized access, hardware regression passed**: verified serial resident flashing, exact-base-bound module updates, normal-boot overwrite rejection, interrupted-upload reboot recovery, a genuinely looping module entry followed by watchdog reset and Wi-Fi repair, STM32 Flash/SRAM programming, and UART PONG/heartbeat. SPI passed 100 bidirectional 64-byte transfers at each of 100 kHz, 500 kHz and 1 MHz. An all-FF MISO fault disappeared after reseating PA6↔GPIO0 without further software changes or resets. See [hardware evidence](validation/hardware-20261107.json). **Physical mid-write power loss remains untested; publication and remote CI remain paused.**

### Reproduction and limits

Use the native and ESP commands in the Chinese section above. `native_deps.py` retrieves only pinned cJSON; `RMB_CJSON_PATH`, `IDF_PATH` or its `--cache` option support existing dependencies. Activate ESP-IDF 5.5.5 before cross-compiling. The safe profile disables service; compile-enabled uses public placeholder credentials and must not be flashed. An unset `ESP_ROM_ELF_DIR` caused nonfatal GDB-init generation messages in the first local builds, not compilation/link failure; the final builds from the canonical source directory exported it and emitted no such errors.

Native fixtures use loopback sockets and simulated I/O. They do not establish physical Flash timing, wiring, Wi-Fi or hardware-watchdog behavior. The unpublished CubeMX application and optional actual-image E2E subcheck are excluded; synthetic TCP/UDP/C-core paths ran.

The old Ctrl-C fixture signalled immediately after server ACK transmission, before proving client consumption; cancellation could legitimately produce a TCP reset. The normal-close fixture now waits for a real CLI stdin marker forwarded only after ACK validation, then requires EOF and exit code 130. A separate deliberately early cancellation accepts only EOF or `ConnectionResetError` in that explicit case. The final complete CTest run also passed.

Board JSON validates one fixed supported wiring and does not generate new implementations. Networking, tasks/queues, executable XIP mapping and actual watchdog services remain ESP adapter responsibilities; declared contracts are not fully integrated runtime paths. No second deployment backend is implemented.
