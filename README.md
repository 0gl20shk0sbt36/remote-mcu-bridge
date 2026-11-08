# Remote MCU Bridge

## 中文

通过网络管理 MCU 桥接器，将可移植的更新状态机、STM32 系统 ROM 下载算法与具体平台实现分开。当前只有 **ESP-IDF 后端**是实际实现；目标组合为 ESP32-C3 桥接器与 STM32F103，SDK 基线为 **ESP-IDF 5.5.5**。设备描述中保留 F103C8 与 F407ZG 的算法参数，不代表这两个目标的所有组合都经过硬件验证。

### 状态与边界

- 默认服务为 **STM ROM + UART（`stm-uart`）**，SPI 仅显式 `--builtin-service stm-spi` 构建；新底座需另装服务，不附带已部署业务。详见 [使用手册](docs/USER_GUIDE.md) 与 [默认固件手册](docs/DEFAULT_STM_FIRMWARE.md)。
- 当前源码为 **ABI4 通用底座 + 独立服务**；STM32 擦写、探测、目标复位与 UART/SPI 属于服务，底座不含 ROM 回调。2026-11-08 已完成独立 [ABI4 实机验收](docs/validation/abi4-hardware-20261108.md)；历史 ABI3 报告仍单列，ABI3 模块不能装入 ABI4 底座。
- 服务使用 `include/rmb/service.h` 的网络与监督接口，显式维护看门狗角色；故障报告可由重连客户端从管理端口读取并按序号确认。
- 原生测试验证生产核心算法与模拟 I/O，不是可部署的第二个后端。
- 其他平台的模板仅供移植设计使用，**不受支持、不能视为可构建或可运行的实现**。
- 默认关闭网络服务及模块运行入口。启用占位配置只用于编译检查；不是部署配置，不应烧录。
- 本地原生 **28/28**、safe/enabled 交叉构建及该固定板组合的 ABI4 实机验收已完成；未执行远程 CI，未测试真实写入中断电或新板卡。以实际命令输出和独立报告为准。
- 网络控制使用明文、共享令牌的可信局域网协议；不是 TLS。只能部署在隔离可信网络，不得直接暴露互联网或转发端口。

### 快速开始：无需硬件

需要 C11 编译器、CMake（支持项目 presets）、Ninja 与 Python 3。在仓库根目录运行：

```sh
python3 tools/native_deps.py
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

这些命令是复现入口，不是已通过的测试记录。原生测试覆盖范围由 CMake 注册的用例决定；主机模拟不能证明真实 Flash 擦写、外设时序或看门狗恢复。

### 选择后端与板卡

```sh
python3 tools/rmb.py backends
python3 tools/rmb.py boards
python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103
```

`python` 指向 Python 3 时也可使用 `python tools/rmb.py ...`。先安装并激活 ESP-IDF 5.5.5 的环境，确认 `idf.py --version`，再运行配置和构建。注册表位于 `backends/esp-idf/backend.json` 与 `boards/esp32c3-stm32f103/board.json`；以注册表和 CLI 帮助为准，不要把模板重命名后当作受支持的后端。

安全基线见 `config/ci-disabled.defaults`；`config/ci-enabled.defaults` 中的公开网络名称、令牌及引脚是 **compile-only 占位值**。配置名称中的 `ci` 不代表已执行 CI。部署前应在本地独立配置网络、强随机令牌、准确引脚与目标几何，确认服务与模块运行开关，并审查生成的 `sdkconfig`。不要将本地配置提交到版本库。

构建不会证明接线正确，也不应自动烧录。先断开目标电源，核对电平、公共地、交叉连接 UART TX/RX、BOOT 与复位线以及外部上拉。不能从板卡名字推断具体模块的引脚兼容性。

### 架构：可移植算法不等于可移植驱动

```text
主机工具 → 网络管理服务（ESP-IDF）
                     ├─ core/update.c → bridge_storage_ops → ESP 分区/哈希
                     └─ 模块运行时 → 业务模块 → rom/rom.c → rom_io → UART/GPIO
