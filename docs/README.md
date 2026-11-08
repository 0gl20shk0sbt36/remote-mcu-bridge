# 文档索引 / Documentation

## 中文

先阅读根目录 [README](../README.md)，了解支持范围、安全默认值、原生测试与 ESP-IDF 5.5.5 构建入口。

优先路线：首次使用 → [服务编写与部署](USER_GUIDE.md) → [默认 STM ROM/UART](DEFAULT_STM_FIRMWARE.md)；二次开发 → [开发与维护](DEVELOPMENT.md) → [服务 SDK 契约](SERVICE_AUTHORING.md)。新底座不自动含 STM 服务；SPI 是可选显式构建。

| 文档/源码 | 受众 | 用途 |
|---|---|---|
| [使用手册](USER_GUIDE.md) | 使用者 | 装底座、构建/安装服务、常见错误 |
| [默认固件手册](DEFAULT_STM_FIRMWARE.md) | 使用者 | 默认 `stm-uart` 的接线、probe/烧录、串口模式 |
| [服务编写契约](SERVICE_AUTHORING.md)、[看门狗与重连诊断](WATCHDOG_RECOVERY.md) | 贡献者 | 服务 SDK、生命周期、故障读取与序号确认 |
| [开发与维护](DEVELOPMENT.md)、[如何跑测试](TESTING.md) | 贡献者 | 代码地图、构建、测试入口与不覆盖范围 |
| [跨平台移植手册](PORTING_GUIDE.md) | 移植者 | 执行清单：平台能力门槛、46 个函数要求、必须改的文件、冻结的 ABI 常量、模块构建器移植、验收清单与常见坑 |
| [底座平台移植](RESIDENT_PLATFORM.md) | 移植者 | 端口契约与 ESP-IDF 参考映射 |
| [后端移植指南](BACKEND_PORTING.md)、[注册表契约](REGISTRY.md)、[ESP 移植指南](ESP_PORTING_GUIDE.md) | 移植者 | 通用算法、驱动/注册表契约、ESP 侧连接与更新不变量 |
| [依赖与平台耦合分析](DEPENDENCY_ANALYSIS.md) | 移植者 | 实测耦合数字与复跑脚本 `tools/analyze_dependencies.py` |
| [架构](ARCHITECTURE.md)、[底座/平台边界](BASE_PLATFORM_BOUNDARY.md)、[模块构建](MODULE_BUILD.md) | 贡献者 | 设计约束、职责划分、模块链接校验 |
| [贡献指南](../CONTRIBUTING.md) | 贡献者 | 开发流程、测试与提交要求；哪些内容不入库 |
| [变更记录](../CHANGELOG.md) | 使用者 | 版本级变更（Keep a Changelog 格式） |
| [安全政策](../SECURITY.md)、[许可证](../LICENSE) | 使用者 | 可信网络限制与报告渠道；MIT 条款 |
### 源码入口（给贡献者）

- `include/rmb/service.h` — 服务 SDK；`include/rmb/port.h` — 底座平台契约
- `include/rmb/update.h`、`core/update.c` — 单槽更新状态机
- `rom/rom.h`、`rom/rom.c` — STM32 ROM 协议与 I/O 约定
- `esp32_rom/main/module_abi.h` — 模块镜像头与 ABI
- `backends/esp-idf/backend.json`、`boards/esp32c3-stm32f103/board.json` — 注册表条目

从仓库根目录运行 `python3 tools/rmb.py --help` 查看 CLI 契约；通过 `backends` 和 `boards` 查询实际注册项。其他平台模板是不受支持的设计起点，原生测试也不是部署后端。

文档描述实现与操作入口，不是执行日志。没有命令输出或单独硬件报告时，不应推断 CI 已执行、板卡已验证或掉电恢复已验证。公开启用配置中的网络与令牌均为仅供编译的占位值，不能部署。将来新增专题文档时，请在此索引说明其范围与证据状态。

---

## English

Start with the root [README](../README.md) for support limits, safe defaults, native tests, and ESP-IDF 5.5.5 build entry points.

Recommended path: first deployment → [User guide](USER_GUIDE.md) → [Default STM ROM/UART](DEFAULT_STM_FIRMWARE.md); maintenance → [Development](DEVELOPMENT.md) → [Service SDK contract](SERVICE_AUTHORING.md). A new resident requires separate service installation; SPI is explicitly optional.

| Document | Audience | Purpose |
|---|---|---|
| [User guide](USER_GUIDE.md) | Users | Install the resident, build/install services, common failures |
| [Default firmware manual](DEFAULT_STM_FIRMWARE.md) | Users | Wiring, probe/flash and UART modes of the default `stm-uart` service |
| [Service SDK contract](SERVICE_AUTHORING.md), [Watchdog recovery](WATCHDOG_RECOVERY.md) | Contributors | Service SDK, lifecycle, fault retrieval and sequence acknowledgement |
| [Development](DEVELOPMENT.md), [Testing](TESTING.md) | Contributors | Source map, builds, how to run tests, what is not covered |
| [Cross-platform porting handbook](PORTING_GUIDE.md) | Porters | Capability gate, the 46 functions, files to change, frozen constants, module-builder port, acceptance list |
| [Resident platform porting](RESIDENT_PLATFORM.md) | Porters | Port contract and the ESP-IDF reference mapping |
| [Backend porting guide](BACKEND_PORTING.md), [Registry contract](REGISTRY.md), [ESP porting guide](ESP_PORTING_GUIDE.md) | Porters | Portable algorithms, driver/registry contracts, ESP connection and update invariants |
| [Dependency and platform coupling](DEPENDENCY_ANALYSIS.md) | Porters | Measured coupling and the reproducible analyzer |
| [Architecture](ARCHITECTURE.md), [Base/platform boundary](BASE_PLATFORM_BOUNDARY.md), [Module build](MODULE_BUILD.md) | Contributors | Design constraints, responsibility split, module link checks |
| [Contributing](../CONTRIBUTING.md) | Contributors | Workflow, tests, submission rules, what stays out of the repository |
| [Changelog](../CHANGELOG.md) | Users | Versioned changes (Keep a Changelog format) |
| [Security policy](../SECURITY.md), [License](../LICENSE) | Users | Trusted-network limits and reporting; MIT terms |

### Source entry points (contributors)

- `include/rmb/service.h` — service SDK; `include/rmb/port.h` — resident platform contract
- `include/rmb/update.h`, `core/update.c` — single-slot update state machine
- `rom/rom.h`, `rom/rom.c` — STM32 ROM protocol and I/O contracts
- `esp32_rom/main/module_abi.h` — module image header and ABI
- `backends/esp-idf/backend.json`, `boards/esp32c3-stm32f103/board.json` — registry entries

Run `python3 tools/rmb.py --help` from the repository root for the CLI contract; query actual entries with `backends` and `boards`. Other platform templates are unsupported starting points, and native tests are not a deployment backend.

Documentation describes implementation and reproduction entry points, not execution logs. Without command output or a separate hardware report, do not infer CI execution, board validation, or verified power-loss recovery. Public enabled configurations contain compile-only network/token placeholders and must not be deployed. Add future topic documents here with their scope and evidence status.
