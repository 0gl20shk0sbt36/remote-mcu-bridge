# 贡献指南 / Contributing

## 中文

欢迎提交可复现的缺陷报告、测试、文档和代码改进。安全漏洞请遵循 [SECURITY.md](SECURITY.md)，不要公开令牌、可利用细节或私有设备信息。

### 开发流程

1. 从独立分支开始，说明问题、范围及不包含的工作。保持变更小而完整。
2. 在仓库根目录执行原生检查：
   ```sh
   cmake --preset native
   cmake --build --preset native
   ctest --preset native --output-on-failure
   ```
3. 修改 ESP 代码时激活 ESP-IDF **5.5.5**，执行：
   ```sh
   python3 tools/rmb.py backends
   python3 tools/rmb.py boards
   python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103
   python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103
   ```
4. 如适用，分别检查服务/模块关闭的安全默认配置和公开占位值启用的 **compile-only** 配置。不要把占位配置烧录到设备，也不要提交真实 `sdkconfig`、网络口令或令牌。
5. **推送前先在本地复演 CI**（与 `.github/workflows/ci.yml` 同一套步骤、同一个剥离环境）：
   ```sh
   IDF_PATH=... IDF_TOOLS_PATH=... ./tools/ci_rehearsal.sh all   # 或 native / esp-idf
   ```
   它会在没有 `IDF_PATH`、没有 `~/tmp`、PATH 最小的条件下跑完原生套件与两个 ESP-IDF 档；失败就不要推送。托管 runner 上没有容器环境时，这是唯一能提前发现“本地能过、CI 不过”的手段。
6. 在 PR 中列出准确命令、工具版本、返回结果与未执行的检查。未执行应写“未执行”，不能写通过。构建、模拟、CI、实机及实际断电测试是不同证据。

### 代码与接口约束

- 核心 C11 代码保持无 ESP-IDF、FreeRTOS、网络或板级头文件依赖。后端将平台错误映射到 `bridge_error`。
- 不要把业务 UART/SPI/GPIO 接口塞进通用更新算法。存储准入和同步回调不得执行上传模块的入口。
- 保持启动写许可锁存、独立提交标记区、先完整验证再提交、更新跨整机复位的语义。不要隐式添加 A/B 槽、持久模式标志或热卸载。
- 针对短读写、边界、碎片化写入、回调错误与提交失败补充测试。修改 ABI、镜像格式、几何或协议时同步文档与兼容性说明。
- 只存在一个实际 ESP-IDF 后端；原生模拟不算部署后端，模板不算实现。新增后端按 [移植指南](docs/BACKEND_PORTING.md) 提供独立证据。
- 中文文档在前，完整英文在后。示例使用 `DEVICE_IP` 等符号或本地环境变量，不包含个人路径、地址、账号或凭据。

### 提交检查清单

- [ ] 说明行为变化、兼容性和安全影响。
- [ ] 测试覆盖生产算法而不是仅测试复制实现。
- [ ] 列出本地实际结果；硬件测试缺失处明确说明。
- [ ] 文档、注册表和 CLI 示例一致，未宣称不支持的后端可用。
- [ ] 清除凭据、个人路径、构建产物及无关变更。

提交的原创贡献按本项目 [MIT 许可证](LICENSE) 提供。保留第三方版权与许可证，不要将外部 SDK 或代码重新标记为本项目原创。

---

## English

Reproducible bug reports, tests, documentation, and code improvements are welcome. Follow [SECURITY.md](SECURITY.md) for vulnerabilities; do not publicly disclose tokens, exploit details, or private device information.

### Development workflow

1. Start an independent branch and describe the problem, scope, and exclusions. Keep changes focused and complete.
2. Run the native checks from the repository root:
   ```sh
   cmake --preset native
   cmake --build --preset native
   ctest --preset native --output-on-failure
   ```
3. For ESP changes, activate ESP-IDF **5.5.5** and run:
   ```sh
   python3 tools/rmb.py backends
   python3 tools/rmb.py boards
   python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103
   python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103
   ```
4. Where applicable, separately check the service/module-disabled safe configuration and the public placeholder-enabled **compile-only** configuration. Never flash placeholders or commit real `sdkconfig`, network passwords, or tokens.
5. **Rehearse CI locally before pushing** (same steps as `.github/workflows/ci.yml`, same stripped environment):
   ```sh
   IDF_PATH=... IDF_TOOLS_PATH=... ./tools/ci_rehearsal.sh all   # or native / esp-idf
   ```
   It runs the native suite and both ESP-IDF profiles with no inherited `IDF_PATH`, no `~/tmp` and a minimal PATH. If it fails, do not push. Without a container runtime this is the only way to catch "passes here, fails on the runner" early.
6. Record exact commands, versions, actual results, and checks not run in the PR. Say “not run” rather than “passed” when appropriate. Compilation, simulation, CI, hardware execution, and physical power-cut testing are different evidence.

### Code and interface constraints

- Keep portable C11 code free of ESP-IDF, FreeRTOS, networking, and board headers. Map platform errors to `bridge_error` in the backend.
- Keep business UART/SPI/GPIO APIs out of the generic updater. Storage admission and synchronization callbacks must not execute uploaded module entry points.
- Preserve boot-latched write permission, an independent commit-marker region, validation before commit, and full resets across updates. Do not silently introduce A/B slots, persistent mode flags, or hot unloading.
- Add coverage for short I/O, bounds, fragmented writes, callback errors, and failed commits. Document compatibility when changing ABI, image format, geometry, or protocol.
- ESP-IDF is the sole implemented deployment backend. Native simulation is not a deployment backend; templates are not implementations. New ports need independent evidence under the [porting guide](docs/BACKEND_PORTING.md).
- Put complete Chinese documentation before complete English. Use symbols such as `DEVICE_IP` or local environment variables, not personal paths, addresses, accounts, or credentials.

### Submission checklist

- [ ] Explain behavior, compatibility, and security impact.
- [ ] Test production algorithms rather than only a copied implementation.
- [ ] Report actual local results and explicitly identify missing hardware tests.
- [ ] Keep documentation, registry, and CLI examples aligned without unsupported-backend claims.
- [ ] Remove secrets, personal paths, build outputs, and unrelated changes.

Original contributions are provided under the project [MIT license](LICENSE). Preserve third-party copyright and license notices; do not relabel external SDK code as original project code.

## 不纳入版本控制的内容

- 实机与诊断记录（串口/Wi-Fi 日志、JSON 证据、波形、抓包）、固件镜像与构建产物、私有凭据：都属于本地开发产物。**不要提交，也不要放进交付包**；需要长期留存的放进项目内已被忽略的 `.internal/`（例如 `.internal/evidence/`、`.internal/notes/`）。
- 仓库只保留可复述的结论、复现命令与限制条件（见 `docs/TESTING.md`）。Git 历史本身就是变更记录，不要用"日期命名的文件"代替提交历史。

## What stays out of version control

- Hardware and diagnostic records (serial/Wi-Fi logs, JSON evidence, waveforms, captures), firmware images, build output and private credentials are local development artifacts: **do not commit them and do not ship them**. Keep long-lived copies inside the ignored `.internal/` directory (for example `.internal/evidence/` and `.internal/notes/`).
- The repository keeps reproducible conclusions, commands and limitations (`docs/TESTING.md`). Git history is the change record; dated files are not a substitute for commits.
