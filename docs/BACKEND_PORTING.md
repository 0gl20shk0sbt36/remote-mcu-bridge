# 后端移植指南 / Backend Porting

## 中文

### 1. 当前支持范围

只有 `backends/esp-idf/backend.json` 注册的 ESP-IDF 后端是实际部署实现，SDK 基线为 **ESP-IDF 5.5.5**，板卡入口为 `boards/esp32c3-stm32f103/board.json`。主机模拟是测试设施；其他平台模板不受支持。本指南是移植契约与检查清单，不是新平台支持声明，也不声明 CI 或硬件验收已经执行。

### 2. 先理解两种可移植算法

**桥接器自身模块更新：** `include/rmb/platform.h`、`include/rmb/update.h` 与 `core/update.c`。

- `bridge_update` 保存调用方拥有的易失状态，不分配堆内存；调用方必须串行化 API 调用。
- `bridge_update_init` 检查几何；`bridge_update_boot` 读取标记并锁存写许可。标记读取 I/O 失败不能被当成允许更新。
- `bridge_update_allowed` 和 `bridge_execution_allowed` 提供状态判断；后者不是镜像准入校验的替代品。
- `bridge_update_invalidate` 只擦除并回读标记区，不卸载正在执行的模块、不改变启动锁存、不改写镜像体或 RAM。
- `bridge_update_begin` 要求本次启动允许更新，先使标记失效再擦镜像体；`bridge_update_write` 接受连续偏移的碎片数据，由核心按写入单位缓冲。
- `bridge_update_finish` 补齐末尾写单位，执行镜像准入、对原始镜像长度的哈希比较及同步，最后写全零标记并回读。提交后本次启动不再接受另一次上传，必须复位。

**被连接 STM32 的 ROM 下载：** `rom/rom.h`、`rom/rom.c` 与 `rom/devices.c`。

- `rom_validate` / `rom_download` / `rom_download_traced` 负责镜像检查与协议下载；`rom_probe` 只读探测但会复位目标并留在 ROM；`rom_restart` 将 BOOT0 拉低并复位已编程目标。
- `rom_io` 提供 `tx`、`rx`、`boot`、`reset`、`sleep_ms`。TX/RX 返回实际字节数、零表示超时、负数表示错误，允许短 I/O；UART 由调用者配置为 8E1。
- `rom_source.read` 随机读取镜像，成功返回零；镜像在验证与下载期间必须不可变。
- `rom_device` 定义 Flash/RAM 范围、均匀页或非均匀扇区、向量对齐、ROM 设备 ID 与擦除命令优先级。设备 ID 是家族级检查，不足以证明具体 SKU 或实际容量。
- `rom_trace_fn` 是同步诊断回调，不得重入或改变 I/O。不要输出镜像内容或秘密。

这两种更新的存储目标不同：前者更新桥接器业务模块，后者操作连接的 STM32。不要将两者的容量、标记或复位语义混用。

### 3. 存储适配契约

实现 `bridge_storage_ops`，回调上下文 `ctx` 由调用方持有，所有回调返回 `bridge_error`，不可直接泄漏 SDK 错误码。

| 字段/回调 | 必须保证 |
|---|---|
| `size` | 整个单槽容量，包含末尾标记区；所有访问严格在边界内 |
| `erase_unit` / `write_unit` | 实际擦除单位和核心使用的编程单位；非零且兼容对齐，写单位不大于 `BRIDGE_MAX_WRITE_UNIT` |
| `read` | 读取逻辑存储值，报告失败；不得伪造成功 |
| `erase` | 仅擦请求范围；当前核心要求擦除后逻辑值为 `0xff` |
| `program` | 接受核心对齐的偏移/长度，处理设备实际约束；不得越界 |
| `sha256` | 对指定存储区间的原始字节计算真实 SHA-256；不含末尾填充 |
| `check_image` | 非执行式验证格式、准确底座绑定和期望镜像大小；不得执行入口 |
| `sync`（可选） | 同步映射/缓存并报告错误；不得执行入口 |

