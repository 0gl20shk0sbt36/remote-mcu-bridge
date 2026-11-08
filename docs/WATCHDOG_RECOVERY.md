# 服务看门狗与重连诊断

## 当前验证范围

2026-11-08 已完成 ABI4 原生、交叉构建和独立实机验收：5 秒角色停止喂狗、1 毫秒预算作业迟到、入口返回失败、入口不返回触发 TWDT、关闭中断后触发 IWDT。五种情况均复位到隔离管理态，能重连读取 RTC 记录；反复读取不清除，错误序号被拒绝，正确 ACK 不自动重启故障服务。入口卡死/IRQ 卡死分别读到硬件复位原因 6/5。见 [实机记录](validation/abi4-hardware.md)。未知 offender/age 仍明确未知；没有做断电保留测试。

## 谁负责什么

底座配置看门狗、执行时限、保留故障信息；服务只使用公共 SDK 的 watchdog_open / watchdog_feed / watchdog_close。喂狗是任务上下文操作，不供 ISR 调用。

- 初始化时服务创建一个根角色，正常事件/空闲循环明确喂它，根角色不能关闭。
- STM 服务另为实际控制连接和 UART 会话创建不同角色。只有对应的实际执行、有限等待循环或同一个受监督 ROM 作业可以喂该角色；健康的根循环不能掩盖卡死的连接。
- 正常没有客户端不算故障。连接结束后关闭其临时角色，不继续等待它喂狗。
- 角色超时后不能靠迟到的 feed/close 恢复。底座记录故障并复位，TWDT/IWDT 保护底座监视任务、调度和中断失去执行机会的情况。
- 业务入口失败时可能已经创建任务/ISR，不能假装原地卸载。底座锁存故障并重启，下一次仅开放管理恢复；更新/隔离启动不会启动需要业务喂狗的角色。

接口接受有界超时，具体角色值以服务源码为准。当前 STM 根与 UART 角色为 5000ms，控制角色为 10000ms。超时是故障检测策略，不是外设实时延迟保证。

## 故障记录

RTC NOINIT 记录带 magic、CRC、故障序号和来源信息；不在每次 feed 时写 Flash。它保留最近的待确认故障，不是无限日志队列，不承诺完整断电保留或任意断点的原子写入。

get-fault 返回：

- seq：确认该条故障所需的序号；reason 非零代表待确认。
- reason=1：服务角色超时；role / lease / checkpoint 给出最后已知执行位置。
- reason=2：异常复位的重建记录，可能为硬件看门狗或 panic，具体看 fault_reset_reason。它不证明芯片存在物理故障。
- reason=3：通用作业或入口的绝对时限超时；reason=4：入口返回失败。两者都在软件复位前写入同一序号日志，role 区分 module-job / module-entry。这些情况下 age_ms 是作业已耗时，last_feed_us 是最后一次真实 progress 的时间；没有进度时 last_feed_unknown=true。
- last_feed_us / timeout_ms / age_ms：来源启动周期中的信息，不应和新启动的单调时钟相减。
- last_feed_unknown / age_unknown / offender_unknown：硬卡死来不及捕获时明确标注未知；最后喂狗角色不是硬卡死肇事者的证明。
- boot / reset_reason / fault_reset_reason：故障来源启动与结束原因；current_boot / current_reset_reason 是当前管理启动，不能覆盖来源证据。

读报告不清除。新的异常产生新序号，旧确认不能清掉新事件。序号耗尽时不回绕接受旧确认。

## 重连报告：保持服务端模型

PC 重连后先鉴权，再查询管理端口 3337；status 的 fault_pending 提示存在待确认报告。设备没有外联推送客户端，没有客户端时仅保留记录。

以下变量由操作者在本地配置；不要把 token 放进命令行或提交到仓库：

```sh
python3 tools/module_ctl.py status --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/module_ctl.py get-fault --host "$DEVICE_IP" --token-file "$TOKEN_FILE"
python3 tools/module_ctl.py ack-fault --host "$DEVICE_IP" --token-file "$TOKEN_FILE" --fault-seq "$FAULT_SEQ"
```

FAULT_SEQ 必须来自实际 get-fault 的 seq。缺失、非整数、过期或不匹配序号被拒绝。确认报告本身不会恢复故障业务；clear-fault 是另外的明确操作，仍受单次初始化与本轮启动权限约束。修改/修复服务则按 enter-update → 重启 → 上传 → 提交 → 重启执行。

协议仍为可信局域网明文鉴权，不是 TLS。不得暴露互联网。
