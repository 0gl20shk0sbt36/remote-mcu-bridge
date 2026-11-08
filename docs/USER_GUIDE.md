# 服务编写与部署手册 / Service authoring and deployment

## 中文


### 1. 四类镜像不能混用

| 对象 | 运行/存放位置 | 管理方式 |
|---|---|---|
| 通用底座 | ESP 的应用分区 | 首次通过串口安装匹配的 IDF bootloader、分区表和应用 |
| ABI4 服务模块 | ESP `business` 分区，代码 XIP、数据在预留 DRAM | `module_ctl.py`，绑定准确底座 ELF |
| STM Flash 程序 | STM32 内部 Flash | 安装 STM 服务后用 `romctl.py flash` |
| STM SRAM 程序 | STM32 SRAM，复位/掉电丢失 | 安装 STM 服务后用 `romctl.py ram` |

**首次烧入新底座不等于已安装默认 STM 服务。** 启用网络的空白底座提供管理/发现/日志，但服务要单独构建和上传；本仓不承诺已打包可部署的默认合并镜像。若自行做工厂合并烧录，必须另外正确生成模块提交 footer、精确绑定和地址，不可简单把业务 bin 拼进镜像后声称可用。

所有网络控制是明文共享令牌协议，只能在隔离可信局域网使用，不能互联网暴露/端口转发。服务拥有普通进程级 SDK 能力，不是安全沙箱。

### 2. 首次构建底座

安装并激活 ESP-IDF 5.5.5 环境，`idf.py --version` 应匹配。**编译期配置只有一个文件**：`config/build.defaults`（IDF 基线、IWDT/TWDT panic 与 panic reboot、端口、监听栈 12288、STM 引脚与 F103C8 选择都在其中）。不需要为部署另写一份 defaults。

凭据在编译时传入，不进仓库：

- `--credentials-file PATH`：私有文件。支持两种写法 —— 两行位置式（第一行 SSID、第二行密码，可选第三行令牌，即 `wifi.pw` 形式），或 `key=value`/`key: value`（`ssid`、`password`/`psk`、`token`/`lan_token`）。`#` 开头为注释。
- 文件里没有令牌时，用环境变量 `RMB_TOKEN` 补足（16–128 字节）。
- 驱动把该文件生成为构建目录内的 `rmb-generated.defaults`（0600），**优先级高于** `config/build.defaults` 的占位网络值；构建目录内的 `sdkconfig` 与镜像仍按私有产物管理。
- 需要覆盖非网络项时再用 `--user-config`，它排在最后，是最终覆盖。

```sh
# BUILD_DIR 用新的绝对路径；凭据路径按你的实际情况写。
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 \
  --profile compile-enabled --credentials-file "$HOME/wifi.pw" \
  --build-dir "$BUILD_DIR"
# 文件里没有令牌时：
RMB_TOKEN='<16-128 bytes>' python3 tools/rmb.py build ... --credentials-file "$HOME/wifi.pw"
```

构建成功会打印一行**脱敏**汇总（是否配置、字节数、占位符状态、栈大小、STM 型号），不含 SSID/密码/令牌原文。缺少真实 SSID 或令牌会直接失败，不会生成可用镜像。safe profile 关闭网络与模块装载入口。修改配置后必须换新构建目录，旧 `sdkconfig` 不会被旧默认值悄悄覆盖。

### 3. 首次串口安装（会写 ESP Flash）

先确认授权、正确串口、ESP32-C3 型号及备份需求。构建驱动只构建，不烧录。使用本次 IDF 构建生成的 `flasher_args.json`/`flash_args` 核对所有 offset→文件映射、Flash 模式和大小；不得沿用历史分区地址或只刷应用遗漏 bootloader/分区表。使用同一 IDF 项目和构建目录，例如：

```sh
idf.py -C esp32_rom -B "$BUILD_DIR" -p "$ESP_PORT" flash
idf.py -C esp32_rom -B "$BUILD_DIR" -p "$ESP_PORT" monitor
```

