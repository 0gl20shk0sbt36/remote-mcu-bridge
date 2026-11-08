# 底座/服务依赖与平台耦合分析 / Dependency and platform-coupling analysis

## 中文

本页是**实测**结论，不是设计意图。所有数字由仓库内脚本生成，可随时复跑：

```sh
python3 tools/analyze_dependencies.py                 # 源码级平台耦合
python3 tools/analyze_dependencies.py --proof <module-link/proof.json>   # 服务→底座链接级审计
```

`proof.json` 由 `esp32_rom/module/build_module.py` 在构建服务时生成，记录该服务**实际导入**的底座符号——这是唯一可靠的口径，源码 grep 看不到宏或内联产生的调用。

### 1. 服务 → 底座：接口耦合接近零

| 项目 | 实测 |
|---|---|
| 服务导入的底座私有 API（`rmb_*` 符号） | **0 个** |
| 服务访问底座的全部途径 | `rmb_host` 函数表：`net`、`now_us`、`progress`、`run`、`watchdog_open/feed/close`；网络走 `net->read`/`net->write`/`net->exact`/`net->listen`/`net->authenticated` |
| 服务导入的全部符号 | 99 个，构成为 ESP-IDF 33 · FreeRTOS 28 · libc/compiler-rt 24 · cJSON 11 · 其它 3 |
| 服务自带定义 | 83 个全局符号（业务实现） |
| 从底座静态库抽取的成员 | 12 个（仅服务显式声明的驱动集合） |

也就是说：**换一个同 ABI 的底座实现，服务不需要改源码**，因为接口只有一张函数表；但它仍然**链接在底座所在的那个运行时上**（ESP-IDF/FreeRTOS/libc/cJSON），所以换平台时这些导入要随平台一起变。

### 2. 底座 → 服务：4 个钩子

| 钩子 | 说明 |
|---|---|
| `module_entry_fn` / `entry` | 唯一入口，`int entry(const rmb_host *)`，只在受监督 worker 执行一次 |
| `module_host_configure(&host)` | 装填函数表与 ABI 版本 |
| `module_service_available()` | 服务健康位，用于 `module_ready` 判定 |
| `module_watchdog_configured()` | 入口是否成功建立根租约；未建立视为初始化失败 |

此外底座在装载期调用 `rmb_network_init_begin/commit/abort` 来完成监听器准入——这是底座内部状态机，不属于服务需要实现的接口。

### 3. 平台耦合（源码级，剔除注释与字符串）

| 区域 | 代码行 | 平台引用 | 密度（refs/kloc） |
|---|---:|---:|---:|
| 可移植核心（`core/`、`include/rmb/`） | 433 | 0 | 0.0 |
| ROM 算法（`rom/`） | 524 | 0 | 0.0 |
| 底座可移植部分（`resident.c`、`generic_network.c`、`module_runtime.c`、三个 `.inc`） | 1708 | 0 | 0.0 |
| ESP 平台层（`esp32_rom/main/platform/`，3 个文件 443 行） | 443 | 126 | 284.4 |
| STM 服务（`esp32_rom/module/`） | 1769 | 261 | 147.5 |
| hello 示例服务 | 242 | 7 | 28.9 |

- **平台无关代码 2665 行**，占比 52%；**含平台代码 2454 行**，其中底座侧只有 443 行。
- 底座里平台代码占比：443 / (1708+443) ≈ **21%**，且 100% 位于 `esp32_rom/main/platform/`，由 `esp32_rom/tests/test_port_boundary.py` 强制（可移植文件出现任何平台标识符即失败，且 46 个契约函数必须有实现）。
- Python 侧：`tools/` 2685 行仅 33 处平台相关（串口名、Windows 分支）；后端驱动 427 行 9 处（IDF 版本、`IDF_PATH`）；模块构建器 568 行 7 处（`--just-symbols`、RISC-V 前缀）。

### 4. 仍然存在的硬耦合

| 耦合 | 性质 | 移植影响 |
|---|---|---|
| 精确底座 ELF 哈希 + 设备上报的取指/数据地址绑定 | 设计选择（省 RAM、支持在线换服务） | 底座一变，所有服务必须重建；地址写错会被准入拒绝（实测已发生并被设备拦下） |
| 固定槽几何：1 MiB 槽 + 4096 B footer + 40 B 头 + 32 KiB 数据窗口 | 两侧共享常量（`MODULE_SLOT_SIZE` 等） | 新平台要提供同几何槽，或同时改两侧常量 |
| 需要 BSD 风格 socket（管理/日志/发现） | 契约隐含要求 | 无 lwIP/POSIX 的平台需加薄适配层 |
| 可执行映射语义（报告实际 VA、保护锁定，做不到必须失败） | 平台必须实现 | 无 MMU/XIP 的 MCU 只能退化为静态链接服务 |
| 服务直调宿主 SDK（UART/GPIO/SPI） | 有意的分层 | 换平台时服务按新 SDK 重写，**底座不动** |
| 共享地址空间、无隔离 | 设计选择 | 不是安全沙箱；恶意模块无防护 |

