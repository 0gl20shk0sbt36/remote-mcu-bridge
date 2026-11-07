# 新业务模块编写：hello A/B（ABI 3）

## 已完成实机 A→B 更新；与 SPI 历史故障分开判断

`examples/services/hello/service.c` 为同一份源码；A.json/B.json 仅将整数宏
`SERVICE_VARIANT` 设为 1/2。A 原样回显；B **只将 ASCII a–z 大写**，UTF-8
其余字节不变，不按字节反转 Unicode。无 Wi-Fi 初始化、监听 socket、lwIP
初始化、RTOS 任务、UART/SPI 驱动或 ROM 实现。仍使用基座已存在 TCP 3333。
本例本机单测的 socket/auth/timer 替身仅用于 UNIT，不冒充真实目标结果。

请求为一行 JSON（结尾 LF，不超过 1024 个行内容字节，整行共用 10 秒期限）：

```json
{"token":"<从安全存储取得，不写入代码>","op":"service-info"}
{"token":"<从安全存储取得，不写入代码>","op":"echo","text":"hello 世界"}
```

只接受字符串字段 token/op/text；重复字段、未知字段、非对象、错误类型、
原始或转义 NUL、JSON 尾随内容均拒绝。echo 必须有 text，最多 128 UTF-8
**字节**；service-info 不允许 text。认证失败不回显业务文本。返回 JSON
使用基座 cJSON_PrintUnformatted 对文本转义。requests 统计通过认证且 schema
有效的成功操作（含当前 info）；echoes 只统计 echo，均饱和递增。输出含版本、
uptime_ms、ROM 不支持和状态重置说明。每次 module_entry 重置内存计数。

ABI3 的 ROM validate/download/probe/restart 槽位仍然必填，本例显式返回
ROM_E_CAP。dispatch 1 同步借用 `int *fd`，不保存指针、不关闭 socket；3/4
为控制获取/释放，其他通道返回不支持。module_entry 返回静态 const ops；
链接脚本将其置于保留 DRAM；`.module_base_id` 是 data 开头的 32 字节 SHA。

## 构建与本机测试

```sh
export IDF_PATH=/path/to/esp-idf-v5.5.5
# 将真实 riscv32-esp-elf 工具链加入 PATH（或传 --tool-prefix）
python esp32_rom/module/build_module.py \
  --module-manifest examples/services/hello/A.json \
  --base-elf /path/to/exact/current/base.elf \
  --text-address 0x420c0028 --data-address 0x3fc962c0 \
  --output /private/output/A/service.bin
# B 改用 B.json 和独立 B 输出目录；绝不在 A/B 间改 service.c。
sh examples/services/hello/test.sh
ctest --test-dir out/native --output-on-failure
```

地址仅为本次生产目标报告值，不可作为其他基座默认值。每个输出目录使用独立
module-link。manifest 严格只允许 name/sources/drivers/defines；sources 必须是
清单目录内真实 C 文件（拒绝绝对路径、越界/符号链接逃逸）；drivers 显式数组，
白名单 **小写 uart、spi**，不得重复；defines 为安全宏名到整数的映射，拒绝
字符串注入、保留编译器宏等。无 manifest 保留完整旧业务的默认源码及双驱动。
空 drivers 不编译/链接 UART/SPI，不要求 SPI 所有权符号，不提取原子辅助。
ELF 精确导入、runtime 组件排除、GP/孤儿段/边界/SHA 检查均保留。
GNU ld 的 resident weak 符号仍可保留 W/V 类型：以精确地址而非一概要求 A
判断是否被复制；本次零 archive 成员提取进一步排除了运行时副本。

## 实测构建量（不虚构工时）

本次源码物理行数通过程序计数：service.c 87 行、test_service.c 60 行；
manifest 测试 217 行；builder 269→371 行，净增 102 行。它是小服务，但为使
其可编写，先必须修复 builder 的硬编码来源/双驱动假设。

| 版本 | 镜像字节 | text | data | bss |
|---|---:|---:|---:|---:|
| A | 1740 | 1220 | 480 | 20 |
| B | 1760 | 1240 | 480 | 20 |

