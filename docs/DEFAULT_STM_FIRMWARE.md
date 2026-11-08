# 默认 STM ROM/UART 服务手册 / Default STM ROM and UART service



### 1. “默认固件”的准确含义

默认构建选择是 ESP 上的 **`stm-uart` ABI4 服务**：通过 STM32 系统 ROM 远程探测、Flash/SRAM 下载、目标复位及应用 UART 通信。它不是给 STM32 自动安装的应用，也不意味着新烧底座已带业务；请先按 [使用与部署](USER_GUIDE.md) 安装底座、构建并上传服务。省略模块选择等同 `--builtin-service stm-uart`，不含 SPI handlers、SPI 诊断逻辑或 GP-SPI 驱动。

支持证据限于既有固定 ESP32-C3 ↔ STM32F103C8 bench，ABI4 验证记录 描述当时结果。F407ZG 虽有算法/几何参数，不能据此声明硬件已验收。本文没有执行新的烧录或实机测试。旧 ABI4 报告中的 600 次 SPI 是显式可选服务的历史记录，不是默认能力。

网络协议是明文共享令牌，仅适合隔离可信 LAN，不是 TLS，不可直接接入互联网/端口转发。不要提交本地网络配置、令牌或可能含凭据的底座/服务产物。

### 2. 接线与供电

两板断电后操作。以下是芯片 GPIO/端口名，不是排针序号或 D 编号；仅适用固定板组合。逻辑只能 3.3 V，信号线不得接 5 V。

| ESP32-C3 | 方向 | STM32F103C8 | 说明 |
|---|---|---|---|
| GND | — | GND | 公共地必须接 |
| GPIO3 / TX | → | PA10 / USART1 RX | UART 交叉 |
| GPIO4 / RX | ← | PA9 / USART1 TX | UART 交叉 |
| GPIO5 | → | BOOT0 | 不可再用跳帽硬短到电源/地 |
| GPIO6 | → | NRST | 开漏复位，STM 板需正常 3.3 V 上拉 |
| 无 ESP 控制线 | — | PB2 / BOOT1 | 必须由跳帽/可靠下拉保持低 |

可用各板正常 USB 供电，但不得将两个已供电的 3V3/5V 输出并接。本表不要求跨板正电源连接；共用电源需另行核查稳压器/反灌路径。NRST 无上拉时补合适上拉，常见 10 kΩ；不要硬短接。详见 [接线](WIRING.md) 的必需部分，SPI 接线不是默认服务要求。

STM ROM 使用 **115200 8E1**；应用 UART 默认 **115200 8N1**，软件切换格式。probe 不是“完全无副作用”：不擦写 Flash，但会切 BOOT0 并复位进入系统 ROM，打断正在运行的应用；完成后需要 `restart` 恢复 Flash 启动，或继续下载。

### 3. 连接与工具分工

安装 Python 3，主机 CLI 使用标准库，无需运行期 pip 包。设好 `DEVICE_IP`、私有 `TOKEN_FILE`。下面均在仓库根目录运行，令牌不出现在命令行正文。`--token-file` 覆盖环境令牌；不给文件时 romctl 可使用 `STM32_ROM_TOKEN` 或安全交互输入。

| 工具/默认端口 | 用途 |
|---|---|
| `module_ctl.py` / TCP 3337 | ESP 管理、模块状态/更新、故障报告和 ESP restart |
| `romctl.py` / TCP 3333 | STM 服务 status/probe/flash/ram/restart |
| `romctl.py discover` / UDP 3334 | 底座发现；发现不代表 STM 服务 ready |
| `wifidebug.py logs` / TCP 3335 | 底座日志，非 STM UART |
| `wifidebug.py uart` / TCP 3336 | STM 应用 UART，默认 framed |

```sh
python3 tools/romctl.py --help
python3 tools/romctl.py --list-interfaces
python3 tools/romctl.py discover --discovery-timeout 32
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/romctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE" --device f103c8
python3 tools/romctl.py probe --host "$DEVICE_IP" --token-file "$TOKEN_FILE" --device f103c8 > probe.json
```