### 5. 结论

1. **接口层面已解耦到位**：服务与底座之间 0 个私有符号，只有一张函数表和 4 个钩子。
2. **平台层面底座已经很薄**：可移植部分平台引用为 0，ESP 胶水 443 行且被测试钉死。
3. **移植成本的真正大头在服务侧**（1769 行 STM 服务 + 模块构建器），因为服务按设计使用宿主 SDK；这也正是"底座不强制服务用哪套驱动"的代价。
4. 想要再降低移植成本，优先级依次是：模块构建器平台化（可执行基址/段布局/重定位）→ 服务驱动层按平台分包 → 槽几何参数化。

## English

Everything below is **measured**, not intended design. Reproduce with the commands in the Chinese section; `proof.json` comes from `build_module.py` and records the base symbols a service really imports — the only honest source, since a text grep cannot see macro or inlined calls.

### 1. Service → base: near-zero interface coupling

| Item | Measurement |
|---|---|
| Base-private API symbols (`rmb_*`) imported by a service | **0** |
| Every route into the base | the `rmb_host` table: `net`, `now_us`, `progress`, `run`, `watchdog_open/feed/close`; networking through `net->read`/`write`/`exact`/`listen`/`authenticated` |
| All imported symbols | 99: ESP-IDF 33, FreeRTOS 28, libc/compiler-rt 24, cJSON 11, other 3 |
| Symbols the service defines | 83 globals (business implementation) |
| Base archive members extracted | 12 (only the driver sets the service declares) |

A different base implementation of the same ABI would not require service source changes, because the interface is one function table. The service is still **linked into the base's runtime** (ESP-IDF/FreeRTOS/libc/cJSON), so those imports move with the platform.

### 2. Base → service: four hooks

`module_entry_fn` (single entry, `int entry(const rmb_host *)`, executed once on the supervised worker), `module_host_configure(&host)`, `module_service_available()` (health bit behind `module_ready`) and `module_watchdog_configured()` (whether the entry established a root lease; missing means initialization failed). The base additionally drives its own `rmb_network_init_begin/commit/abort` listener admission — internal state, not part of the service interface.

### 3. Platform coupling (comments and string literals excluded)

| Area | Code lines | Platform refs | Density (refs/kloc) |
|---|---:|---:|---:|
| Portable core (`core/`, `include/rmb/`) | 433 | 0 | 0.0 |
| ROM algorithms (`rom/`) | 524 | 0 | 0.0 |
| Resident portable half | 1708 | 0 | 0.0 |
| ESP platform layer (3 files, 443 lines) | 443 | 126 | 284.4 |
| STM service (`esp32_rom/module/`) | 1769 | 261 | 147.5 |
| hello example service | 242 | 7 | 28.9 |

Platform-free code is **2665 lines (52%)**. The base contains only **443 lines** of platform code (~21% of the base), all inside `esp32_rom/main/platform/` and enforced by `test_port_boundary.py` (any platform identifier in a portable file, or a missing contract function, fails the test). Python: 33 platform references in 2685 lines of host tools, 9 in the 427-line backend driver, 7 in the 568-line module builder.

### 4. Remaining hard coupling

Exact base ELF hash plus device-reported text/data addresses (a deliberate choice for RAM economy and in-place service replacement — a wrong address is rejected by admission, observed on hardware); fixed slot geometry (1 MiB slot, 4096-byte footer, 40-byte header, 32 KiB data window) shared as constants by both sides; BSD-style sockets for management/logs/discovery; executable-mapping semantics that must fail rather than pretend when the platform cannot enforce them; services calling the host SDK directly (deliberate layering: a port rewrites services, not the base); shared address space with no isolation (not a sandbox).

### 5. Verdict

Interface coupling is solved (zero private symbols, one table, four hooks). The base is thin (443 lines of glue, zero stray references elsewhere, boundary enforced by a test). The real porting cost sits in the services and the module builder, because services are SDK-native by design. To reduce it further, in order: make the module builder platform-aware (executable base, section layout, relocations), split service driver layers per platform, then parameterise slot geometry.
