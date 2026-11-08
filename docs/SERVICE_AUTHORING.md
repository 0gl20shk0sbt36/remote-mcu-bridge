# 新业务模块编写 / Service authoring — ABI 4

操作步骤请先看 [服务编写与部署手册](USER_GUIDE.md)；维护入口见 [开发手册](DEVELOPMENT.md)，默认 STM 业务见 [STM ROM/UART 手册](DEFAULT_STM_FIRMWARE.md)。本页保留 SDK 深入合同和历史证据，不是一步安装脚本。

## 范围与证据

本页描述 ABI4 契约；构建/原生验证不等于硬件验收。2026-11-08 的独立 ABI4 验收见 [实机记录](validation/abi4-hardware.md)。ABI3 的历史结果单列于文末，不混作 ABI4 证据。ABI4 故意拒绝 ABI3 二进制，必须针对准确的新基座 ELF 重建。

## 公共 SDK：`include/rmb/service.h`

公共头仅依赖 `stdint.h`、`stddef.h`、`stdbool.h`，不包含 ROM、ESP-IDF 或 socket 头。以下字段顺序和签名是 ABI，不可自行调整：

```c
#define RMB_SERVICE_ABI 4u
#define MODULE_ABI_VERSION RMB_SERVICE_ABI
#define RMB_NET_AUTH_CONSUME 0u
#define RMB_NET_AUTH_REPLAY 1u
#define RMB_IO_ERROR (-1)
#define RMB_IO_AGAIN (-2)

typedef int32_t rmb_connection;
typedef void (*rmb_connection_fn)(rmb_connection, void *);
typedef int (*rmb_job_fn)(void *);

typedef struct {
    uint32_t abi, size;
    int (*listen)(uint16_t port, uint32_t flags,
                  rmb_connection_fn handler, void *context);
    bool (*exact)(rmb_connection, void *buffer, size_t size,
                  bool writing, int64_t deadline_us);
    int (*read)(rmb_connection, void *buffer, size_t size, bool nonblocking);
    int (*write)(rmb_connection, const void *buffer, size_t size, bool nonblocking);
    bool (*authenticated)(const char *candidate);
} rmb_net_api;

typedef struct {
    uint32_t abi, size;
    const rmb_net_api *net;
    int64_t (*now_us)(void);
    void (*progress)(void);
    int (*run)(rmb_job_fn function, void *context, int64_t timeout_us);
    int (*watchdog_open)(const char *role, uint32_t timeout_ms);
    int (*watchdog_feed)(int lease, uint32_t checkpoint);
    int (*watchdog_close)(int lease);
} rmb_host;

typedef int (*module_entry_fn)(const rmb_host *);
```

新服务使用 `rmb_host`，不恢复旧 ops 表。没有 `module_ops`、dispatch 编号、必填 ROM 桩、device/staging 指针。镜像头及 32 字节 ELF-binding 布局保留，仍强绑定准确 ELF 哈希和地址；这不是跨 CPU/SDK/基座的二进制可移植性承诺。

## 初始化与监听

`int module_entry(const rmb_host *host)` 校验 host/net 的 ABI 与 size，初始化本服务正常驱动/任务，注册监听，成功返回 0。hello 是普通服务：仅注册 TCP **3333**，不初始化 Wi-Fi、不导入 raw socket、不注册 3336、不实现 ROM 不支持桩。A 原样 echo，B 只将 ASCII a–z 大写，UTF-8 其他字节不变。业务 schema、JSON 转义、计数和内存所有权仍由 hello 负责。

`listen` 成功返回 0，失败返回负数。服务自行选择 3333/3336；3337 是基座管理端口，3335 是基座日志端口，禁止服务占用。最多 4 个监听，有界基座连接槽；每监听一个 worker，同一监听回调串行，不同监听可并发。业务回调绝不在管理任务上执行，不要假设跨监听共享状态天然串行。

监听 worker 栈由 `CONFIG_RMB_LISTENER_STACK_BYTES` 配置，默认 **12288 字节/监听**；ESP-IDF 此参数单位是字节，不是原版 FreeRTOS 的 words。预算包含业务回调、SDK、日志和 libc 的完整嵌套调用链。回调返回后 `rmb-net` 记录 `stack_min_free_bytes` 最小余量；调栈配置也会改变底座 ELF，因此必须重建绑定模块。原 6144 字节在实机 STM 请求日志链中触发栈保护；仅看业务函数自己的静态栈帧不够。

仅 INIT 阶段可注册；非法端口、flags、重复端口或不在已准入模块镜像内的回调指针必须拒绝。入口完整成功后才打开 admission/ready gate；部分初始化不得留下可调用业务回调。失败初始化进入恢复/整机复位，不热重入。

## 认证与 AUTH_REPLAY

所有连接在回调前由基座校验首个 LF 结束的 JSON 对象，要求恰有一个字符串 `token` 字段；其他字段允许属于业务协议，基座不解释 `op` 或 STM 操作。基座限制首行长度、token 长度及认证时间，拒绝重复 token、原始/解码 NUL、畸形 JSON 和错误令牌。

- `RMB_NET_AUTH_CONSUME`：丢弃认证行，业务从后续字节开始读。
- `RMB_NET_AUTH_REPLAY`：将认证首行的**原始完整字节**作为业务最先读到的数据，不重排或重新编码 JSON，兼容 token/op 同行的旧单请求协议。
- 基座不额外注入 ACK，避免破坏现有协议。
- `net->authenticated(candidate)` 仅供旧服务做纵深校验，不向服务提供秘密；普通服务无需重复认证。hello/STM 的旧单请求协议选择 REPLAY。

示例请求仍是一行 JSON：

