# T06 能量与调度 C 原语交付报告

日期：2026-10-26。交付状态：本角色实现、实际本机编译及 Python 对照完成，提交协调者独立验收；此报告不把主线 T06 标记 done。

已读 AGENTS、PROJECT、STATUS、TEAM T06 补充、energy-t06 任务书与 ROADMAP T06。T05 已验收；恢复检查 `python -B -X utf8 tools/project_status.py` 退出码 0，T01–T05 已验收来源／产物匹配，仅核对旧收据，不重跑上游。现有 `C:\msys64=\ucrt64\bin\gcc.exe --version` 确认 GCC 16.1.0。

只写 `firmware/core/hanger_energy.h`／`.c`、`firmware/host/energy_bridge.c`／`energy_validation.py` 和本报告；构建与验证产物限于 `firmware/host/build/energy/`。上游 Python、旧结果、另一角色源码、全局状态、主入口和 CORE-INTERFACE 均由对应所有者维护。

本轮提取预算、逐步收支、迟滞、准入、双日额度与退避原语，不移植完整环境仿真、请求队列或状态机，不提供收衣授权。控制器继续负责事件顺序、批次身份、新安装、实际采集与付费时序、可信时钟及掉电后额度恢复。

## 文件与接口

| 文件 | 内容 |
|---|---|
| [hanger_energy.h](../../../firmware/core/hanger_energy.h) | C99 公共结构、单位、范围、错误码和调用责任 |
| [hanger_energy.c](../../../firmware/core/hanger_energy.c) | 无堆分配、无递归、无硬件／I/O／可变全局状态的原语实现 |
| [energy_bridge.c](../../../firmware/host/energy_bridge.c) | 原生 ABI 转发与 sizeof 查询；不含 Python 预计算的决策或期望结果 |
| [energy_validation.py](../../../firmware/host/energy_validation.py) | 实际构建 DLL、ctypes 调用真实 C、调用真实 T05 Python 方法作对照，保存输入及结果证据 |
| 本报告 | 接口接续、测试与限制；不修改协调者的 CORE-INTERFACE |

供协调者接入的入口为 `run_validation(compiler, build_dir) -> dict`：编译器参数须为现存绝对路径字符串，`build_dir` 为 `pathlib.Path`，必须位于 `firmware/host/build/energy/` 内。实际执行编译及原生调用；任何构建、ABI、来源漂移或比较失败抛异常。成功返回 JSON 可序列化的 `passed`、`check_count`、实际参数列表 `commands`、`command_cwd`、`metrics`、来源指纹和产物路径。每次在 build_dir 内新建独立子目录，避免覆盖本进程已加载的 DLL；不删除旧产物。

| C API | 契约 |
|---|---|
| `he_config_validate`／`he_protocol_validate` | 有限非负能量／功率、效率、迟滞、启动预算、无线时长／限次／退避；协议原语只核对三个阶段的整数范围，完整 Protocol／Gates／Policy 仍须外部确认 |
| `he_budget` | 完整测量或最坏无线动作分项、全过程背景和电池预留；默认测量增量 4.38 J，背景另加；不包含 stop 保留线本身 |
| `he_account` | 固定 11 分类优先供给，返回实际供给／未供、转换损失、储能、弃能、残差；脉冲禁止传入收能 |
| `he_power_update` | 停止／低模式／正常模式迟滞；返回支付后的储能及 boot／stop／deferred 标志，不代发 T04 事件 |
| `he_time_add` | 检查 uint64 加法；恰好等于 UINT64_MAX 可表示，超过则报错 |
| `he_admit` | 与 T05 `eligibility` 同序的供电、安装、批次、低优先级、完整期限、状态准备、旧窗口、完整能量、无线额度检查 |
| `he_quota_init`／`he_quota_advance` | 仅保存当前／下一模拟日的实际无线电池支出和可信时间下限；倒退失败，跨一天平移，跳多天清掉已过期日，不复活旧额度 |
| `he_wifi_check` | 按完整超时和每个跨越日计算预算；合法检查会推进额度时钟，即使正常预算拒绝也不会回退，但不扣除假想支出 |
| `he_wifi_charge` | 只登记实际已观察并付费的连续秒数；结束时钟为开区间末端，拒绝倒填或重叠；超额度实耗仍登记，不能隐藏已发生费用 |
| `he_retry` | 根据已尝试次数返回耗尽或失败结束后的下一次时间；末退避槽可重复，时间加法检查溢出 |
| `he_reason_name` | 与 T05 准入字符串逐项对齐；非法枚举返回 `invalid_reason` |