发现默认收满 32 秒；自动选设备默认 3 秒，只接受唯一设备；多设备必须明确 `--host`。可重复 `--interface <本机IPv4>` 选择网卡，`--passive-only` 只监听；Linux 枚举使用 `ip -j`，Windows 使用 PowerShell，不能把发现失败等同目标离线。需要时用实际 IP 显式连接。其他选项有 `--port`、`--discovery-port`、`--timeout`（默认120秒）。

**总是显式 `--device f103c8`**：romctl 的历史 CLI 默认仍是 f407zg，不能把该默认当 bench 型号。该 flag 只选择主机镜像校验几何，不会改变已安装 STM 服务的编译型号；两侧必须匹配。

### 4. 可下载二进制的要求

使用原始 little-endian `.bin`，不能直接上传 ELF/HEX 或 ESP 模块。头两个 word 为初始 MSP 和带 Thumb 位的 reset PC；PC 必须在镜像范围，MSP 8 字节对齐且在允许 SRAM 内，SRAM 镜像还需栈高于镜像结尾。缺少向量表或按错误地址链接的 bin 应重新链接，不要改掉检查。

| F103C8 目标 | 镜像基址 | 最大镜像 | 额外要求 |
|---|---|---|---|
| Flash | `0x08000000` | `0x10000`（64 KiB） | 按 Flash 启动布局链接 |
| SRAM | `0x20000400` | `0x4c00` | 预留 ROM 低 SRAM；栈、data/bss/heap 均须纳入实际 SRAM 预算 |

SRAM 程序须专门链接、正确设置向量表和启动初始化，不能把 Flash bin 改扩展名当 SRAM 程序。CLI 校验向量/大小不证明 bss、运行时栈或中断启动逻辑正确。可参考 `tools/ramtarget/ramtarget.py --help` 和设备描述，但不要假定仓库提供了所有 STM 应用工程。

### 5. 探测、Flash、SRAM 与复位

先确认镜像来源、型号、地址、备份与授权。`probe` 采集 bootloader 版本/GET VERSION/option 字节和设备 ID；不是任意保护状态可写的证明。遇读写保护、option byte 异常或 ID/能力不匹配，停止调查；**不自动 unprotect、不改 option bytes、不做破坏性重复重试**。某些解除保护会全片擦除，必须另行评估授权。

```sh
# 写 STM Flash：会擦除镜像覆盖的页/扇区，回读验证后复位启动应用。
python3 tools/romctl.py flash "$STM_FLASH_BIN" --device f103c8 \
  --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
# 写 STM SRAM：不擦 STM Flash；回读验证后 ROM GO 执行。
python3 tools/romctl.py ram "$STM_RAM_BIN" --device f103c8 \
  --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
# 复位 STM，BOOT0=0，重新启动 Flash 应用（不是重启 ESP）。
python3 tools/romctl.py restart --host "$DEVICE_IP" --token-file "$TOKEN_FILE" --device f103c8
```

服务先接收并校验镜像，ROM 按块写入、4 字节对齐补 `0xff`，再完整回读（含补齐字节），只有验证成功才执行。Flash 擦除的是镜像覆盖单元，不保证页内其他旧数据保留；不是自动全片备份/恢复。CRC/回读不是签名，也不保证掉电安全。SRAM 镜像掉电、复位或再次 probe 后要重载；restart 不会恢复 SRAM 程序，而是启动原有 Flash 应用。

### 6. 应用串口：framed、stdio、PTY、raw

UART 端口默认 framed，带控制/数据分离和编程优先状态。打开串口流不会复位目标。ROM 操作优先接管 UART；不要把编程期间的暂停当作应用 UART 连续性保证，不要同时启动多个争抢会话。

