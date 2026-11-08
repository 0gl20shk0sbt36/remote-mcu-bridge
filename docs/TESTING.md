# 测试 / Testing

## 中文

本页说明**怎么跑测试、每个套件覆盖什么、哪些不被覆盖**。某次运行的结果属于本地开发记录，不入库也不随包发布（见 `CONTRIBUTING.md`）；判定以你本地实际输出为准。

### 1. 原生测试（无需硬件）

```sh
python3 tools/native_deps.py                 # 获取固定提交的 cJSON
export IDF_PATH=/path/to/esp-idf-5.5.5       # 部分夹具从这里取 cJSON / mbedtls
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

`IDF_PATH` 只用于让夹具找到 cJSON/mbedtls 的头与源文件，不需要完整工具链。未设置时 `supervisor_protocol`、`spi_pad_request` 等会报 "cJSON missing"，那是环境问题而非回归。已有依赖也可通过 `RMB_CJSON_PATH` 指定。

### 2. 套件构成

| 套件 | 覆盖内容 |
|---|---|
| `portable_core`、`supervisor_core` | 无 SDK 依赖的单槽更新状态机与监督规则（擦写中断、回读错误、边界、提交失败） |
| `rom_unit`、`rom_e2e` | AN3155 协议核心（假 UART/假 ROM）；不验证真实时序 |
| `port_boundary` | 可移植文件不得出现平台 API；`port.h` 的契约函数必须全部有实现 |
| `generic_network` | 鉴权、首行重放、借用句柄代次、监听并发、初始化闸门、exact 截止期 |
| `service_watchdog` | 生产 C 的租约与故障日志（ASan/UBSan），含迟到喂狗、槽复用、CRC、序号确认 |
| `supervisor_deadline`、`exact_deadline` | 作业截止期、入口失败、最终字节跨截止期、事件顺序 |
| `module_format`、`module_*`、`hello_service_native` | 镜像头/布局/绑定校验、模块构建器与 manifest、示例服务 A/B |
| `build_config`、`registry_contract` | 单一配置文件、凭据层优先级与脱敏、注册表与板卡引脚校验 |
| `client_streams`、`discovery`、`probe_cli` | 主机 CLI 的帧协议、发现逻辑、probe 参数面 |

### 3. 交叉构建与模块链接（无需硬件）

```sh
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 --profile safe
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 --profile compile-enabled
python3 tools/check_module_link.py --base-elf out/esp-idf/esp32c3-stm32f103/compile-enabled/stm32_rom_wifi.elf
```

`safe` 关闭网络与模块装载；`compile-enabled` 使用公开占位凭据，只证明能编译链接，**不可烧录**。模块链接检查使用合成取指地址，产物标注 `NOT DEPLOYABLE`。

### 4. 不在自动化范围内

真实 Flash 时序与擦写中断电、无线链路、硬件看门狗与 RTC 保留、UART/SPI 信号质量、目标芯片行为。这些必须实机验证，仓库不保存相关记录；SPI 的间歇全 FF 故障根因仍未坐实，任何"换线后通过"都不作为永久修复结论。

## English

This page explains **how to run the tests, what each suite covers and what is not covered**. Results of a particular run are local development records — not committed and not shipped (see `CONTRIBUTING.md`); judge by your own output.

### 1. Native tests (no hardware)

```sh
python3 tools/native_deps.py                 # pinned cJSON
export IDF_PATH=/path/to/esp-idf-5.5.5       # fixtures take cJSON/mbedtls from here
cmake --preset native && cmake --build --preset native
ctest --preset native --output-on-failure
```

`IDF_PATH` only lets fixtures find cJSON/mbedtls headers and sources; no toolchain is required. Without it, `supervisor_protocol` and `spi_pad_request` report "cJSON missing" — an environment issue, not a regression. Existing dependencies can also be supplied with `RMB_CJSON_PATH`.

### 2. Suites

`portable_core`/`supervisor_core` cover the SDK-free single-slot update machine and supervision rules; `rom_unit`/`rom_e2e` cover the AN3155 protocol core against a fake target; `port_boundary` proves portable files name no platform API and that every contract function is implemented; `generic_network` covers authentication, first-line replay, generation-bound handles, listener concurrency, the init gate and exact deadlines; `service_watchdog` runs the production lease/fault journal under ASan/UBSan including late feeds, slot reuse, CRC and sequence acknowledgement; `supervisor_deadline`/`exact_deadline` cover job deadlines, failed entry, final-byte deadline crossing and event ordering; `module_format`/`module_*`/`hello_service_native` cover image header, layout and binding, the module builder and manifests, and the example service; `build_config`/`registry_contract` cover the single configuration file, credential overlay priority and redaction, and registry/board pin validation; `client_streams`/`discovery`/`probe_cli` cover the host CLI.

### 3. Cross-builds and module links (no hardware)

`safe` disables networking and module loading; `compile-enabled` uses public placeholder credentials and proves compilation/linking only — **never flash it**. Module link checks use a synthetic text address and are labelled `NOT DEPLOYABLE`.

### 4. Outside automation

Real flash timing and mid-write power loss, radio links, hardware watchdog and RTC retention, UART/SPI signal quality and target-chip behaviour require hardware; the repository stores no such records. The intermittent SPI all-FF cause remains unproven, so "it passed after reseating a wire" is never treated as a permanent fix.