`ESP_PORT` 在 Linux 可能是 `/dev/ttyACM0`，Windows 可能是 `COM5`，必须自己确认，不要把示例当设备识别结果。IDF 的 flash 使用该构建生成的烧录参数；先人工检查。串口 monitor 通常用 Ctrl+] 退出。换底座会使旧绑定模块失效，应重新构建模块。以上是操作命令，不代表已执行烧录。

### 4. 准备令牌与获取真实地址

本机设置 `DEVICE_IP`、`TOKEN_FILE` 和 `BASE_ELF`：后者必须是**正在目标上运行的最终底座 ELF**，不能使用另一次编译的同名文件。私有令牌文件只包含令牌，Linux 设置仅本人可读权限；Windows 使用对应文件 ACL。不要把令牌直接写在命令行、日志或 issue 中。

```sh
chmod 600 "$TOKEN_FILE"  # POSIX；Windows 使用 ACL
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE" > status.json
python3 -c 'import json; s=json.load(open("status.json")); print("ABI",s["module_abi"],"text",s["text_address"],"data",s["data_address"],"update",s["update_mode"] )'
```

`status` JSON 在 stdout，重定向保存；没有 `--output` 选项。将实际 `text_address` / `data_address` 设置为 `TARGET_TEXT_ADDRESS` / `TARGET_DATA_ADDRESS`（十进制或 `0x` 十六进制均可）。必须非零且 ABI=4；data 同时由 ELF 验证。SDK 编译数据库和静态库应与 ELF 同目录保留。无法确认 ELF 来源或地址时停止，不要猜。

### 5. 构建默认服务或自定义服务

默认 STM 远程 ROM + UART，不含 SPI 处理/逻辑/驱动：

```sh
python3 esp32_rom/module/build_module.py --base-elf "$BASE_ELF" \
  --text-address "$TARGET_TEXT_ADDRESS" --data-address "$TARGET_DATA_ADDRESS" \
  --builtin-service stm-uart --output "$SERVICE_OUTPUT_DIR/stm-uart.bin"
```

省略服务选择也默认 `stm-uart`。SPI 诊断须显式 `--builtin-service stm-spi`，另见 [默认服务手册](DEFAULT_STM_FIRMWARE.md) 的可选边界；与 `--module-manifest` 互斥。

编写新服务从 `examples/services/hello/` 复制**整个目录**到自己的服务目录，保留服务/平台分层。A 原样 echo，B 仅将 ASCII a–z 大写；它不是底座更新 A/B 分区。manifest 结构：

```json
{
  "name": "hello-A",
  "sources": ["service.c", "platform_idf.c"],
  "drivers": [],
  "defines": {"SERVICE_VARIANT": 1}
}
```

- 必须恰好有 `name/sources/drivers/defines`，禁止重复 JSON key。
- name 为安全标识符；sources 非空且唯一，必须是 manifest 所在目录内实际 `.c` 文件。禁止绝对路径、`../` 越界及符号链接越界；把共享源放在该目录树下，而非绕过验证。
- drivers 只能为不重复的 `uart`/`spi`；纯网络 hello 为 `[]`。整数 defines 不接受字符串或 bool。
- 入口、网络认证、借用 handle、角色租约和 context 生命周期按 [SDK 契约](SERVICE_AUTHORING.md) 实现，不添加旧 ROM 桩或 raw socket fd。

```sh
python3 esp32_rom/module/build_module.py --base-elf "$BASE_ELF" \
  --text-address "$TARGET_TEXT_ADDRESS" --data-address "$TARGET_DATA_ADDRESS" \
  --module-manifest examples/services/hello/A.json --output "$SERVICE_OUTPUT_DIR/hello-A.bin"
```

每次输出目录保存镜像及 `module-link/` 中的 ELF、map、命令、relocations、proof.json。先运行 `sh examples/services/hello/test.sh` 和原生测试；无硬件 synthetic compilecheck 产物明确不可部署。修改底座的监听栈等配置也必须重建绑定。

### 7. 服务与底座的依赖边界