所有非 OK 错误均不修改调用者输出或额度状态；正常不准入是 `HE_OK` 加原因，不是执行异常。错误码为 `HE_ERR_ARGUMENT`（空指针）、`HE_ERR_RANGE`（值／枚举非法）、`HE_ERR_CONFIG`（配置约束违反）、`HE_ERR_OVERFLOW`（时间或浮点派生溢出）、`HE_ERR_TIME`（时钟倒退等）、`HE_ERR_STATE`（内部字段不一致／无法支付启动）和 `HE_ERR_UNINITIALIZED`（未恢复日额度）。校验覆盖 NaN、Inf、负功率／能量、布尔域、非法模式、总量溢出、期限／预约加法、跨午夜与退避溢出。

## 类型、参数与一致性域

功率 W、执行器 J、账本 Wh；名字含 store／battery／solar／spill 的量在电池侧，其他负载侧量按 `eta` 转换。三个 2 mW 阶段值仍是背景之外的增量，开／关各 0.05 J，不能重复付费或从全周期预算扣除恢复阶段。C 不内置另一套物理门限，由调用者传入已接受配置；不改 T05 Python 或其输入、结果。

阶段长度为正 `uint32_t` 整数秒；其和提升到 uint64 后计算。墙钟、期限、窗口及退避时长为 `uint64_t` 秒，所有派生加法检查溢出；最小周期间隔严格为正。无线成功／超时为正整数秒，成功不超过超时；为保证固定双日存储，**超时最多 86400 秒**。尝试次数为 uint32，退避固定 4 槽，耗尽后不再计算下一时刻。

外部签名整数、文本或浮点时间必须先检查再转换为无符号类型，不能把 `-1` 转成 UINT64_MAX。主机转换层包含此检查及 18 组非法转换验证；C 接口只接收已表示的无符号值，无法恢复调用前丢失的符号信息。主机验证确认 double 为 8 字节，按当前 binary64 运算与 Python 对照；目标编译器若改变 double、枚举大小或 ABI，须重新核验，不能直接复制 ctypes 布局当作持久化格式。

普通对照使用当前 T05 默认配置及种子 `20261026` 的 160 组合法参数组合；模拟时间在若干日内、随机容量 4–10 Wh、效率 0.55–1、随机时长／功耗均记录在向量中。UINT64_MAX、输入溢出及不可表示值另做 C 类型边界检查；不声称 Python 浮点事件时钟在 uint64 极限仍保有一秒分辨率。完整 T04 配置身份仍为 `8430c826a757a4d5260b6da6f279796fe378a8a080a69425bd2f602c41111ed7`；本层不接替状态机的身份／原始观测验证。

存在一项明确的保守差异：在构造的远小于 `1e-12 Wh` 储能尺度，T05 的启动 epsilon 比较可能许可实际支付不了的启动。C 检查实际启动供给，返回 `HE_ERR_STATE` 并保持输出不变，不复制“未付费但已启动”。已用 `stop=0`、`restart=2e-14 Wh`、`boot=1e-14 Wh`、实际储能 0 的专门输入验证；正常默认和随机一致性域没有此差异。此项不是改动上游模型，也不是实物缺陷结论。

## 外部控制器仍须负责的工作

本层没有请求队列、批次字符串／哈希集合、原始采样缓存、固定／按需策略选择器、T04 状态机、网络成功模拟或太阳能环境生成器。`he_admit` 接收已经确认的安装、批次一致、准备状态和完整配置参数；不能把任意传入的 ready 或 batch_matches 当作真实验证。调度须只保留一个活动动作，并持久保留仍有安全含义的窗口／额度；本轮不实施一个会悄悄淘汰旧状态的队列。

事件控制顺序沿用 T05：新安装先取消旧动作；到期完整 finish 先于同秒低电量停止；严格事件槽占用时传 `can_emit=0`，停止可延至下秒并支付该秒背景。外部负责 clip、取消请求、重装等待、boot 域、完成超时、证据过期、气窗安全退出及原始 assess。该层不缩短完整周期，不设置收衣授权。

