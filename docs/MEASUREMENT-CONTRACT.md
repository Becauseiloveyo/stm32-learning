# 测量协议与观测契约 v1.0

日期：2026-10-23。适用范围：电脑上的近似等温短窗口研究模型。所有模拟数据为 `SYNTHETIC_UNCALIBRATED`；本协议不证明真实衣物已干，不包含实物收衣授权。

## 1. 唯一参数入口与版本

| 类别 | 唯一可编辑入口 | 使用方与边界 |
|---|---|---|
| 阶段时长、采样周期、特征窗口、时间／数量检查 | `theory/protocol.py::MeasurementProtocol`；`DEFAULT_PROTOCOL` 为其不可变实例 | 模拟器、判定器和图表共用；不放进物理参数 |
| 环境质量门限和强／弱信号阈值 | `theory/protocol.py::Gates`；`DEFAULT_GATES` 为其不可变实例 | 仅判定器使用；全部为演示值，未标定 |
| 体积、流量、活度、壁源、传感器动态、观测误差 | `theory/model.py::Parameters` | 模拟器使用；判定器不接收此对象 |
| 单位约定、观测字段版本 | `theory/protocol.py` 的版本和单位常量 | 记录生产者和判定器共同使用 |
| 合成情景覆盖、采样变体 | `theory/run_simulation.py::cases` / `verification_protocols` | 只覆盖基类参数，不定义第二套默认值 |
| 固定随机种子 | `theory/model.py::DEFAULT_SEED` | 用于复现随机噪声，不是校准来源 |

默认数值和本次运行快照见生成的 [manifest](../theory/results/manifest.json)。文档不是第二套配置。manifest 是输出，修改它不会改变模拟器或判定器；需要修改源类型再重新生成。

`MeasurementProtocol.version="1.0"` 标识时序和窗口语义，`observation_version="1.0"` 标识观测结构。未知版本拒绝；没有隐式迁移。配置指纹由协议字段按键排序、数值规范成浮点数后，以紧凑 JSON 编码计算 SHA-256。同样数值的 `30` 和 `30.0` 指纹相同；任一有效参数改变后指纹改变。

每条观测携带 `protocol_sha256`。`assess` 将它与调用者显式协议的指纹核对，防止变更协议后误用默认规则或混入另一种协议的数据。指纹不包括场景真值和算法阈值；算法阈值在 manifest 单独记录。指纹用于配置一致性，不是传感器真实性或数据完整性的认证。

## 2. 时序与有效范围

| 协议字段 | 单位 | 编程有效范围／关系 |
|---|---|---|
| `baseline_s`, `flush_s`, `recovery_s` | s | 有限且正；累积阶段边界必须对齐采样网格 |
| `sample_period_s` | s | 有限且正；作为模拟步长，不能再另传独立 `dt` |
| `head_window_s`, `tail_window_s` | s | 有限且正；两者之和小于恢复时长，不允许窗口重叠 |
| `max_gap_factor` | 无量纲 | 有限且至少为 1；允许间隔 = `sample_period_s * max_gap_factor` |
| `min_phase_samples` | 样本数 | 整数，至少 2；每个阶段都必须满足 |
| `min_window_samples` | 样本数 | 整数，至少 2；首尾两个特征窗分别满足 |
| `time_tolerance_s` | s | 有限且正，小于采样周期的千分之一；只处理浮点比较，不是设备抖动容限 |

还检查派生总时长、最大间隔为有限数，并提前验证规则网格能为各阶段和特征窗提供足够样本。非法配置在 `simulate` / `assess` 入口抛出带原因的 `ValueError`，不伪装成一次普通无结果测量。上述范围是代码可接受条件，不是实物响应已充分稳定的范围；缩短周期能通过结构检查不代表足以测衣物。

记背景时长为 B、换气时长为 F、恢复时长为 R，E=B+F+R：

| 阶段 | 观测时间区间 | 模拟作用 |
|---|---|---|
| `baseline` | `[0, B)` | 背景额外交换量 |
| `flush` | `[B, B+F)` | 换气额外交换量 |
| `recovery` | `[B+F, E]` | 只保留泄漏交换量 |

切换时刻的样本归新阶段，记录的是切换瞬间的读数；新阶段作用于之后的积分区间。E 时刻保留终点样本，不在终点后继续积分。`time_s` 是相对本周期起点的秒数，不能直接传 Unix 时间或毫秒。

判定要求记录从 0 到 E，时间严格递增，阶段标签与计划一致，每段有足够样本，相邻间隔不超限。浮点容差应用在边界、终点和最大间隔比较；不会对乱序数据排序，也不补出丢失样本。