标记区必须占用独立实际擦除单元，不能与镜像体共享扇区。槽容量及标记长度须按擦除单位对齐，擦除单位可被写入单位整除。不要将 ESP 当前 4096 字节标记区复制为所有平台的常量。若介质逻辑擦除值不是 `0xff`，当前算法不能直接使用，需要明确的核心协议变更与测试。

加密 Flash 必须核对逻辑读取值、编程对齐、重复编程限制及全零标记的可实现性。不能仅因编译成功就声称支持加密存储。测试中的摘要替身只验证回调数据范围和比较路径，不是密码学实现验证。

### 4. 平台职责与当前 ESP 文件

- `bridge_lifecycle_ops` 声明 `monotonic_us`、`watchdog_feed` 和 `reset`。它不是完整调度器，也不证明当前服务已全部通过该表调用生命周期功能。
- `esp32_rom/main/` 的可移植部分（`resident.c`、`generic_network.c`、`module_runtime.c`、各 `.inc`）只经 `include/rmb/port.h` 访问平台；平台实现集中在 `esp32_rom/main/platform/`。判据是 `esp32_rom/tests/test_port_boundary.py`，不要凭感觉宣称"已可移植"。
- `esp32_rom/main/module_runtime.c` / `.h` 处理当前 ESP 模块的分区、映射、加载和更新衔接；`module_runtime_prepare` 是非执行预留，`module_runtime_init` 会执行入口，只能从监督工作路径调用，不能从上传提交路径调用。
- `esp32_rom/main/module_abi.h` 定义 `module_header`、`module_host`、`module_ops` 与镜像 ABI。`esp32_rom/module/build_module.py` 与准确底座构建绑定模块；二进制不保证跨 CPU、SDK、ABI 或底座构建复用。
- `esp32_rom/module/entry.c`、`business.c` 是业务模块；`module/logic/spi_diagnostic.c` 是诊断流程；`module/platform/esp_idf_io.c` / `.h` 与 `board_config.h` 才是其 ESP UART/GPIO/SPI 适配及板级配置。
- `esp32_rom/main/Kconfig.projbuild`、`esp32_rom/partitions.csv` 定义服务配置与存储布局。引脚与网络值不是通用核心 API。

新后端必须提供网络字节流的短读写、期限、断线、背压与资源上限，以及目标平台的存储、时钟、看门狗、整机复位、镜像准入、数据/BSS 初始化和执行安全边界。UDP 发现可以是可选能力；缺少发现不应阻止直接指定设备地址。不要直接将 AT 命令 UART 当作可靠 TCP 字节流。

### 5. 注册与构建

从实际 ESP 注册表及 CLI 读取当前字段约定，不要猜 JSON 字段。将新后端放在独立目录，创建对应 `backend.json` 和独立板卡 `board.json`，显式关联后端、工具链/SDK 与板级配置。模板必须保持 unsupported，直到构建入口、完整实现和证据都存在。