`he_power_update` 的成功启动结果**已经扣除启动费**，调用者应用返回储能并记录 `boot_load_wh`，不能再把同一启动交给 `he_account` 扣第二次。无线日额度则仅是额度账，不替代电池能量账；真实执行秒段须分别做 `he_account` 和 `he_wifi_charge`，且后者只传实际已付秒数。前者不允许把将来太阳能输入动作准入或启动／开关脉冲。合并连续区间必须保持动作、环境及饱和／耗尽边界；本验证从 T05 保存账本只选零时长脉冲或一秒段，从未把非线性合并段冒充单步。

`he_quota_init` 是可信新建／恢复入口，不是任意 boot 清零入口。新启动必须保留或可信恢复额度和时钟；若没有恢复依据，保持 `initialized=0`，无线准入报错。日推进只抛弃已过去的额度日，倒退／跳日后再回旧日均拒绝；记录实际超支后后续准入失败。没有非易失存储、时钟防篡改或原子写入实现，不能声称物理断电后仍可靠去重／限额。

## 实际编译与验证

在项目根执行：

```powershell
python -B -X utf8 firmware/host/energy_validation.py
```

最终退出码 0，返回 `passed=true`、**1940 组逻辑检查**；这不是 1940 个独立物理实验。内部共比较 12,147 个浮点标量。检查数分组为：ABI 2；预算 322；能量单步 202 和守恒 202；准入 191；功率状态 256；无线检查 182、实际付费 182；退避 168；跨日状态 7；解析总能量 1；整数边界 4；C 无效／原子失败 149；真实 Python 配置拒绝对照 54；无符号转换 18。

实际使用 `C:\msys64=\ucrt64\bin\gcc.exe`，版本 `gcc.exe (Rev5, Built by MSYS2 project) 16.1.0`。编译为 C99，参数含 `-Wall -Wextra -Werror -pedantic -O2 -fno-fast-math -ffp-contract=off -fstack-usage`，链接 `-shared ... -lm`。先分别编译 core 和 bridge，再链接共享库，经 ctypes 原生调用实际 C；期望结果仅用于返回后的比较，从不传给 C 作决策。子进程使用参数列表，PATH 前置编译器 bin，不安装任何工具。

首次尝试把含中文的绝对输出路径传给汇编器失败；当前用 Unicode 工作目录加 ASCII 相对源码／输出参数，编译通过。此修正不移动工程、不改全局路径、不写指定 build 范围之外。构建日志保留实际 cwd、参数、stdout／stderr 和退出码。最后源码修正为拒绝零最小周期间隔，并补充来源与无符号输入检查后，已重新执行全部检查；不使用之前 1867 组结果代替最终证据。

| 原语 | 真实 Python 对照入口 |
|---|---|
| 完整预算 | `measurement_components`、`required_battery_wh` |
| 各类实际供给／储能／弃能／缺口 | `Scheduler.account` |
| 迟滞、启动付费与延迟停止 | `Scheduler.boot_or_stop` |
| 准入原因 | `Scheduler.eligibility` |
| 跨日最坏额度 | `Scheduler.wifi_budget_ok` |
| 实际已付无线日费用 | `Scheduler.spend_second`，逐秒更新真实参考额度字典 |
| 失败后退避／耗尽 | `Scheduler.finish` 的真实无线超时分支 |
| 无效功率／能量参数 | `Energy.validate(DEFAULT_CONFIG)` |

比较覆盖恰好足够／差 `1e-9 Wh`、截止／预约等号、完整 4.38 J、满电弃能、空储能缺口、分类供给优先级、未来太阳不可借给脉冲／准入、低电量迟滞、同秒停止延迟、超时付费、跨午夜、日预算恰好／略超、时间倒退／跳日、重复检查不扣费、未恢复额度拒绝、重叠记账拒绝、时间溢出与零尝试非法等。12 个 T05 原账本各选取若干单步作为额外向量；先核对其指纹符合已验收 manifest，再读入，防止使用陈旧或损坏产物。

## 数值与内存证据

数值比较容差为 `max(2e-12 Wh, 2e-12 × max(|C|, |Python|))`；状态、准入原因、错误码和整数时刻逐值精确比较，不用此容差宽免不同决策。容差给 binary64 求和及能量单位换算留余量，与 T05 `1e-12 Wh` 比较 epsilon 分开；不代表物理准确度。当前向量的最大绝对差 **3.552713678800501e-15**；参考值大于绝对容差时最大相对差 **8.341386387847297e-15**。原始最大相对差 4.0 来自接近零的守恒残差，其绝对差仍在上述范围内，不能把该比率解读成能量误差 400%。这是本次向量的观察上界，不是所有有限 double 输入的全域误差证明。