- 服务只拿到 `rmb_host` 函数表（`net`、`now_us`、`progress`、`run`、`watchdog_open/feed/close`），**不链接任何 `rmb_*` 底座符号**；网络只用 `net->listen/read/write/exact/authenticated`。
- 但服务与底座**共享同一套 IDF/FreeRTOS/libc/cJSON 运行时**：不是沙箱。因此底座任何变化（固件、SDK、监听栈等编译期配置、地址布局）都必须重新构建并上传服务；旧服务装进新底座会被绑定哈希拒绝。
- 构建服务必须用**设备 `status` 上报的** `text_address`/`data_address` 与正在运行的底座 ELF；写错地址会被准入拦下（实测如此）。
- 板卡是数据：`boards/*/board.json` 的引脚/型号必须与编译出的 `sdkconfig` 一致，构建驱动会逐根校验；换板卡只需新增 JSON。
- 度量脚本：`python3 tools/analyze_dependencies.py --proof <service>/module-link/proof.json`。

### 6. 远程安装/替换服务

这是 ESP 服务更新，不是 STM flash。已有服务运行时必须先失效 footer 并复位，绝不热卸载后立即擦正文：

```sh
python3 tools/module_ctl.py enter-update --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
# 等待 ESP 重启、重新联网；重复 status 直到 update_mode=true、busy=false。
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/module_ctl.py upload "$SERVICE_IMAGE" --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/module_ctl.py restart --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
# 等待重连，然后确认 module_ready=true、fault=false。
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
```

设置 `SERVICE_IMAGE` 为刚构建的实际模块。新空白底座若已报告允许更新可直接 upload，但统一先确认状态。上传成功只表示提交完成，重启才激活。中断/哈希失败保持未提交，应调查后在更新启动重传；不能声称自动回滚。CLI 正式名称是 **upload/restart**，不是内部 wire 名称 `module-upload/esp-restart`。3337 为默认管理 TCP 端口，可用 `--port` 覆盖；其他管理选项有 `--timeout`。

换成 hello 后 3333 是 hello 协议，不再是 STM ROM；不能拿 romctl 的 STM 操作测 hello。需要 STM 功能时重新安装 stm-uart。服务协议不一致与底座管理不可用要分开诊断。

### 7. 故障与恢复

```sh
python3 tools/module_ctl.py get-fault --host "$DEVICE_IP" --token-file "$TOKEN_FILE" > fault.json
# 人工审阅并将该报告真实序号设置为 FAULT_SEQ：
python3 tools/module_ctl.py ack-fault --fault-seq "$FAULT_SEQ" --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
```

不要编造 seq。ACK 只确认读取，不自动清除隔离；`clear-fault` 要求无待确认报告，且不能绕过当前启动准入/重复入口限制。通常修复后 enter-update→上传→restart；需要保留旧服务时按报告决定是否 restart/clear-fault。详见 [看门狗恢复](WATCHDOG_RECOVERY.md)。`test-hang` 是故意故障注入，不能作为日常健康检查。

诊断顺序：管理 status → 完整 fault → 网络/令牌/ELF 绑定 → 服务协议与资源 → 目标硬件。底座管理可用不代表 STM 已连线或模块已 ready。不会自动擦保护位、自动反复擦写或替用户授权破坏性操作。

---

## English

### 1. Keep the four images separate

| Artifact | Location | Installation/control |
|---|---|---|
| Generic resident | ESP application partition | First serial installation of matching IDF bootloader/partition table/application |
| ABI4 service | ESP business partition, XIP code/reserved DRAM | `module_ctl.py`; exact resident ELF binding |
| STM Flash application | STM internal Flash | `romctl.py flash` after installing the STM service |
| STM SRAM application | STM SRAM; lost on reset/power loss | `romctl.py ram` after installing the STM service |

**A newly flashed resident does not automatically contain the default STM service.** An enabled blank resident exposes management/discovery/logs; build and install the service separately. No ready-to-deploy merged default image is promised. Custom factory provisioning must correctly implement binding, mapping and commit footer, not merely append a module binary.

All control uses plaintext shared-token trusted-LAN protocols. Isolate the network; never expose or forward ports to the Internet. A service has ordinary SDK/process privileges, not sandbox isolation.