```sh
python3 tools/rmb.py backends
python3 tools/rmb.py boards
python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

编译期配置应收敛到一个受版本控制的文件（本仓为 `config/build.defaults`），profile 与凭据以构建期覆盖层追加，`safe` 必须关闭服务和模块运行；凭据文件由参数传入、不落仓库。占位网络值只用于 compile-only 检查，不得部署。新后端也必须具备同样的防误启动行为。

### 6. 验收与证据

- 原生：使用真实生产核心，覆盖碎片写、边界、不同几何、回调失败、损坏摘要、错误绑定、标记擦写失败、重复提交与重启锁存。参考 `tests/test_portable_core.c` 和 `rom/test_rom.c`。
- 构建：记录 SDK/编译器版本、精确命令和结果，分别覆盖关闭与占位启用配置。编译成功不等于部署通过。
- 网络：独立验证错误鉴权、短 I/O、断线、慢客户端、资源耗尽与恢复。
- 模块执行：验证底座绑定、数据/BSS、唯一运行时、外设/ISR 生命周期和坏入口/停滞后的管理恢复。不得声称对恶意模块强隔离。
- 实机：操作者授权后测试真实 Flash 擦写、复位/看门狗、目标 BOOT/UART、电源故障与重新上传恢复。断开 TCP 不等于物理掉电。
- 报告：分别列出“通过”“失败”“未执行”及环境；不把模板、模拟或 CI 配置文件算作硬件证据。不要附上个人路径、设备地址或凭据，使用 `DEVICE_IP` 等符号。

---

## English

### 1. Current support

Only the ESP-IDF backend registered by `backends/esp-idf/backend.json` is implemented for deployment. The SDK baseline is **ESP-IDF 5.5.5**, with board entry `boards/esp32c3-stm32f103/board.json`. Host simulation is test infrastructure; other platform templates are unsupported. This guide defines porting contracts and acceptance criteria, not new-platform support or completed CI/hardware validation.

### 2. Understand the two portable algorithms

**Updating the bridge business module:** `include/rmb/platform.h`, `include/rmb/update.h`, and `core/update.c`.

- `bridge_update` holds caller-owned volatile state without heap allocation. Callers must serialize API calls.
- `bridge_update_init` validates geometry; `bridge_update_boot` reads the marker and latches permission. Marker-read I/O failure must not grant update permission.
- `bridge_update_allowed` and `bridge_execution_allowed` expose state decisions. The latter is not a replacement for image admission checks.
- `bridge_update_invalidate` erases and reads back only the marker. It does not unload executing code, change the boot latch, or rewrite image bytes/RAM.
- `bridge_update_begin` requires permission for this boot and invalidates the marker before erasing the body. `bridge_update_write` accepts fragments at sequential offsets and buffers them into write units.
- `bridge_update_finish` pads the final write unit, performs image admission, hashes the original image length, synchronizes, and finally writes/readbacks the all-zero marker. Once committed, another upload in that boot is forbidden; reset is required.

**Downloading to the connected STM32 ROM:** `rom/rom.h`, `rom/rom.c`, and `rom/devices.c`.

- `rom_validate` / `rom_download` / `rom_download_traced` validate images and run the download protocol. `rom_probe` is read-only but resets the target and leaves it in ROM. `rom_restart` drives BOOT0 low and resets an already programmed target.
- `rom_io` supplies `tx`, `rx`, `boot`, `reset`, and `sleep_ms`. TX/RX return transferred bytes, zero for timeout, or negative errors; short I/O is allowed. The caller configures UART as 8E1.
- `rom_source.read` provides random image reads and returns zero on success. The image must remain immutable during validation and download.
- `rom_device` defines Flash/RAM bounds, uniform pages or nonuniform sectors, vector alignment, ROM device ID, and erase-command preference. The family-level ID does not prove a specific SKU or physical capacity.
- `rom_trace_fn` is synchronous diagnostics and must not reenter or modify I/O. Do not expose image bytes or secrets.

These operations address different storage: the bridge business-module slot versus the connected STM32. Do not conflate their capacity, markers, or reset semantics.

### 3. Storage adapter contract

Implement `bridge_storage_ops` with caller-owned `ctx`. Callbacks return `bridge_error`, not raw SDK-specific errors.

| Field/callback | Required behavior |
|---|---|
| `size` | Entire single-slot capacity including the final marker region; strict bounds |
| `erase_unit` / `write_unit` | Actual erase geometry and the programming unit used by the core; nonzero, alignment-compatible, write unit at most `BRIDGE_MAX_WRITE_UNIT` |
| `read` | Read logical storage bytes and report failures honestly |
| `erase` | Erase only the requested range; the current core requires logical erased bytes of `0xff` |
| `program` | Accept core-aligned offsets/lengths while respecting physical constraints and bounds |
| `sha256` | Real SHA-256 over the requested original stored bytes, excluding final padding |
| `check_image` | Nonexecuting format, exact base-build binding, and expected-size admission; never call entry |
| Optional `sync` | Synchronize mapping/cache and report failures; never call entry |

The marker must occupy independent physical erase units, never a shared body sector. Slot capacity and marker size must be erase-aligned; the erase unit must be divisible by the write unit. Do not make the current ESP 4096-byte footer a universal constant. Media whose logical erase value is not `0xff` cannot directly use this algorithm without an explicit core/protocol change and tests.

For encrypted Flash, verify logical read values, programming alignment, repeated-program restrictions, and whether the all-zero marker is implementable. Compilation is not proof of encrypted-storage support. The test digest substitute checks callback ranges and comparisons, not cryptographic correctness.

### 4. Platform responsibilities and current ESP files

- `bridge_lifecycle_ops` declares `monotonic_us`, `watchdog_feed`, and `reset`. It is not a complete scheduler or proof that all current service lifecycle calls use this table.
- The portable part of `esp32_rom/main/` (`resident.c`, `generic_network.c`, `module_runtime.c`, the `.inc` units) reaches the platform only through `include/rmb/port.h`; platform code is confined to `esp32_rom/main/platform/`. The criterion is `esp32_rom/tests/test_port_boundary.py`, not intuition.
- `esp32_rom/main/module_runtime.c` / `.h` integrate ESP partition storage, mapping, loading, and updates. `module_runtime_prepare` reserves resources without execution; `module_runtime_init` executes entry and belongs only in the supervised worker, never upload commit.
- `esp32_rom/main/module_abi.h` defines `module_header`, `module_host`, `module_ops`, and the image ABI. `esp32_rom/module/build_module.py` binds modules to an exact base build. Binaries are not portable across CPUs, SDKs, ABIs, or base builds.
- `esp32_rom/module/entry.c` and `business.c` implement the business module; `module/logic/spi_diagnostic.c` implements the workflow; `module/platform/esp_idf_io.c` / `.h` and `board_config.h` provide its ESP UART/GPIO/SPI adapter and board configuration.
- `esp32_rom/main/Kconfig.projbuild` and `esp32_rom/partitions.csv` define service configuration and layout. Pins/network settings are not portable-core APIs.

A new backend must implement reliable network-stream short I/O, deadlines, disconnects, backpressure, and resource limits; storage, clock, watchdog, full reset, image admission, data/BSS initialization, and execution boundaries. UDP discovery can be optional; direct device addressing should still work. An AT-command UART is not automatically a reliable TCP stream.

### 5. Registration and builds

Read the actual ESP registry and CLI for current field names rather than inventing JSON fields. Give a new backend its own directory and `backend.json`, and each board its own `board.json`, explicitly associating backend, toolchain/SDK, and board configuration. Keep templates unsupported until build integration, full implementation, and evidence exist.

```sh
python3 tools/rmb.py backends
python3 tools/rmb.py boards
python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