```

`core/update.c` 是无 SDK 依赖的单槽更新算法。`bridge_storage_ops` 提供存储容量、擦除/写入单位、回读、擦除、编程、SHA-256、镜像准入与可选同步；`bridge_lifecycle_ops` 声明时钟、看门狗与复位服务。声明生命周期接口不表示所有生命周期逻辑已经迁移到核心。

`rom/rom.c` 是 STM32 ROM 协议算法，通过 `rom_io` 使用 UART、BOOT、复位与延时，通过 `rom_source` 随机读取不可变镜像，通过 `rom_device` 选择几何与设备标识。UART 8E1、GPIO、网络、Flash 分区、缓存/XIP 映射、FreeRTOS 及模块装载属于后端，不属于可移植算法。

单槽更新先使提交标记失效；本次启动的写入许可在启动时锁存。已运行业务的启动周期不能通过擦标记立即覆写业务，必须整机复位进入更新环境。接收完整镜像、校验格式/底座绑定及哈希后，最后写入全零提交标记；再次复位才加载新模块。它不是 A/B 回滚，不承诺恶意模块隔离；哈希和底座绑定也不是数字签名。

### 源码导航

| 路径 | 职责 |
|---|---|
| `include/rmb/platform.h` | 通用错误码、存储与生命周期回调契约 |
| `include/rmb/update.h`、`core/update.c` | 单槽更新状态和算法 |
| `rom/rom.h`、`rom/rom.c`、`rom/devices.c` | STM32 ROM API、协议与设备几何 |
| `rom/fake_rom.c`、`rom/native_bridge.c`、`rom/test_rom.c` | 原生模拟器、主机桥与协议单元测试 |
| `backends/esp-idf/backend.json` | 唯一实际部署后端的注册信息 |
| `boards/esp32c3-stm32f103/board.json` | 板卡选择及其后端关联 |
| `esp32_rom/main/service.c`、`*.inc` | ESP-IDF 管理服务、流与监督逻辑 |
| `esp32_rom/main/module_runtime.c`、`module_runtime.h` | ESP 模块存储/加载与更新适配 |
| `include/rmb/service.h`、`esp32_rom/main/module_abi.h` | 通用服务 SDK 与平台镜像头，不引入 ROM API |
| `esp32_rom/module/entry.c`、`business.c` | 可替换业务模块入口与业务 |
| `esp32_rom/module/logic/spi_diagnostic.c` | 独立 SPI 诊断流程逻辑 |
| `esp32_rom/module/platform/esp_idf_io.c`、`board_config.h` | 业务 UART/GPIO/SPI 的 ESP 适配与板级配置 |
| `esp32_rom/module/build_module.py` | 与精确底座构建绑定的模块构建工具 |
| `esp32_rom/main/Kconfig.projbuild`、`esp32_rom/partitions.csv` | 服务配置与 ESP 分区布局 |
| `tools/rmb.py` | 注册表发现、配置与构建入口 |
| `tools/romctl.py`、`tools/module_ctl.py` | 目标 ROM 与桥接器业务模块的不同管理工具 |
| `tests/test_portable_core.c`、`esp32_rom/tests/` | 可移植核心测试与 ESP 相关主机检查 |

网络示例只使用本地环境变量；`DEVICE_IP` 由操作者在本地设置。请先阅读工具帮助，不要在命令示例、日志或 issue 中写入令牌：

```sh
python3 tools/romctl.py --help
python3 tools/module_ctl.py --help
```

### 文档、贡献与许可证

- [维护与二次开发](docs/DEVELOPMENT.md) · [服务编写与部署手册](docs/USER_GUIDE.md) · [默认 STM ROM/UART 手册](docs/DEFAULT_STM_FIRMWARE.md)

- [本地验证结果与边界](docs/TESTING.md) · [ABI4 重构合同](docs/ABI4_REFACTOR.md) · [看门狗与重连诊断](docs/WATCHDOG_RECOVERY.md)
- [服务 A/B 实测与耦合评估](docs/SERVICE_AUTHORING.md) · [SPI 全 FF 深度调查](docs/SPI_INVESTIGATION.md)
- [接线与 SRAM 限制](docs/WIRING.md) · [模块构建](docs/MODULE_BUILD.md) · [文档索引](docs/README.md) · [后端移植](docs/BACKEND_PORTING.md)
- [贡献指南](CONTRIBUTING.md) · [安全政策](SECURITY.md)
- [MIT 许可证](LICENSE)，Copyright (c) 2026 Remote MCU Bridge contributors。外部 SDK 和第三方组件仍适用各自许可证。

---

## English

A network-managed MCU bridge separating a portable update state machine and STM32 system-ROM download algorithms from platform implementation. **ESP-IDF is the only implemented deployment backend**, targeting an ESP32-C3 bridge with STM32F103 and using **ESP-IDF 5.5.5** as the SDK baseline. F103C8 and F407ZG algorithm descriptions do not establish hardware validation for every target combination.

### Status and limits

- The default service is **STM ROM + UART (`stm-uart`)**; SPI requires explicit `--builtin-service stm-spi`. A new resident requires separate service installation. See [User guide](docs/USER_GUIDE.md) and [Default firmware manual](docs/DEFAULT_STM_FIRMWARE.md).
- Current sources use **ABI4 generic resident + independent services**. STM32 programming/probing/reset and target UART/SPI belong to the service, not resident callbacks. Independent [ABI4 hardware validation](docs/validation/abi4-hardware-20261108.md) ran on 2026-11-08; earlier ABI3 reports remain historical. ABI3 modules cannot load on ABI4.
- Services use `include/rmb/service.h` and explicitly maintain watchdog-role leases. Authenticated reconnecting clients retrieve diagnostics and acknowledge the exact fault sequence.
- Native tests exercise production algorithms and simulated I/O; they are not a second deployable backend.
- Other platform templates are design scaffolding: **unsupported, not buildable or runnable implementations**.
- Network service and module execution entry points are disabled by default. Placeholder-enabled configuration is for compilation only, not deployment or flashing.
- Local 28/28 CTest, safe/enabled cross-builds and ABI4 validation on this fixed bench combination passed. Remote CI, physical mid-write power loss and new boards remain untested; see actual outputs and the hardware report.
- Network management uses a plaintext, shared-token trusted-LAN protocol, not TLS. Use an isolated trusted network; never expose it directly to the Internet or forward its ports.

### Quick start without hardware

Install a C11 compiler, CMake supporting the project presets, Ninja, and Python 3. From the repository root:

```sh
python3 tools/native_deps.py
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