### 2. Build a private resident

Activate ESP-IDF 5.5.5 and check `idf.py --version`. **Compile-time configuration is a single file**: `config/build.defaults` (IDF baseline, IWDT/TWDT panic and reboot-on-panic, ports, the 12288-byte listener stack, STM pins and the F103C8 selection). You do not need a second defaults file for deployment.

Credentials are supplied at build time and never stored in the tree:

- `--credentials-file PATH`: a private file. Either the positional `wifi.pw` shape (line 1 SSID, line 2 password, optional line 3 token) or `key=value` / `key: value` with `ssid`, `password`/`psk`, `token`/`lan_token`. `#` starts a comment.
- If the file has no token, `RMB_TOKEN` (16–128 bytes) may supply it.
- The driver renders that file into `rmb-generated.defaults` inside the build directory (mode 0600) **ahead of** the placeholder network values in `config/build.defaults`. The generated sdkconfig and firmware remain private artifacts.
- Use `--user-config` only for non-network overrides; it is applied last and wins.

```sh
# Use a new absolute BUILD_DIR; point the credentials path at your own file.
python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 \
  --profile compile-enabled --credentials-file "$HOME/wifi.pw" \
  --build-dir "$BUILD_DIR"
# When the file carries no token:
RMB_TOKEN='<16-128 bytes>' python3 tools/rmb.py build ... --credentials-file "$HOME/wifi.pw"
```

A successful build prints one **redacted** summary line (configured or not, byte counts, placeholder state, stack size, STM part) with no SSID, password or token text. A missing real SSID or token fails the build instead of producing a deployable image. Safe disables networking and module loading. After a configuration change, use a fresh build directory; stale sdkconfig values are not silently reused.

### 3. First serial installation

Obtain authorization, confirm ESP32-C3, serial port and backup needs. Inspect this build's IDF-generated `flasher_args.json`/`flash_args`: offsets, files, Flash mode and size must match. Never reuse historical offsets or omit bootloader/partition table from a fresh installation.

```sh
idf.py -C esp32_rom -B "$BUILD_DIR" -p "$ESP_PORT" flash
idf.py -C esp32_rom -B "$BUILD_DIR" -p "$ESP_PORT" monitor
```

IDF uses the generated flashing arguments; review them first. Linux `/dev/ttyACM0` and Windows `COM5` are examples, not detected ports. Exit monitor with Ctrl+]. Replacing the resident requires rebuilding bound services. These commands are instructions, not a claim that flashing was performed.

### 4. Token file and actual mapping

Set local `DEVICE_IP`, `TOKEN_FILE`, and `BASE_ELF`; the ELF must be the **exact final image currently deployed**, not a same-named rebuild. Store only the token in a private file. Use POSIX permissions or Windows ACLs, never literal command-line/logged credentials.

```sh
chmod 600 "$TOKEN_FILE"  # POSIX only; Windows: restrict ACLs
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE" > status.json
python3 -c 'import json; s=json.load(open("status.json")); print("ABI",s["module_abi"],"text",s["text_address"],"data",s["data_address"],"update",s["update_mode"] )'
```

Status JSON goes to stdout; there is no `--output` flag. Set `TARGET_TEXT_ADDRESS` and `TARGET_DATA_ADDRESS` from actual nonzero fields and require ABI4. The builder verifies data against the ELF. Retain the matching compile database, generated configuration and static archives beside the ELF. Stop if provenance/mapping is unknown.

### 5. Build a service

Default ROM/UART service, without SPI handlers, logic or driver:

```sh
python3 esp32_rom/module/build_module.py --base-elf "$BASE_ELF" \
  --text-address "$TARGET_TEXT_ADDRESS" --data-address "$TARGET_DATA_ADDRESS" \
  --builtin-service stm-uart --output "$SERVICE_OUTPUT_DIR/stm-uart.bin"
```

Omitting selection also defaults to stm-uart. Optional SPI requires explicit `--builtin-service stm-spi`. Builtin selection and `--module-manifest` are mutually exclusive.

