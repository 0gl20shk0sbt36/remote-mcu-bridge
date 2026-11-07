# 安全政策 / Security Policy

## 中文

### 适用范围与报告

本项目是开发与实验用途的远程 MCU 桥接器，不是经过安全认证的产品。目前没有承诺长期维护的版本系列或漏洞响应时限。

如果托管平台为此仓库启用了 **Report a vulnerability / 私密漏洞报告**，请优先使用该渠道。未启用时，先提交不含敏感信息的 issue，请求维护者建立私密报告渠道；不要在公开 issue、PR 或讨论中附上利用步骤、可执行恶意镜像、口令、令牌或私有设备地址。请提供受影响提交、组件、影响描述、脱敏复现条件与建议修复；等待私密渠道后再提供敏感细节。此政策不虚构安全邮箱或保证已有私密渠道。

### 威胁模型与限制

- 管理连接是明文可信局域网协议，共享令牌不是加密。监听者可能获取令牌和上传数据；令牌不能替代 TLS 或网络隔离。
- 不要公网暴露、端口转发或与不可信设备共享广播域。必要时通过受控的加密隧道和防火墙访问；隧道不改变设备端协议本身。
- 模块使用底座运行环境及权限，不是沙箱。恶意或错误模块可能破坏内存、占用外设、阻断调度或造成设备复位。只安装可信构建来源的模块。
- SHA-256、格式校验与精确底座构建绑定用于完整性和兼容性，不是发布者身份认证或数字签名。
- 单槽更新的提交标记和启动许可锁存不是 A/B 回滚，也不保证任何硬件在任意掉电时刻都能恢复。真实断电、Flash 故障与严重异常需要单独测试。
- 默认关闭网络服务与模块运行。公开的启用配置是 compile-only，占位令牌是公开字符串，绝对不能用于部署。

### 部署与泄露处置

使用独立且强随机的本地令牌，最小化网络访问范围，核对板卡与电压，并保留不依赖网络的串口/调试恢复方法。真实配置、生成的 `sdkconfig`、转储与日志可能含有秘密；分享前脱敏，示例仅使用 `DEVICE_IP` 等符号或环境变量。

若凭据泄露，先隔离设备并轮换令牌/网络凭据，检查上传模块与固件来源，必要时通过可信本地恢复路径重新刷写。删除公开文本不能使已泄露凭据重新安全。

本文不声明已完成安全审计、渗透测试、CI 或实机验证。[MIT 许可证](LICENSE) 的无担保条款适用。

---

## English

### Scope and reporting

This is a development/experimental remote MCU bridge, not a security-certified product. No long-term supported release series or vulnerability-response deadline is currently promised.

If the hosting platform enables **Report a vulnerability / private vulnerability reporting** for this repository, use it first. Otherwise, open a non-sensitive issue asking maintainers to establish a private reporting channel. Do not publish exploit instructions, executable malicious images, passwords, tokens, or private device addresses in issues, PRs, or discussions. Include the affected revision/component, impact, sanitized reproduction conditions, and a proposed fix; send sensitive details only once a private channel exists. This policy does not invent a security email address or imply that a private channel is already enabled.

### Threat model and limits

- Management is a plaintext trusted-LAN protocol. A shared token is not encryption; observers may recover the token and uploaded data. Tokens do not replace TLS or network isolation.
- Never expose it publicly, forward its ports, or share its broadcast domain with untrusted devices. Use controlled encrypted tunnels and firewalls where necessary; tunneling does not change the device protocol itself.
- Modules share the base runtime and privileges; they are not sandboxed. Malicious or faulty modules may corrupt memory, seize peripherals, block scheduling, or reset the device. Install only modules from trusted builds.
- SHA-256, format checks, and exact base-build binding provide integrity/compatibility checks, not publisher authentication or digital signatures.
- Single-slot commit markers and boot-latched permissions are not A/B rollback or a guarantee of recovery at every physical power-loss point. Real power cuts, Flash failures, and severe faults require separate tests.
- Network service and module execution are disabled by default. Public enabled configurations are compile-only; placeholder tokens are public strings and must never be deployed.

### Deployment and incident handling

Use a distinct strong random local token, minimize network access, verify board wiring and voltage, and retain a serial/debug recovery path independent of networking. Real configuration, generated `sdkconfig`, dumps, and logs may contain secrets: sanitize before sharing, and use symbols such as `DEVICE_IP` or environment variables in examples.

If credentials leak, isolate the device, rotate tokens/network credentials, investigate uploaded modules and firmware provenance, and reflash through a trusted local recovery path if needed. Removing public text does not make exposed credentials safe again.

This policy does not claim a completed security audit, penetration test, CI run, or hardware validation. The [MIT license](LICENSE) warranty disclaimer applies.