本机 `sizeof`（字节）：

| 结构 | 字节 | 结构 | 字节 |
|---|---:|---|---:|
| he_config | 200 | he_protocol | 12 |
| he_budget_result | 120 | he_balance | 232 |
| he_power_state | 16 | he_power_result | 32 |
| he_quota | 40 | he_wifi_result | 32 |
| he_admission | 80 | he_admission_result | 24 |
| he_retry_result | 16 | double／uint64_t | 8／8 |

若外部保存配置、协议、功率状态与日额度，本层这四项合计 268 字节；调用输出另由调用者分配，可复用，外部状态机／动作／队列空间不计入该数。C 无 malloc/realloc/calloc/free、递归或随运行时间增长的历史数组，分类固定 11 项，退避固定 4 项，额度固定 2 日。

GCC `.su` 全部记录为静态栈帧，最大单帧 480 字节（he_power_update）。core 与桥接所有列出帧的保守总和为 2600 字节；对无递归的本模块可作很宽的已列代码帧上界，**不含 libm／运行库、外部调用者、RTOS／中断或目标编译器栈**。不是 MCU 总栈／RAM 验收。为保持 T05 临界额度比较的逐秒求和顺序，每个无线份额循环上界 86400 次，charge 最多两次这种有界循环；没有声称常数周期指令耗时或在目标 MCU 上满足实时性。

## 证据位置与指纹

本角色最终验证目录：`firmware/host/build/energy/energy_run_7a5723ah/`。

- [energy_validation.json](../../../firmware/host/build/energy/energy_run_7a5723ah/energy_validation.json)：最终结构化结果、实际命令、来源／参数引用、sizeof、逐函数栈与限制。
- [energy_vectors.json](../../../firmware/host/build/energy/energy_run_7a5723ah/energy_vectors.json)：完整输入、随机参数、期望和原生结果，SHA-256 为 `02d7f12561517ab1550bf1fa868e57ef8f603db41f15dbfbf3f1728af9d299c0`；种子 `20261026`，标签 `SYNTHETIC_UNCALIBRATED`。
- `energy_build_log.json`、`hanger_energy.su`、`energy_bridge.su`、对象文件和 `energy_core.dll` 同目录保留。构建输出可重新生成，不代替主项目收据。

最终四份源码 SHA-256（均相对项目根）：

| 文件 | SHA-256 |
|---|---|
| firmware/core/hanger_energy.h | `c735903bab6a0f3962ccd5c59e488dd07ef10d40804b1aaaaeac6a9bcde9863d` |
| firmware/core/hanger_energy.c | `07d0e4d3879f4a1bd794799c0bb8093d23d29e2a2802270f8940e606eba59a99` |
| firmware/host/energy_bridge.c | `34976c34f2f04f1c751075cd9a6dee10e5243e2eecdd9ce1358724aa793323bc` |
| firmware/host/energy_validation.py | `508699cc7f85ea121fb9575586328a873d23808526a4ec6f4b54adf7196bb81b` |

参考 T05 manifest 指纹为 `86c85040f782cab1caa706f9fa54814fe124aaefc532402e9b103c3a8bf50521`；其完整场景参数输入 `inputs.json` 指纹为 `aad2efe9f87f356b0dab45659509fa29c393f7e389ea9d2fef416b07ec007055`。验证结果还列出 T05 contracts/model、T04 machine、protocol/model、正式文档、任务书及实际读取的 12 个账本指纹；启动前核对已验收参考，结束后再次检查输入／本轮源码没有中途漂移。

## 限制与协调者下一动作

这是一组可在本机验证的 C 原语，不是完整 T05 C 仿真器或可刷写固件。外部事件排序、可信身份与时钟、完整 T04 集成、资源有限的请求队列、采集、持久化和真实硬件驱动仍由后续整合处理。实际功率、收能、瞬态供电、掉电安全及判干标定没有新增实物证据；也没有实施 T07 通知。

协调者接续：通过上述 `run_validation` 入口复验，并独立审查 C 数值／时间边界和调用责任；结合另一角色接口编写主入口与 CORE-INTERFACE，再决定主线 T06 验收及资源问题 U06 的哪些范围得到证据。本角色不修改其全局状态或收据，不向其他对话发送消息；交付后停止扩展，保留源码供整合。