```sh
# 交互应用 UART；默认115200 8N1。
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --baud 115200
# 精确发送 UTF-8 文本，不自动加换行；按应用协议自行提供终止符。
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" exec --send "status" --duration 3
# 二进制子进程管道，stdout 仅为应用数据；不要与日志混写。
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --stdio
# Linux 虚拟串口；设置不存在的用户可写链接路径。
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --pty --pty-link "$UART_PTY_LINK"
# 显式兼容旧 raw 服务；编程可断开，不能和PTY组合。
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --legacy-raw
# 底座日志与原始接收保存。
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" logs --duration 10 --save bridge.log
```

`--stdio` 适合程序 stdin/stdout 的二进制接入，Windows 使用二进制 stdio 而不是 PTY。`--pty` 仅 Linux，供本机串口软件打开工具打印的 PTY/链接，不创建 Windows COM；工具拒绝覆盖已有链接。`--save` 保存接收原始字节，不是另一个自动烧录接口。`--send` 是 UTF-8 文本，不是通用十六进制/二进制参数。Windows 可用 `python` 替代 `python3`，用 PowerShell 环境变量和路径替换 POSIX `$VAR`；只看 bat 包装文件不能推断额外协议能力。

### 7. 常见故障

| 现象 | 优先检查 |
|---|---|
| 无发现/连接超时 | 同一可信 LAN、网卡/防火墙、实际 IP/端口、底座是否启用并联网 |
| 认证失败 | 私有令牌文件/权限、设备对应关系；不输出令牌或完整认证报文 |
| 3337 可达、3333/3336 不可用 | module_ready/fault、安装的是否 hello 而非 stm-uart、初始化/租约报告 |
| ROM sync/UART timeout | 公共地、TX/RX交叉、BOOT1低、BOOT0无硬短、NRST上拉/释放、115200 8E1 |
| unexpected ID/capability/option错误 | 型号编译/CLI选择一致、芯片真实ID/ROM版本/保护状态；不绕过准入 |
| readback mismatch | 电源/接触/时序、目标地址与镜像；停止执行，不自动盲重擦 |
| invalid stack/reset vector/size | 用正确基址重新链接 raw bin；检查 SRAM 栈与镜像重叠 |
| 应用 UART 没输出 | 目标是否已从 ROM退出、应用8N1/波特率、镜像是否正确、流暂停/其他占用 |
| 看门狗/反复隔离 | 先 get-fault 保存、按准确seq ACK，再依 [恢复手册](WATCHDOG_RECOVERY.md) 修复，不代喂卡住角色 |

### 8. 可选 SPI 不属于默认

只有明确需要诊断时另建独立输出：将构建选择改为 `--builtin-service stm-spi`，仍按服务更新流程安装。还需 [可选接线](WIRING.md)、匹配的 STM SPI 从机应用及 UART 握手。仓库没有承诺提供完整可构建 STM SPI 从机工程；也不把"曾经通过"当作排除间歇全 FF 故障的依据。

---

### 9. 默认服务与底座的关系

- 默认服务只通过 `rmb_host` 与底座交互，不链接任何 `rmb_*` 底座符号；但它运行在底座的 IDF/FreeRTOS/libc 运行时里，**不是沙箱**。
- 因此：底板固件、SDK、监听栈等编译期配置或地址布局一变，默认服务必须用新底座 ELF + 设备 `status` 上报的地址重建后再上传；旧镜像会被绑定哈希拒绝。
- 安装流程与自定义服务相同：`module_ctl.py enter-update`（空槽开机已处于更新模式）→ `upload` → `restart`。
- 未安装服务时，底板只有管理/日志/发现可用；`romctl` 的 STM 命令会因 3333 无监听而失败，这是预期行为。

## English

### 1. What “default firmware” means

The default is the ESP-side **stm-uart ABI4 service**: remote STM32 system-ROM probing, Flash/SRAM download, target reset and application UART. It is not an automatically installed STM application or a bundled service in a blank resident. Follow [deployment](USER_GUIDE.md) to build/upload it. Omitted selection equals `--builtin-service stm-uart`; no SPI handlers, diagnostic logic or GP-SPI driver are included.

