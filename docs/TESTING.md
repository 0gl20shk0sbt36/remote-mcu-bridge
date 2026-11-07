# 验证 / Verification

## 中文

### 本地执行结果（2026-11-07）

- 整理后的源码正本：**19/19 CTest 通过**，包含原工程除 `apps_f103` 外的 15 项适用主机测试，以及注册表、客户端、发现和 probe 四项。最后完整运行在客户端竞争修复后执行。
- 更新核心的严格 GCC / ASan / UBSan 检查通过：270 个场景、225765 次断言、179 次注入故障；覆盖回读错误、部分擦写、分片、对齐、不可变启动许可和提交失败。模拟摘要只测试回调/范围/比较，不冒充密码学实现验证。
- 修复存储回调返回 `BRIDGE_E_STATE` 时被误当作标记内容不匹配的问题；现在回调状态和读取结果分开，任何回读错误都不能授予更新或执行许可。
- 客户端完整套件 17/17 通过；正常关闭及早期握手中断两个 Ctrl-C 场景各重复 50 次，共 100/100 通过。
- 从已有归档恢复并校验 ESP-IDF **v5.5.5** 与 RISC-V GCC **esp-14.2.0_20260121 / 14.2.0**，Python 依赖安装在独立虚拟环境，没有全局安装。
- 通过统一后端入口完成 `safe` 和 `compile-enabled` 两个配置的真实本地 ESP32-C3 编译；生成配置的启用状态和固定板级引脚均通过校验。
- 业务模块对启用版底座 ELF 的真实独立链接、镜像头和精确底座哈希绑定检查通过；无未解析符号、无重复底座定义。链接检查使用合成取指地址，产物明确标记 **NOT DEPLOYABLE**，不是可烧录或已运行的业务模块。
- 私有工作树的生产配置底座也已重新构建。生产凭据、固件及旧开发历史不进入这份干净源码仓库。

**没有连接或操作板子，没有新版本实机回归，也没有真实写入中途断电验证。GitHub 发布与远程 CI 按用户要求暂停。** 历史版本的实机结果不能代替本轮验证。

### 原生复现

```sh
python3 tools/native_deps.py
cmake --preset native
cmake --build --preset native
ctest --preset native --output-on-failure
```

`native_deps.py` 只获取固定提交 `b2890c8d76bbb64e710585ebc0a917196b9c67e7` 的 cJSON，不下载完整 SDK。已有依赖可以通过 `RMB_CJSON_PATH` 或 `IDF_PATH/components/json/cJSON` 使用；`--cache` 接受本地 Git 对象库。

激活 ESP-IDF 5.5.5 环境后，可复现无需硬件的交叉编译：

```sh
python tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 --profile safe
python tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 --profile compile-enabled
python tools/check_module_link.py --base-elf out/esp-idf/esp32c3-stm32f103/compile-enabled/stm32_rom_wifi.elf
```

`safe` 关闭服务，`compile-enabled` 使用公开占位凭据，只用于编译；不要烧录占位配置。首次本地构建的缺失 `ESP_ROM_ELF_DIR` 提示只影响 GDB 初始化文件生成，命令退出成功，不是编译或模块链接失败；本地源码正本最后构建已补全该变量，两个配置均无此错误提示。

### 测试边界与竞争修复

原生测试使用本地回环 socket 和模拟 I/O，不验证真实 Flash 时序、接线、无线链路或硬件看门狗。`apps_f103` 依赖未随本仓提供的旧 CubeMX 工程，明确排除；ROM E2E 的可选真实 CubeMX 镜像子检查未执行，合成镜像的 TCP/UDP/C 核心路径执行。

Ctrl-C 的旧测试在服务端发出 ACK 后立即中断客户端，不能保证客户端已读 ACK，因而可能正常地产生 TCP reset。修复后的正常关闭测试等待真实 CLI 经 stdin 回传标记，证明 ACK 已被处理，再发送 SIGINT，仍严格要求 EOF 和退出码 130。独立早期握手中断测试只在该明确场景接受 EOF 或 `ConnectionResetError`，不放宽其他错误。最后完整 CTest 同样通过。

板卡 JSON 校验唯一受支持板卡的固定接线，不会自动生成新板实现。网络、任务/队列、XIP 加载与实际看门狗服务仍属于 ESP 适配；不能把通用接口声明说成已经接入了所有运行路径。其他后端尚未实现。

## English

### Local results (2026-11-07)

- The clean source tree passed **19/19 CTest entries after the client-race fix**: 15 applicable original host fixtures (excluding `apps_f103`), plus registry, client, discovery and probe tests.
- Strict GCC / ASan / UBSan checks passed for the update core: 270 scenarios, 225765 assertions and 179 injected failures. The mock digest tests callback ranges and comparisons, not cryptographic correctness.
- Fixed a fail-closed bug where a storage callback returning `BRIDGE_E_STATE` was confused with an observed footer mismatch. Callback failures now deny both update and execution permission.
- All 17 client tests passed; the normal-close and early-handshake Ctrl-C cases each passed 50 repetitions (100/100 total).
- Restored and checksum-verified ESP-IDF **v5.5.5** and RISC-V GCC **esp-14.2.0_20260121 / 14.2.0**. Python requirements use an isolated virtual environment, not global installation.
- Both `safe` and `compile-enabled` ESP32-C3 builds passed locally through the common backend CLI, including generated configuration and fixed-wiring validation.
- The business module completed a real independent link against the enabled resident ELF; header and exact base-hash binding validation passed, with no undefined symbols or duplicate resident definitions. The check uses a synthetic instruction address: its outputs are explicitly **NOT DEPLOYABLE**, not runtime validation.
- The private production-configured resident was also rebuilt. Production credentials, firmware and old development history are excluded from this clean source repository.

**No board connection, flashing, new hardware regression, or physical mid-write power cut was performed. Publication and remote CI are paused at the user's request.** Historical hardware results do not validate this revision.

### Reproduction and limits

Use the native and ESP commands in the Chinese section above. `native_deps.py` retrieves only pinned cJSON; `RMB_CJSON_PATH`, `IDF_PATH` or its `--cache` option support existing dependencies. Activate ESP-IDF 5.5.5 before cross-compiling. The safe profile disables service; compile-enabled uses public placeholder credentials and must not be flashed. An unset `ESP_ROM_ELF_DIR` caused nonfatal GDB-init generation messages in the first local builds, not compilation/link failure; the final builds from the canonical source directory exported it and emitted no such errors.

Native fixtures use loopback sockets and simulated I/O. They do not establish physical Flash timing, wiring, Wi-Fi or hardware-watchdog behavior. The unpublished CubeMX application and optional actual-image E2E subcheck are excluded; synthetic TCP/UDP/C-core paths ran.

The old Ctrl-C fixture signalled immediately after server ACK transmission, before proving client consumption; cancellation could legitimately produce a TCP reset. The normal-close fixture now waits for a real CLI stdin marker forwarded only after ACK validation, then requires EOF and exit code 130. A separate deliberately early cancellation accepts only EOF or `ConnectionResetError` in that explicit case. The final complete CTest run also passed.

Board JSON validates one fixed supported wiring and does not generate new implementations. Networking, tasks/queues, executable XIP mapping and actual watchdog services remain ESP adapter responsibilities; declared contracts are not fully integrated runtime paths. No second deployment backend is implemented.