Keep compile-time configuration in one tracked file (here `config/build.defaults`) and append profile and credential overlays at build time; `safe` must disable service and module execution. Credentials arrive through a path argument and never enter the tree. Placeholder network values are compile-only and must not be deployed. New backends need equivalent protection against accidental activation.

### 6. Acceptance and evidence

- Native: exercise production core with fragmented writes, bounds, different geometry, callback failures, bad digests/binding, marker failures, repeated commits, and reboot latches. See `tests/test_portable_core.c` and `rom/test_rom.c`.
- Build: record SDK/compiler versions, exact commands, and results for disabled and placeholder-enabled configurations separately. Compilation is not deployment validation.
- Networking: independently verify bad authentication, short I/O, disconnects, slow clients, resource exhaustion, and recovery.
- Execution: verify base binding, data/BSS, a single runtime, peripheral/ISR lifecycle, and management recovery after bad entry/stalls. Do not claim isolation from malicious modules.
- Hardware: with operator authorization, test physical Flash operations, reset/watchdog, target BOOT/UART, power faults, and recovery uploads. Disconnecting TCP is not a physical power cut.
- Reporting: distinguish passed, failed, and not run with environment details. Templates, simulations, and CI configuration files are not hardware evidence. Replace personal paths, device addresses, and credentials with symbols such as `DEVICE_IP`.