A/B 的 proof.json 均记录仅一份 service.c、drivers=[]、提取 archive 成员为空、
无未解析引用、无 resident 重定义。模块自有全局仅 module_entry 和
module_base_binding；静态 ops/计数属于模块 DRAM。实际 SDK 基座 nm 已检查存在
所有所需全局符号；未仅凭头文件猜测。私有输出目录
`$PRIVATE_OUTPUT_ROOT/service-authoring/{A,B}/service.bin`，完整导入表、SHA、
敏感字节审计和行数在同目录上层 audit.json；每版各有 module-link/proof.json。
基座 SHA 构建前后均为
`48a8d97b734f585156f811cb54e66bc9b78f18b7d395bb50f3a28df907d787c0`。

验证：现有 CTest 20/20、新 manifest 测试 11/11、实际 service.c 的 A/B 两次
ASan+UBSan 原生测试通过；默认完整业务也对同一生产 ELF 成功重建。
本机测试覆盖认证拒绝、重复/未知/NUL/超长/schema/转义、计数重置和借用期。
已实测 Wi-Fi 更新 A→B，原样回显/ASCII 大写行为分别正确，状态重新从 requests=1、echoes=0 开始；错误口令、129 字节文本和不支持操作被拒绝，拒绝请求未增加计数。错误底座绑定的镜像也被拒绝。更新提交后、重启前服务不可用（可能 TCP reset，不保证总能收到 JSON 错误），重启后才激活。底座 ELF/bin/bootloader/分区表的构建产物 SHA 前后相同，全程未串口刷底座。

随后已恢复原 UART/SPI 模块。首次 SPI 恢复复测出现全 FF，不能误写成当时已正常；用户换线后，另一次 A→B→同一 SPI 模块恢复在三档速率各 100 次通过。该情况正在按独立 SPI 故障调查处理，不能归因于服务更新或断言只是线。详见 [SPI 调查](SPI_INVESTIGATION.md)。

编写难度按维度评估：套用现有 manifest/模板写纯 JSON 服务为低到中；裸写协议、鉴权边界和生命周期仍有成本。新硬件驱动服务需要 ESP-IDF/驱动状态/中断与内存预算知识，不能由本例证明其同样容易。Wi-Fi 初始化与外设驱动可不写，但协议层、SDK 构建和二进制绑定仍强耦合。

## 耦合评估：可换业务，不等于稳定 SDK

**高耦合点仍存在：**

- module_abi.h 内嵌 ROM 类型与强制 ROM 回调，非 ROM 服务也需写不支持桩。
- `authenticated/socket_exact/reply/failure/esp_timer_get_time/cJSON_*` 均通过
  **ELF 全局符号名**导入，不是正式 host 函数表。auth 与原始网络 fd/JSON
  解析绑在一起，不是消息帧抽象。cJSON 节点布局也属于二进制耦合。
- 必须使用完全相同的 base ELF、SDK 编译配置、真实地址及哈希；复用
  compile_commands 带入庞大 SDK include 路径，包括旧私有工程路径。
  这不是模块源码独立于 SDK/基座的通用可移植接口。
- fd/host/JSON 的所有权与生命周期是隐式约定：本例同步完成、不保存借用 fd，
  复制业务文本后释放 parse tree；依赖基座控制任务串行调用，不宣称线程安全。
- 管理固件仍保留 STM32 名称、target/staging 等与 hello 无关的概念；概念上尚未
  完全泛化，本次不改基座、不增加 host API。

**现有非必要配置耦合与打包风险：** 主代理已确认旧 business.c 的
configuration_valid 对 CONFIG_ROM_SERVICE_TOKEN/WIFI_SSID/WIFI_PASSWORD
做 strlen 校验，使当前 SPI 业务镜像嵌入真实 token/password；这是不必要的
业务配置耦合。本例不复制该 guard、不修改不归本任务所有的 business.c。
对生产 sdkconfig.h 的真实 token/password 仅在内存中取得并搜索 A/B 字节，
只记录字段名和 present=false，绝不打印值；A/B 均未包含它们。
生产基座/完整业务镜像仍必须私有；示例目录不放编译产物，不能由 hello 的
“未包含口令”推导所有旧模块都可以公开。

## English summary

One C source builds A (identity echo) and B (ASCII-only uppercase) using validated
integer manifest defines. No base change, new host API, drivers or networking
initialization. Exact-ELF global imports and borrowed socket/JSON contracts remain
high coupling, not a decoupled message-frame SDK. Native stubs are UNIT ONLY;
This service was cross-linked and physically updated A→B over Wi-Fi, with behavior/state/authentication tests passing and resident build hashes unchanged. The original UART/SPI module was restored. Its intermittent all-FF SPI observation is investigated separately and must not be attributed conclusively to wiring or service code; see SPI_INVESTIGATION.md.
