# ESP32-C3 ↔ STM32F103C8T6 接线 / Wiring

## 中文

适用组合：本项目当前 ESP32-C3 固定板级配置 + STM32F103C8T6。以下编号均为**芯片 GPIO 编号/STM32 端口名**，不是排针序号，也不是任意开发板的 `D0/D1` 别名。其他 ESP 或 STM32 板卡不能直接套用。

**操作前两块板都断电。只使用 3.3 V 逻辑；任何信号线都不要接 5 V。**

### 1. 必需：UART、启动和复位（5 根跨板线）

| ESP32-C3 | 方向 | STM32F103C8T6 | 用途 |
|---|---|---|---|
| GND | — | GND | 公共地，必接 |
| GPIO3 / TX | → | PA10 / USART1 RX | ESP 向 STM32 发送 ROM 命令及应用数据 |
| GPIO4 / RX | ← | PA9 / USART1 TX | STM32 向 ESP 返回数据 |
| GPIO5 | → | BOOT0 | 由业务模块切换 ROM 下载/用户应用启动 |
| GPIO6 | → | NRST / RST | 开漏拉低复位；释放后依靠 STM32 板的上拉恢复高电平 |

STM32 的 **PB2 / BOOT1 必须保持低电平**，可使用板上 BOOT1 低电平跳帽；没有可靠下拉时连接 PB2→GND。当前配置没有 ESP→BOOT1 的控制线，不能把未驱动写成已经测量或主动控制。

- UART 的 TX/RX 必须交叉：`GPIO3→PA10`、`GPIO4←PA9`。
- BOOT0 不能被跳帽硬短接到 GND 或 3.3 V 后又让 GPIO5 驱动；先撤掉这种硬短接，保留板上正常弱下拉即可。
- NRST 不能被硬短接到电源或地。确认板上有到 **3.3 V** 的正常上拉；缺失时再补合适上拉（常见为 10 kΩ）。
- ESP 的 GPIO4 接收端在当前驱动中启用弱上拉。这不等于已经测量 STM32 端电平。
- ROM 下载：当前实测配置为 **115200、8E1**；应用诊断串口为 **115200、8N1**。软件负责切换，不是两者通用的固定串口格式。

### 2. 可选：SPI 主从（另加 4 根跨板线）

保留上面全部线路；SPI 诊断还通过 UART 交换 ARM / READY / DONE 握手。

| ESP32-C3：SPI2 主机 | 方向 | STM32F103：SPI1 从机 |
|---|---|---|
| GPIO7 / SCLK | → | PA5 / SCK |
| GPIO1 / MOSI | → | PA7 / MOSI |
| GPIO0 / MISO | ← | PA6 / MISO |
| GPIO10 / CS | → | PA4 / NSS，低有效 |

配置：Mode 0（CPOL=0、CPHA=0）、MSB first、8 bit、每次 CS 选中传输 64 字节。首次重新连接从 **100 kHz** 开始；接线尽量短，避免悬空和松动。

历史 v18/v19 已做过 100 kHz、500 kHz、1 MHz 各 100 次双向 64 字节校验；**这不是本轮重构版或重新接线后的实机验证**。新版本的状态以仓库验证报告为准。

### 3. 供电与恢复顺序

1. 两板断电，核对型号、3.3 V 逻辑和上述端口，接好 GND，再接其余信号。
2. 可分别使用两块开发板正常的 USB 供电；**不要同时把两个已供电的 3V3/5V 输出并在一起**。本表不要求跨板连接电源正极；若要共用电源，须另外核对稳压器能力和反灌路径。
3. 确认 BOOT1 低、BOOT0 无硬短接、NRST 可正常释放，最后上电。
4. 先确认 ESP 独立管理服务可用，再测试 STM32 ROM `probe`；不要一上电就直接擦写 Flash。
5. SPI 测试前需要 STM32 已运行匹配的 SPI 从机诊断应用。只接好线不等于从机固件已经存在；如果使用 SRAM 版，STM32 断电或复位后须重新加载，Flash 原有应用不会自动变成 SRAM 诊断应用。
6. 本轮板线已拆，软件工作只做本地检查/构建，**不自动连接、刷板或宣称新版本实机通过**。