允许小范围时间抖动和少量内部缺样：只要不触发间隔／数量条件。默认间隔规则允许孤立丢失一个内部样本。缺起点、缺终点、阶段缺失、连续大缺口会拒判。这里没有承诺任意不均匀采样等价于均匀采样；特征采用样本算术均值，不做插值或时间加权。

首窗固定在 `[B+F, B+F+head_window_s]`，尾窗固定在 `[E-tail_window_s, E]`，均包含端点。恢复幅度 = 尾窗平均分压差 − 首窗平均分压差；末段特征 = 尾窗平均分压差。窗口不会因首个或最后一个有效样本缺失而平移。

## 3. 观测记录、单位与缺失值

`assess` 接受一个周期的 `list` 或 `tuple`，每条记录为映射。字段如下：

| 字段 | 类型／单位 | 要求 |
|---|---|---|
| `observation_version` | 字符串 | 必须为当前支持版本 |
| `unit_system` | 字符串 | 必须为 `s_degC_pctRH_Pa` |
| `protocol_sha256` | 字符串 | 必须与显式协议指纹相同 |
| `time_s` | 有限数，s | 本周期相对时间，范围与顺序见上文 |
| `phase` | 字符串 | 三个阶段之一，且匹配时间 |
| `ambient_T_C`, `local_T_C` | 有限数，°C | 闭区间 0–50；当前物理换算的模型域 |
| `ambient_RH_pct`, `local_RH_pct` | 有限数，%RH | 闭区间 0–100；60 表示 60%RH，不是 0.60 |
| `sensor_valid` | 真正的布尔值 | 任一条为 false 则本周期无效 |
| `clip_event` | 真正的布尔值 | 任一条为 true 则条件不明 |
| `delta_e_Pa` | 可选诊断值，Pa 或 null | 判定器完全忽略，由原始温湿度重新计算 |
| `synthetic_true_chamber_Pa` | 可选仿真真值，Pa | 仅用于研究核对，判定器完全忽略 |

数值字段仅接受 Python `int` / `float`，不接受布尔、数字字符串、null、NaN 或无穷值。传感器失效时应置 `sensor_valid=false`，缺失测量用 Python `None`／JSON `null`。模拟器的无效诊断分压差改为 `None`；CSV 输出为空单元格，不再写 `nan`。测量字段缺失不默认补零，不把无效样本过滤掉后继续判定。

CSV 是导出格式，`csv.DictReader` 读回的字符串需要调用者按这些字段显式解析：数值转换为数值，空测量转换为 `None`，`True` / `False` 精确转换为布尔。不能使用 `bool("False")`。原样把字符串行交给 `assess` 会拒判；本轮没有添加自动 CSV 导入器。

错误的单位声明会拒绝，代码不自动猜测或换算单位。若生产者把 0.60 错写在 `%RH` 字段且仍声明正确单位，仅凭这条记录不能识别其实际是比例值；同样不能从合理范围的数值发现所有隐含单位错误。生产者必须正确声明单位。

## 4. 物理输入与算法阈值不能混用

`Parameters` 的有效域保留原模型：体积 mL 大于零；交换、漏风和额外流量 mL/s、壁源 µg/s、传感器时间常数 s、噪声 RH 百分点均非负；表面活度在 0–1。温度、环境湿度、环境湿度阶跃后的值和带偏差的局部温度必须在模型域。RH 偏差以百分点计，温差以 °C 计。全部数值必须有限。壁源和流量对应的 SI 换算仍在模型内完成。

三个事件时间 `ambient_step_at_s`、`clip_at_s`、`invalid_at_s` 属于合成情景激励，单位 s；负值表示禁用，非负时必须落在本周期内并对齐采样网格。环境阶跃持续作用，开夹和传感器故障为指定时间的一次事件。更换协议必须重新安排不对齐的事件，不能静默丢掉事件。

`Gates` 中湿度门限单位为 %RH，湿度变化门限为 RH 百分点，温差门限为 °C，信号门限为 Pa。均有限且非负；湿度相关量不大于 100，温差不大于模型温度跨度，强信号阈值严格大于弱信号阈值。高湿和局部近饱和用 `>=` 拒判；环境变化和温度失配用 `>` 拒判。两个信号特征都达到强阈值才报告明显水汽来源；两个特征绝对值都不超过弱阈值才报告弱信号。T01 没有改变基线的六项默认阈值。

流量、活度、体积、噪声和场景名称不进入 `assess`。导出文件中的真值即使改成相反标签或不可读对象，也不得改变判定。这由守卫观测测试检查。

## 5. 返回结果与异常原因