These are reproduction commands, not a passing-test record. Coverage is defined by the tests registered in CMake. Host simulation cannot verify physical Flash behavior, peripheral timing, or watchdog recovery.

### Select a backend and board

```sh
python3 tools/rmb.py backends
python3 tools/rmb.py boards
python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103
```

`python tools/rmb.py ...` is equivalent where `python` resolves to Python 3. Install and activate ESP-IDF 5.5.5 first and check `idf.py --version`. The registry entries are `backends/esp-idf/backend.json` and `boards/esp32c3-stm32f103/board.json`. Consult the registry and CLI help; renaming a template does not create a supported backend.

The safe baseline is `config/ci-disabled.defaults`. Public network names, token strings, and pins in `config/ci-enabled.defaults` are **compile-only placeholders**. The `ci` filename is not evidence that CI ran. Before deployment, create private local configuration with the actual network, a strong random token, correct pins and target geometry, review service/module execution switches, and inspect the generated `sdkconfig`. Never commit local configuration.

A build does not verify wiring and should not automatically flash anything. With target power disconnected, check voltage levels, common ground, crossed UART TX/RX, boot/reset signals, and required external pull-ups. A board name does not prove pin compatibility with a particular module.

### Architecture: portable algorithms are not portable drivers

```text
Host tools → network management service (ESP-IDF)
                   ├─ core/update.c → bridge_storage_ops → ESP partition/hash
                   └─ module runtime → business module → rom/rom.c → rom_io → UART/GPIO
```