For a custom service copy the complete `examples/services/hello/` directory and retain service/platform separation. A echoes; B uppercases ASCII a–z only. This service A/B example is not A/B partition rollback. Manifest example:

```json
{"name":"hello-A","sources":["service.c","platform_idf.c"],"drivers":[],"defines":{"SERVICE_VARIANT":1}}
```

Exactly name/sources/drivers/defines are required, with no duplicate keys. Sources must be unique real `.c` files inside the manifest directory tree; no absolute paths, parent escapes or symlink escapes. Drivers may contain unique uart/spi entries, or none for hello. Defines require safe macro names and integer values, not strings/bools. Implement [the SDK contract](SERVICE_AUTHORING.md), not obsolete ROM stubs or raw socket descriptors.

```sh
python3 esp32_rom/module/build_module.py --base-elf "$BASE_ELF" \
  --text-address "$TARGET_TEXT_ADDRESS" --data-address "$TARGET_DATA_ADDRESS" \
  --module-manifest examples/services/hello/A.json --output "$SERVICE_OUTPUT_DIR/hello-A.bin"
```

Use separate output directories and retain image plus module-link ELF/map/commands/relocations/proof. Run `sh examples/services/hello/test.sh` and native tests. Synthetic compilecheck output is **not deployable**. Resident stack/layout changes require a newly bound service.

### 7. Dependency boundary between service and resident

- A service receives only the `rmb_host` table (`net`, `now_us`, `progress`, `run`, `watchdog_open/feed/close`) and imports **no `rmb_*` base symbols**; networking goes through `net->listen/read/write/exact/authenticated`.
- The service still **shares the resident's IDF/FreeRTOS/libc/cJSON runtime**: this is not a sandbox. Any resident change (firmware, SDK, compile-time settings such as the listener stack, address layout) requires rebuilding and re-uploading the service; an old service is rejected by the binding hash.
- Build against the `text_address`/`data_address` **reported by the device** and the exact resident ELF that is running; a wrong address is rejected by admission (observed on hardware).
- Boards are data: the pins/device in `boards/*/board.json` must match the compiled `sdkconfig`, which the driver verifies pin by pin; adding a board is a JSON change.
- Metrics: `python3 tools/analyze_dependencies.py --proof <service>/module-link/proof.json`.

### 6. Install or replace over the network

```sh
python3 tools/module_ctl.py enter-update --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
# Wait for reset/reconnection and status update_mode=true, busy=false.
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/module_ctl.py upload "$SERVICE_IMAGE" --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/module_ctl.py restart --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
# Wait for reconnection; require module_ready=true and fault=false.
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
```

Set SERVICE_IMAGE to your real artifact. A blank resident already allowing updates may upload directly after status verification. Existing services require footer invalidation and a full reset **before erasing the body**; there is no hot unload. Upload commits; restart activates. Interrupted/hash-failed transfers remain uncommitted, not automatically rolled back. Public CLI names are **upload/restart**, not internal module-upload/esp-restart. Management defaults to TCP 3337, with `--port` and `--timeout` overrides.

Installing hello changes port 3333 to hello's protocol; STM romctl operations will no longer apply. Reinstall stm-uart to restore STM functions. Resident availability and service protocol compatibility are separate checks.

### 7. Fault recovery

```sh
python3 tools/module_ctl.py get-fault --host "$DEVICE_IP" --token-file "$TOKEN_FILE" > fault.json
# Review the report and set FAULT_SEQ to its actual sequence.
python3 tools/module_ctl.py ack-fault --fault-seq "$FAULT_SEQ" --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
```

Never invent a sequence. ACK acknowledges diagnostics, not quarantine removal. `clear-fault` requires acknowledged reports and cannot bypass boot/entry gates. Typically repair, enter-update, upload, restart; consult [watchdog recovery](WATCHDOG_RECOVERY.md) before preserving/restarting old code. `test-hang` is deliberate destructive fault injection, not a health check.

Diagnose resident status, full fault, networking/token/ELF binding, service protocol/resources, then target hardware. Management availability does not prove wiring or module readiness. Do not automatically unprotect option bytes or repeatedly perform destructive retries.