返回 `state`、分号分隔的 `reasons`、`recovery_rise_Pa`、`tail_delta_Pa`、本次期望的 `protocol_sha256` 和恒为 false 的 `collection_authorized`。结构错误不提取特征，两项特征为 `None`；结构正确但环境不适合时保留诊断特征，状态为 UNKNOWN。

| 原因码 | 含义 |
|---|---|
| `missing_data`, `missing_field` | 空／错误容器、错误行或缺少必填字段 |
| `unsupported_observation_version`, `unsupported_units`, `protocol_mismatch` | 版本、单位或配置不匹配 |
| `invalid_flag`, `invalid_sensor`, `outside_model_domain` | 标志类型错误、无效／非有限读数或读数越界 |
| `non_monotonic_time`, `sample_gap` | 时间重复／倒退或间隔超限 |
| `missing_phase`, `phase_time_mismatch` | 阶段缺失或阶段与时间不一致 |
| `incomplete_cycle` | 起止点不全或时间超出周期 |
| `insufficient_phase_samples`, `insufficient_feature_samples` | 阶段或特征窗样本不足 |
| `high_ambient_humidity`, `near_saturation`, `ambient_changed`, `temperature_mismatch`, `clip_changed` | 当前观测条件不适合解释 |
| `ambiguous_response` | 有效观测仍不满足明确证据类别 |
| `low_signal_does_not_prove_dry` | 弱信号成立，但不能推出衣物已干 |

错误按字段存在、元数据、标志／读数、时序、环境条件的顺序检查；前一层不可靠时停止，原因列表不是所有问题的穷举。时序层保留同层多个原因，不再用“不完整周期”覆盖已发现的缺阶段原因。

三个研究状态保持为 `VAPOR_SOURCE_PRESENT`、`LOW_SIGNAL_UNCONFIRMED`、`UNKNOWN`。`EvidenceTracker` 仅计数连续弱证据，未知或强信号清零；重复弱信号不能制造标定，永不输出收衣授权。批次、重启和完整多周期状态机仍属于后续 T04。

## 6. 调用与迁移

从项目根目录的 Python 环境调用：

```python
from dataclasses import replace
from theory.model import Parameters, simulate, assess
from theory.protocol import DEFAULT_PROTOCOL, DEFAULT_GATES

protocol = replace(DEFAULT_PROTOCOL, sample_period_s=0.5)
rows = simulate(Parameters(), protocol=protocol)
result = assess(rows, DEFAULT_GATES, protocol=protocol)
assert result["collection_authorized"] is False
```

原 `simulate(Parameters())` / `assess(rows)` 默认调用继续工作；`from model import Gates` 仍可使用（由新模块导入）。自定义阶段时长从 `Parameters` 移到 `MeasurementProtocol`；`simulate(..., dt=...)` 改成协议的 `sample_period_s`。旧独立参数会抛 `TypeError`，避免两套时序混用。自定义协议必须显式同时传入模拟器和判定器，传错时拒判。

旧版无元数据的 CSV 不自动接受。迁移者必须先核实原单位、周期参数及来源，再补充新字段和明确类型，或直接从冻结情景重生成。`delta_e_Pa` 从必填算法输入变成可选诊断值，避免它与温湿度矛盾时改变状态。

完整默认数据的状态、原因和特征与冻结基线一致。变化主要影响残缺数据：旧实现允许只剩较短恢复段并随剩余数据移动首尾窗口；现在必须覆盖协议终点，窗口固定，缺阶段和阶段乱序也明确拒绝。非法配置和失配单位现在得到明确错误。

## 7. 复现、来源与验收边界

```powershell
python -X utf8 -m unittest discover -s theory -p test_model.py -v
python -X utf8 theory/run_simulation.py --plot
python -X utf8 tools/project_status.py
```

仿真重生成默认的 12 类情景、60 组扫描，以及 [三种协议的 15 条对照结果](../theory/results/protocol-comparison.csv)。两个变体是更密的采样和更长周期的较稀采样，定义在 `verification_protocols`，实际参数／种子／指纹在 manifest。另有短周期和小数采样边界测试；它们不是新增的物理有效性证据。

manifest 的 `source_sha256` 包含 `protocol.py`、`model.py` 和 `run_simulation.py`；`output_sha256` 覆盖本次实际生成的 CSV 和图。没有 `--plot` 时 `plots_generated=false`，旧图片即使留在磁盘，也不列作本次生成证据。测试读取冻结基线 CSV 做数值回归，不导入其他角色的研究代码。

变更协议、模型或阈值后，必跑模型测试并重生成受影响结果；图表依赖同样来源，需用 `--plot` 一起更新。角色测试记录和未解决问题见 [测量交接报告](team/reports/measurement.md)。主状态、主测试收据及 T01 验收由协调者维护，本角色不运行 `--record-tests`。
