---
name: "缺陷报告 / Bug report"
about: "可复现的软件或文档问题 / Reproducible software or documentation problem"
title: ""
labels: ""
assignees: ""
---

## 中文

请勿公开安全漏洞细节或凭据；请先阅读 [安全政策](../../SECURITY.md)。删除个人路径、地址、账号、网络名、令牌和日志中的秘密，设备地址使用 `DEVICE_IP`。

### 问题与预期

- 实际行为：
- 预期行为：
- 最小复现步骤与准确命令：

### 环境与证据

- 提交/版本：
- 后端与板卡（默认 `esp-idf` / `esp32c3-stm32f103`）：
- 操作系统、Python、CMake、编译器、ESP-IDF 版本：
- 配置类型（关闭基线 / 占位启用 compile-only / 脱敏本地部署）：
- 脱敏命令输出、退出码：
- 实际执行：原生测试 [ ]、ESP 编译 [ ]、CI [ ]、硬件 [ ]、物理断电 [ ]。
- 未执行的检查及原因：

编译/模拟不能替代实机证据。不要烧录公开占位配置；不要将模板当作支持的后端。

---

## English

Do not disclose vulnerability details or credentials; read the [security policy](../../SECURITY.md) first. Remove personal paths, addresses, accounts, network names, tokens, and log secrets. Use `DEVICE_IP` for device addresses.

### Problem and expectation

- Actual behavior:
- Expected behavior:
- Minimal reproduction and exact commands:

### Environment and evidence

- Revision/version:
- Backend and board (default `esp-idf` / `esp32c3-stm32f103`):
- OS, Python, CMake, compiler, ESP-IDF versions:
- Configuration (disabled baseline / placeholder-enabled compile-only / sanitized local deployment):
- Sanitized output and exit codes:
- Actually executed: native tests [ ], ESP build [ ], CI [ ], hardware [ ], physical power cut [ ].
- Checks not run and why:

Compilation/simulation is not hardware evidence. Never flash public placeholders or treat templates as supported backends.