`core/update.c` is an SDK-independent single-slot update algorithm. `bridge_storage_ops` supplies capacity, erase/write geometry, readback, erase, program, SHA-256, image admission, and optional synchronization. `bridge_lifecycle_ops` declares clock, watchdog, and reset services; its declaration does not imply that every lifecycle policy has moved into the core.

`rom/rom.c` implements the STM32 ROM protocol through `rom_io` for UART, boot control, reset, and delay; `rom_source` for random access to an immutable image; and `rom_device` for geometry and identity. UART 8E1 setup, GPIO, networking, Flash partitions, cache/XIP mapping, FreeRTOS, and module loading belong to the backend, not the portable algorithms.

Single-slot updates invalidate the commit marker first. Write permission is latched at boot: a boot that ran business code cannot immediately overwrite it by erasing the marker; a full reset must enter the update environment. Only after complete reception, format/base binding checks, and hash verification is the all-zero commit marker written. Another reset loads the new module. This is not A/B rollback or isolation from malicious modules. Hashes and base-image binding are not digital signatures.

### Source map

| Path | Responsibility |
|---|---|
| `include/rmb/platform.h` | Portable errors and storage/lifecycle callback contracts |
| `include/rmb/update.h`, `core/update.c` | Single-slot update state and algorithms |
| `rom/rom.h`, `rom/rom.c`, `rom/devices.c` | STM32 ROM API, protocol, and device geometry |
| `rom/fake_rom.c`, `rom/native_bridge.c`, `rom/test_rom.c` | Native simulator, host bridge, and protocol unit tests |
| `backends/esp-idf/backend.json` | Registry for the only implemented deployment backend |
| `boards/esp32c3-stm32f103/board.json` | Board selection and backend association |
| `esp32_rom/main/service.c`, `*.inc` | ESP-IDF management service, streams, and supervision |
| `esp32_rom/main/module_runtime.c`, `module_runtime.h` | ESP module storage/loading and update integration |
| `esp32_rom/main/module_abi.h` | Image header, ABI, and host/module function tables |
| `esp32_rom/module/entry.c`, `business.c` | Replaceable business module entry and implementation |
| `esp32_rom/module/logic/spi_diagnostic.c` | Separate SPI diagnostic workflow logic |
| `esp32_rom/module/platform/esp_idf_io.c`, `board_config.h` | Business UART/GPIO/SPI ESP adapter and board configuration |
| `esp32_rom/module/build_module.py` | Module builder bound to an exact base build |
| `esp32_rom/main/Kconfig.projbuild`, `esp32_rom/partitions.csv` | Service configuration and ESP partition layout |
| `tools/rmb.py` | Registry discovery, configure, and build entry point |
| `tools/romctl.py`, `tools/module_ctl.py` | Separate target-ROM and bridge business-module tools |
| `tests/test_portable_core.c`, `esp32_rom/tests/` | Portable-core tests and ESP-related host checks |

Use local environment variables for network examples; set `DEVICE_IP` locally. Read tool help first and do not put tokens in examples, logs, or issues:

```sh
python3 tools/romctl.py --help
python3 tools/module_ctl.py --help
```

### Documentation, contributing, and license

- [Development and maintenance](docs/DEVELOPMENT.md) · [Service authoring/deployment](docs/USER_GUIDE.md) · [Default STM ROM/UART](docs/DEFAULT_STM_FIRMWARE.md)

- [Local verification and limits](docs/TESTING.md)
- [Service A/B and coupling assessment](docs/SERVICE_AUTHORING.md) · [SPI all-FF investigation](docs/SPI_INVESTIGATION.md)
- [Wiring and SRAM limits](docs/WIRING.md) · [Module builds](docs/MODULE_BUILD.md) · [Documentation index](docs/README.md) · [Backend porting](docs/BACKEND_PORTING.md)
- [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md)
- [MIT license](LICENSE), Copyright (c) 2026 Remote MCU Bridge contributors. External SDKs and third-party components retain their own licenses.
