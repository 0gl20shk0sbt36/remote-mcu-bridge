# 文档索引 / Documentation

## 中文

先阅读根目录 [README](../README.md)，了解支持范围、安全默认值、原生测试与 ESP-IDF 5.5.5 构建入口。

优先路线：首次使用 → [服务编写与部署](USER_GUIDE.md) → [默认 STM ROM/UART](DEFAULT_STM_FIRMWARE.md)；二次开发 → [开发与维护](DEVELOPMENT.md) → [服务 SDK 契约](SERVICE_AUTHORING.md)。新底座不自动含 STM 服务；SPI 是可选显式构建。

| 文档/源码 | 用途 |
|---|---|
| [ABI4 重构](ABI4_REFACTOR.md)、[服务编写](SERVICE_AUTHORING.md)、[看门狗与重连诊断](WATCHDOG_RECOVERY.md) | 当前接口、生命周期、故障读取/确认与验收边界 |
| [后端移植指南](BACKEND_PORTING.md) | 通用算法、平台职责、注册流程与验收边界 |
| [跨平台移植手册](PORTING_GUIDE.md) | 执行清单：平台能力门槛、46 个函数要求、必须改的文件、冻结的 ABI 常量、模块构建器移植、验收清单与常见坑 |
| [底座平台移植](RESIDENT_PLATFORM.md) | 端口契约 46 个函数、新平台最小实现清单、边界强制测试与 ESP-IDF 参考映射 |
| [依赖与平台耦合分析](DEPENDENCY_ANALYSIS.md) | 实测数字：服务→底座 0 个私有符号、平台代码占比与剩余硬耦合；附可复跑脚本 `tools/analyze_dependencies.py` |
| [贡献指南](../CONTRIBUTING.md) | 开发流程、测试和提交要求 |
| [变更记录](../CHANGELOG.md) | 版本级变更（Keep a Changelog 格式） |
| [验证证据索引](validation/README.md) | 稳定命名的证据快照；日期在文件内部，不在文件名 |
| [安全政策](../SECURITY.md) | 可信网络限制、漏洞报告和部署风险 |
| [许可证](../LICENSE) | MIT 条款与版权 |
| `include/rmb/platform.h` | 存储与生命周期回调的权威定义 |
| `include/rmb/update.h` | 更新状态机的公开 API |
| `rom/rom.h` | ROM 下载/探测/重启 API 及 I/O 约定 |
| `esp32_rom/main/module_abi.h` | 当前 ESP 模块 ABI 与镜像结构 |
| `backends/esp-idf/backend.json` | 唯一实际后端的注册信息 |
| `boards/esp32c3-stm32f103/board.json` | 当前板卡注册信息 |

从仓库根目录运行 `python3 tools/rmb.py --help` 查看 CLI 契约；通过 `backends` 和 `boards` 查询实际注册项。其他平台模板是不受支持的设计起点，原生测试也不是部署后端。

文档描述实现与操作入口，不是执行日志。没有命令输出或单独硬件报告时，不应推断 CI 已执行、板卡已验证或掉电恢复已验证。公开启用配置中的网络与令牌均为仅供编译的占位值，不能部署。将来新增专题文档时，请在此索引说明其范围与证据状态。

---

## English

Start with the root [README](../README.md) for support limits, safe defaults, native tests, and ESP-IDF 5.5.5 build entry points.

Recommended path: first deployment → [User guide](USER_GUIDE.md) → [Default STM ROM/UART](DEFAULT_STM_FIRMWARE.md); maintenance → [Development](DEVELOPMENT.md) → [Service SDK contract](SERVICE_AUTHORING.md). A new resident requires separate service installation; SPI is explicitly optional.

| Document/source | Purpose |
|---|---|
| [ABI4 contract](ABI4_REFACTOR.md), [Service SDK](SERVICE_AUTHORING.md), [Watchdog recovery](WATCHDOG_RECOVERY.md) | Current interfaces, lifecycle, diagnostic acknowledgment and evidence limits |
| [Backend porting guide](BACKEND_PORTING.md) | Portable algorithms, platform responsibilities, registration, and acceptance limits |
| [Cross-platform porting handbook](PORTING_GUIDE.md) | Execution checklist: capability gate, the 46 functions, files to change, frozen ABI constants, module-builder port, acceptance list and pitfalls |
| [Resident platform porting](RESIDENT_PLATFORM.md) | The 46-function port contract, the minimum a new host implements, the enforced boundary test and the ESP-IDF reference mapping |
| [Dependency and platform coupling](DEPENDENCY_ANALYSIS.md) | Measured: zero base-private symbols imported by a service, platform-code share, remaining hard coupling; reproducible via `tools/analyze_dependencies.py` |
| [Contributing](../CONTRIBUTING.md) | Development workflow, tests, and submission requirements |
| [Changelog](../CHANGELOG.md) | Versioned changes (Keep a Changelog format) |
| [Validation evidence index](validation/README.md) | Evidence snapshots with stable names; dates live inside the files |
| [Security policy](../SECURITY.md) | Trusted-network limits, vulnerability reporting, and deployment risks |
| [License](../LICENSE) | MIT terms and copyright |
| `include/rmb/platform.h` | Authoritative storage/lifecycle callback definitions |
| `include/rmb/update.h` | Public update-state-machine API |
| `rom/rom.h` | ROM download/probe/restart APIs and I/O contracts |
| `esp32_rom/main/module_abi.h` | Current ESP module ABI and image structure |
| `backends/esp-idf/backend.json` | Registry for the only implemented backend |
| `boards/esp32c3-stm32f103/board.json` | Current board registry entry |

Run `python3 tools/rmb.py --help` from the repository root for the CLI contract; query actual entries with `backends` and `boards`. Other platform templates are unsupported starting points, and native tests are not a deployment backend.

Documentation describes implementation and reproduction entry points, not execution logs. Without command output or a separate hardware report, do not infer CI execution, board validation, or verified power-loss recovery. Public enabled configurations contain compile-only network/token placeholders and must not be deployed. Add future topic documents here with their scope and evidence status.