Existing hardware evidence is limited to the fixed ESP32-C3/F103C8 bench; see ABI4 record. F407ZG parameters are not hardware acceptance. No new hardware tests/flashing were performed for this manual. Historical 600-transaction SPI evidence belongs to explicit optional diagnostics, not default capabilities.

Use only an isolated trusted LAN: plaintext shared-token control is not TLS and must never be Internet-exposed/port-forwarded. Keep credentials and possibly credential-bearing artifacts private.

### 2. Wiring and power

Disconnect both boards before wiring. These are chip GPIO/port names, not header numbers or D aliases. All logic is 3.3 V, never 5 V.

| ESP32-C3 | Direction | STM32F103C8 | Requirement |
|---|---|---|---|
| GND | — | GND | Mandatory common ground |
| GPIO3 TX | → | PA10 USART1 RX | Cross UART |
| GPIO4 RX | ← | PA9 USART1 TX | Cross UART |
| GPIO5 | → | BOOT0 | No hard supply/ground jumper |
| GPIO6 | → | NRST | Open drain; proper 3.3 V board pull-up |
| No ESP wire | — | PB2 BOOT1 | Reliable jumper/pull-down to low |

Separate normal USB power is possible; never tie two powered 3V3/5V outputs together. A positive cross-board supply is not required. Shared power needs regulator/backfeed checks. Add an appropriate NRST pull-up if absent (commonly 10 kΩ), never a hard short. See [required wiring](WIRING.md); SPI wiring is not a default requirement.

ROM uses **115200 8E1** and application UART defaults to **115200 8N1**. Software switches formats. Probe does not erase Flash, but it **resets the target into ROM**, interrupting its application. Use restart to return to Flash boot or continue programming.

### 3. Tools and discovery

Host CLIs require Python 3 standard library only. Set local DEVICE_IP/TOKEN_FILE and run from the repository root. Token-file overrides environment; romctl otherwise accepts STM32_ROM_TOKEN or secure interactive input.

| Tool / default port | Purpose |
|---|---|
| module_ctl / TCP3337 | Resident module status/update/faults and ESP restart |
| romctl / TCP3333 | STM status/probe/flash/ram/restart |
| romctl discover / UDP3334 | Resident discovery, not proof of STM readiness |
| wifidebug logs / TCP3335 | Resident logs, not target UART |
| wifidebug uart / TCP3336 | Framed application UART |

```sh
python3 tools/romctl.py --help
python3 tools/romctl.py --list-interfaces
python3 tools/romctl.py discover --discovery-timeout 32
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/romctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE" --device f103c8
python3 tools/romctl.py probe --host "$DEVICE_IP" --token-file "$TOKEN_FILE" --device f103c8 > probe.json
```

Discover collects the full 32-second default window; automatic selection uses 3 seconds and requires exactly one device. Select --host explicitly for multiple devices. Repeat --interface with local IPv4 addresses or use --passive-only. Linux enumeration uses `ip -j`, Windows PowerShell; failed discovery is not proof of offline hardware. Other options include --port, --discovery-port and --timeout (120 seconds default).

**Always specify --device f103c8** for this bench: the historical CLI default remains f407zg. This flag selects local image validation only; it does not change the installed service's compiled target.

### 4. Binary requirements

Upload raw little-endian bin, not ELF/HEX or an ESP module. Its first words are initial MSP and Thumb reset PC. PC must lie inside the image, MSP must be 8-byte aligned in permitted SRAM, and SRAM image stack must lie above the image. Relink invalid images; do not disable checks.

| F103C8 image | Link base | Maximum image |
|---|---|---|
| Flash | 0x08000000 | 0x10000 (64 KiB) |
| SRAM | 0x20000400 | 0x4c00 |

SRAM needs a dedicated linker/startup/vector layout preserving ROM low SRAM and budgeting data/bss/heap/stack. Renaming a Flash bin does not make it SRAM-compatible. Vector checks do not prove runtime memory or interrupt correctness. Consult `tools/ramtarget/ramtarget.py --help` and device descriptions; not every STM application project is provided.

### 5. Probe and programming