```json
{"token":"<securely supplied>","op":"service-info"}
{"token":"<securely supplied>","op":"echo","text":"hello 世界"}
```

## 借用连接与 I/O

`rmb_connection` 是 **int32_t opaque borrowed handle，不是 OS fd**。仅当前回调内有效，不能保存供以后使用、传给 socket API 或自行 close；回调返回后由基座关闭。基座使用 generation-bound slot ID，每次读写检查 generation 和 active 状态，过期 handle 不得访问后来复用的连接槽。

read/write 允许短传输：正数是实际字节数；read 的 0 是 EOF；负数为错误，`RMB_IO_AGAIN` 表示暂时不可用。nonblocking 路径必须正确处理 EAGAIN/EINTR。`nonblocking=false` 也仅允许适配器规定的有限等待，不承诺无限等到填满 buffer；需要完整传输和明确期限时使用 `exact`。`exact` 处理短读写，`writing` 指定方向，使用 `now_us()` 同一单调时钟上的**绝对**微秒期限，完成返回 true，否则 false；不能在每次短传输后重置期限。不得依赖 write(0) 表示 read EOF。

## 服务存活

入口中注册根看门狗角色，服务实际的事件/空闲循环喂该角色；活动中的关键控制/UART 回调分别注册和维护自己的角色，避免健康的空闲任务掩盖卡死的业务。临时角色在回调返回时关闭，根角色持续到重启；已超时角色不能靠迟到的 feed/close 续命。hello 的 `platform_idf.c` 是普通 FreeRTOS 空闲心跳任务，不喂业务回调角色。

看门狗由底座配置和判定，服务通过公开 SDK 显式喂狗；接口仅供任务上下文使用。普通驱动/中断依然按 SDK 方式使用，不新增 cache-off ISR 执行框架。详见 [看门狗与诊断](WATCHDOG_RECOVERY.md)。

## 长任务监督

`host->run(function, context, timeout_us)` 在受监督 worker 中执行准入镜像内的函数，限时不超过 1800 秒。context 是借用数据，调用方必须保持有效直到工作结束或整机复位；禁止超时返回后仍有后台任务持有调用栈指针。长操作调用 `progress()`；watchdog 独立于管理网络任务。嵌套 run 必须安全内联或明确拒绝，不能等待自身 worker 导致死锁。

基座只认识 `SJ_INIT/SJ_CALL/SJ_HANG`，不认识 probe/download 等 STM 操作；STM 旧状态响应由 STM 服务自行组装，不把 host 状态直接当作 STM wire response。

## 构建与验证边界

保留 MEMPROT+LOCK、固定分区/footer 和不可变的启动期更新许可。运行中失效 footer 后整机重启；只有无服务执行的更新启动才能擦写镜像体；校验提交后再次重启，入口每启动仅执行一次，不热卸载。footer 失效本身是 Flash 操作，NVS 也可能造成普通 IRQ 延迟；本次不增加特殊 cache-off ISR 子系统。

manifest 的 sources/drivers/整数 defines 选择服务代码；hello 不需要 UART/SPI 驱动。使用 `esp32_rom/module/build_module.py` 和准确的 ABI4 base ELF、真实预留 text/data 地址构建；不要复制历史地址。保留精确导入、唯一运行时、GP/孤儿段/边界及 SHA 检查。synthetic 地址的链接结果只是 compile proof，**不可部署**。

原生测试应覆盖认证、REPLAY 原字节、短 I/O、绝对期限、过期 handle、初始化 gate、监听并发和监督超时。交叉构建证明编译/链接；native mock 证明对应路径；两者均不能证明 Wi-Fi 更新、实际 Flash/WDT 或目标 UART/SPI 行为。当前 ABI4 验证结果以本轮实际构建/测试记录为准，本页不预报通过数量。

## 历史 ABI3 实机记录（不是 ABI4 验收）

以下是迁移前的独立记录，不作为 ABI4 证据；哪些操作已在 ABI4 重跑，以新版实机报告为准：

- ABI3 hello 使用必填 ROM_E_CAP 桩、dispatch 1 借用 `int *fd` 和静态 ops；这些是旧接口，不是新服务模板。
- 当时 A/B 镜像分别 1740/1760 字节；text 1220/1240，data 均 480，bss 均 20。原 service.c 87 行、test_service.c 60 行，manifest 测试 217 行；builder 269→371 行。这些数值不是 ABI4 当前大小/行数。
- 当时 CTest 20/20、manifest 11/11、A/B ASan+UBSan 原生测试通过；同一生产 ELF 重建完整旧业务成功。历史基座 SHA 为 `48a8d97b734f585156f811cb54e66bc9b78f18b7d395bb50f3a28df907d787c0`，不能用于 ABI4 重建。
- 当时实机 Wi-Fi A→B 的 echo/ASCII 大写、计数重置、错误口令/129 字节文本/不支持操作拒绝、错误底座绑定拒绝均已记录；提交后到重启前服务不可用，重启才激活。底座 ELF/bin/bootloader/分区表 SHA 未变，未串口刷底座。
- 随后恢复原 UART/SPI 模块；第一次 SPI 复测全 FF，并非成功。用户换线后的另一次 A→B→同一 SPI 恢复在三档速率各 100 次通过。间歇故障独立调查，不能断言只是线或归因于更新。见 [SPI 调查](SPI_INVESTIGATION.md)。
- 旧生产基座/完整业务可能含凭据，必须私有；历史 hello 未检出 token/password 不代表其他模块或 ABI4 产物可公开。不要读取/输出生产配置来补写文档。

ABI4 的独立硬件验收由新版报告证明，不由这些历史结果推定。