### 4. 对应源码与公开范围

- 板级引脚：[`esp32_rom/module/platform/board_config.h`](../esp32_rom/module/platform/board_config.h)
- ESP 驱动配置：[`esp32_rom/module/platform/esp_idf_io.c`](../esp32_rom/module/platform/esp_idf_io.c)
- 板卡描述：[`boards/esp32c3-stm32f103/board.json`](../boards/esp32c3-stm32f103/board.json)
- 公开仓不包含旧用户 CubeMX 工程及其未明确许可的生成文件；不能据本说明假定仓内提供了完整可直接构建的 STM32 SPI 从机固件。

## English

Applies to the current fixed ESP32-C3 board configuration and STM32F103C8T6. Numbers identify **chip GPIOs / STM32 port names**, not header positions or arbitrary development-board `D0/D1` aliases. Do not reuse this mapping for another board without checking it.

**Disconnect power from both boards before wiring. All signals are 3.3 V logic; never connect a signal to 5 V.**

### Required UART / boot / reset wiring

| ESP32-C3 | Direction | STM32F103C8T6 | Function |
|---|---|---|---|
| GND | — | GND | Mandatory common ground |
| GPIO3 / TX | → | PA10 / USART1 RX | ROM commands and application data |
| GPIO4 / RX | ← | PA9 / USART1 TX | STM32 replies |
| GPIO5 | → | BOOT0 | Select system ROM or application boot |
| GPIO6 | → | NRST / RST | Open-drain reset; released high by the STM32 board pull-up |

Hold **PB2 / BOOT1 low**, using a reliable board jumper/pull-down or a PB2-to-GND connection. BOOT1 is not driven by the ESP in this configuration. Remove jumpers that hard-short BOOT0 to a supply rail before attaching the GPIO5 driver; a normal weak pull-down may remain. Ensure NRST has a proper pull-up to 3.3 V (commonly 10 kΩ if absent), not a hard short.

UART lines cross. The ESP GPIO4 RX weak pull-up is enabled by the driver; this is not a voltage measurement at the STM32. The tested ROM configuration is 115200 8E1; the diagnostic application uses 115200 8N1. Software switches formats.

### Optional SPI wiring

Keep all required wires: SPI diagnostics also use UART ARM / READY / DONE handshakes.

| ESP32-C3 SPI2 master | Direction | STM32F103 SPI1 slave |
|---|---|---|
| GPIO7 / SCLK | → | PA5 / SCK |
| GPIO1 / MOSI | → | PA7 / MOSI |
| GPIO0 / MISO | ← | PA6 / MISO |
| GPIO10 / CS | → | PA4 / active-low NSS |

Use mode 0, MSB first, 8-bit words and one uninterrupted 64-byte transaction per CS assertion. Start reconnection testing at 100 kHz and keep wires short. Historical v18/v19 hardware tests covered 100 bidirectional 64-byte transactions at each of 100 kHz, 500 kHz and 1 MHz; that does **not** validate the new refactor or rewired hardware.

### Power and recovery

Wire with both boards unpowered, verify mapping and logic levels, connect common ground, then power on. The boards may use their normal separate USB power inputs; do not tie two powered 3V3/5V outputs together. Cross-board positive supply wiring is not required by this table. A shared supply needs separate regulator/backfeed checks.

Check management connectivity and a non-destructive STM32 ROM probe before programming Flash. SPI requires a matching slave diagnostic application already running; an SRAM-loaded application must be reloaded after STM32 reset or power loss. Wiring alone does not install it.

The boards are disconnected during this software revision: no automatic connection, flashing or new hardware-pass claim is made. GPIO mapping is defined in `esp32_rom/module/platform/board_config.h`, implemented by `esp32_rom/module/platform/esp_idf_io.c`, and described by `boards/esp32c3-stm32f103/board.json`. The public repository omits the previous user's CubeMX project and generated files with unclear licensing; it does not include a complete buildable STM32 SPI-slave firmware image.