Verify image provenance, target, backups and authorization. Probe collects ROM version, GET VERSION/option bytes and ID; this does not establish that protected memory is writable. Stop on protection/option anomalies or mismatched ID/capabilities. **No automatic unprotect, option-byte changes or destructive retries**; unprotect may mass-erase and needs separate authorization.

```sh
python3 tools/romctl.py flash "$STM_FLASH_BIN" --device f103c8 \
  --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/romctl.py ram "$STM_RAM_BIN" --device f103c8 \
  --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/romctl.py restart --device f103c8 --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
```

Flash erases covered pages/sectors, writes in blocks with four-byte 0xff padding, verifies all bytes including padding, then resets to application boot. Other old data within erased units is not preserved. SRAM does not erase STM Flash; verification precedes ROM GO. CRC/readback is not signing or power-loss safety. SRAM code is lost after reset, power loss or another probe. STM restart sets BOOT0 low and starts the Flash application; it does not restart the ESP or restore SRAM code.

### 6. Application UART modes

Framed mode separates data/control and programming-priority state. Opening a stream does not reset the target. ROM operations take UART ownership; application continuity during programming is not guaranteed. Avoid competing sessions.

```sh
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --baud 115200
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" exec --send "status" --duration 3
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --stdio
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --pty --pty-link "$UART_PTY_LINK"
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" uart --legacy-raw
python3 tools/wifidebug.py --host "$DEVICE_IP" --token-file "$TOKEN_FILE" logs --duration 10 --save bridge.log
```

Interactive UART defaults to 115200 8N1. --send transmits exact UTF-8 text without adding a newline, not arbitrary hex/binary. --stdio provides binary process stdin/stdout; keep diagnostics separate. Use binary stdio on Windows. --pty is Linux-only, prints a local PTY for serial software and refuses existing links; it does not create a Windows COM port. Explicit --legacy-raw is for compatibility, may disconnect for programming, and cannot combine with PTY. --save stores received raw bytes. On Windows substitute Python command, PowerShell variables and paths for POSIX syntax; batch wrappers do not imply additional protocol capabilities.

### 7. Troubleshooting

| Symptom | Check first |
|---|---|
| Discovery/connection failure | LAN, active NIC, firewall, actual IP/port, enabled resident |
| Authentication rejection | Correct private file/device pairing and permissions; never print credentials |
| Management works, service absent | module_ready/fault, installed hello versus STM, init/lease report |
| ROM sync/timeout | Ground, crossed UART, BOOT1 low, BOOT0 free, NRST pull-up/release, 115200 8E1 |
| ID/capability/options mismatch | Actual target/ROM/protection and host/service target agreement; never bypass checks |
| Readback mismatch | Power/contact/timing/address; stop execution, do not blindly re-erase |
| Invalid vectors/size | Relink at correct base; check SRAM stack overlap |
| No application UART | Exit ROM, correct 8N1/baud/application, paused or competing stream |
| Watchdog quarantine | Save get-fault, ACK exact seq, then follow recovery guidance; do not mask a stuck role |

### 8. SPI is optional, not default

Only if diagnostics are explicitly required, build into a separate output directory with `--builtin-service stm-spi` and install through the normal service update flow. This also requires [optional wiring](WIRING.md), a matching STM SPI slave application and UART handshakes. A complete buildable STM slave project is not promised, and past successes do not eliminate intermittent all-FF faults.

### 9. How the default service relates to the resident

- The default service talks to the resident only through `rmb_host` and imports no `rmb_*` base symbols, but it runs inside the resident's IDF/FreeRTOS/libc runtime: **not a sandbox**.
- Therefore any change to the resident firmware, SDK, compile-time settings (for example the listener stack) or address layout requires rebuilding the service against the new base ELF plus the device-reported addresses, then re-uploading; an old image is rejected by the binding hash.
- Installation is the same flow as any service: `module_ctl.py enter-update` (a blank slot already boots in update mode) -> `upload` -> `restart`.
- Without an installed service the resident only offers management/logs/discovery; `romctl` STM commands fail because nothing listens on 3333, which is expected.

