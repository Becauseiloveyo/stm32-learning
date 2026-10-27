# 测量协议角色交接

日期：2026-10-23。范围：T01。本角色实现与验证已完成，按本轮任务书自检达到交付条件，提交协调者最终验收；未改主任务状态，不接续 T02。

已读：AGENTS.md、docs/PROJECT.md、docs/STATUS.json、docs/TEAM.md、本角色任务书、ROADMAP 的 T01、当前 theory 源码与说明、baseline-v1/snapshot.json 和冻结 manifest。初始只读 `python -X utf8 tools/project_status.py` 通过，记录为 14 项旧检查，后续源码修改后该主记录需要协调者更新。

写入仅限 theory/**、docs/MEASUREMENT-CONTRACT.md 和本报告。不修改全局状态和主测试记录；不采购、部署、使用设备、创建更多对话、发跨对话消息或提交 Git。

## 实际交付

已解决阶段时长混在物理参数中、恢复完整性／采样间隔／特征窗／图表区间各自硬编码的问题。新建不可变 `MeasurementProtocol`，模拟器、判定器和绘图共用其时序。协议验证覆盖版本、数值类型、有效范围、阶段边界网格对齐、窗口重叠、可用样本数、派生数值有限性。

观测增加结构版本、单位系统和协议 SHA-256。缺字段、缺数、错误布尔标志、错误单位、错配协议、缺阶段、阶段顺序错误、重复／倒退时间、过大间隔、首尾不全和样本不足均返回明确的 UNKNOWN 原因。非法配置抛 ValueError，表示调用方必须修正设置。判定器只读取观测及协议／演示阈值，原始温湿度决定分压差；导出 delta、场景标签和合成真值不再参与判断。

物理方程、物理默认假设和六项算法阈值未调整。保留湿表面漏风反例、非唯一参数反例、腔壁来源歧义。单周期结果和 EvidenceTracker 的 `collection_authorized` 都恒为 false。

## 参数唯一入口

| 内容 | 当前入口 |
|---|---|
| 协议时序、特征窗、间隔／数量条件 | `theory/protocol.py::MeasurementProtocol` → `DEFAULT_PROTOCOL` |
| 六项演示门限 | `theory/protocol.py::Gates` → `DEFAULT_GATES` |
| 物理假设、传感器误差和合成事件参数 | `theory/model.py::Parameters` |
| 观测版本、单位、协议版本常量 | `theory/protocol.py` |
| 固定随机种子 | `theory/model.py::DEFAULT_SEED` |
| 情景和协议变体覆盖 | `theory/run_simulation.py::cases` / `verification_protocols` |
| 本次实际数值、种子、来源和产物哈希 | 生成的 `theory/results/manifest.json`，不作为可编辑输入 |

协议与观测版本均为 `1.0`；manifest 格式版本为 2。默认协议指纹为 `286acc0970edd40ace775bd55d1bca9bc34f603e29c863d9d3c99ac772e261bb`。

## 实际命令与结果

工作目录为项目根目录。以下均于 2026-10-23 本轮实际执行；没有运行 `--record-tests`。

| 命令／检查 | 结果 |
|---|---|
| 初始 `python -X utf8 tools/project_status.py` | 退出 0；初始仿真来源和旧 14 项主测试记录匹配 |
| `python -X utf8 -m unittest discover -s theory -p test_model.py -v` | 主体迁移后先通过原 14 项；新增测试后通过 35 项；补上负事件哨兵和门限等号边界后，最终 **37 项通过**，退出 0，最终运行约 0.197 秒 |
| `python -X utf8 theory/run_simulation.py --plot` | 退出 0；重生成 12 情景、60 扫描、三协议 15 条对照及 PNG／SVG；非唯一参数对最大差 `1.8189894035458565e-12 Pa` |
| PowerShell here-string 管道到 `python -X utf8 -` 的只读完整性核对 | 退出 0；核对 manifest 中 3 个源码、17 个产物哈希；核对 baseline snapshot 中 10 个冻结文件；比较 72 条基线摘要／扫描；读回并显式解析 12 份 trace CSV 后再次 assess；核对三协议 15 条对照的指纹和授权标志；全部通过 |
| 最终 `python -X utf8 tools/project_status.py` | 退出 0；源码与生成清单匹配 True；主测试记录匹配 False，仍记旧 14 项，**按分工保留给协调者刷新** |
| PNG 视觉检查 | 已查看本轮生成图；四幅子图、灰色换气区间、坐标单位和合成声明可读；图标题已更新为协议 v1.0 |
| PowerShell here-string → `python -X utf8 -` 文档检查 | 测量契约、本报告、theory/README 的本地 Markdown 链接均存在，代码围栏配对通过 |
| `git status --short` | 只读核对；项目仍以未跟踪文件为主；未提交、重置或清理 |

完整性脚本使用标准库 `hashlib` 逐项核对 manifest 与冻结 snapshot；`csv.DictReader` 读取并将测量字段显式转成 float／None，两个标志按 True／False 严格解码，然后调用 `assess(rows, protocol=MeasurementProtocol(**manifest["measurement_protocol"]))`。没有用猜测的旧测试结果作当前证据。

最终 37 项中含原有数学／判定检查和 23 项协议检查；带多个 subTest，故 37 不是异常样本总数。覆盖包括：

- 12 情景状态、原因、特征和物理参数快照与冻结基线一致；60 扫描状态、原因、特征一致。额外对生成 CSV 比较得最大特征差 **0.0 Pa**。
- 默认＋两种非默认采样协议均可完整运行；漏风湿表面仍是弱信号，非唯一参数对仍重合；每种协议连续 20 次弱信号也不授权收衣。
- 短恢复协议不再受旧的 80 秒常数约束；小数采样能正确划分阶段；不宣称这些短协议有物理验证依据。
- 缺阶段、缺字段、空输入、丢失起止点、窗口样本不足、阶段标签错配、重复／倒退／超范围时间、间隔等于和超过上限。
- 摄氏温度／RH 闭区间边界与略越界值、NaN、无穷、null、数字字符串、布尔冒充数值、极大整数、非法标志类型、错误版本和单位。
- 配置零／负值、网格错配、窗口重叠、最少样本不足、派生时间溢出、非法阈值关系；高湿／饱和阈值等号拒判、环境变化／温差严格大于门限的行为。
- 守卫映射在读取真值、场景名、物理参数或导出 delta 时会抛错，判定仍成功且结果不变；移除这些非观测字段也不改变结果。

本次生成对照的采样概况如下，这是运行结果摘要，不是第二套默认配置：

| 对照名称 | 采样周期 | 总时长 | 样本数 |
|---|---:|---:|---:|
| default | 1 s | 140 s | 141 |
| half_second | 0.5 s | 140 s | 281 |
| three_second_long | 3 s | 162 s | 55 |

## 改动文件

新增：

- `theory/protocol.py`
- `docs/MEASUREMENT-CONTRACT.md`
- `theory/results/protocol-comparison.csv`
- 本报告 `docs/team/reports/measurement.md`

修改：

- `theory/model.py`：移出测量时序／阈值定义；模拟和判定接收同一协议；观测验证、固定窗口、重算分压差、明确授权 false；负事件时间确实禁用。
- `theory/run_simulation.py`：共用协议和种子，图表阶段来自协议，生成三协议对照；manifest 覆盖新增配置来源及实际输出文件哈希。
- `theory/test_model.py`：保留原检查，新增协议、回归、异常和真值隔离检查。
- `theory/README.md`：替换旧的硬编码协议说明，指向唯一入口和迁移契约，不继续手工维护重复默认阈值。
- `theory/results/manifest.json`、`case-summary.csv`、`parameter-sweep.csv`、`simulation-overview.png`、`simulation-overview.svg`。
- `theory/results/trace-{wet_normal,dry_equilibrium,wet_leaky,wet_high_humidity,ambient_step,clip_changed,sensor_failure,temperature_mismatch,dry_wall_source,slow_sensor,ambiguous_A,ambiguous_B}.csv` 共 12 份，重新生成并加入观测元数据。

未写入 baseline-v1、其他角色目录、主 STATUS、DECISIONS、ROADMAP、PROJECT、TEAM、AGENTS、根 README、tools/project_status.py 或 docs/verification/latest-tests.json。

## 接口迁移与兼容性变化

1. 默认 `simulate(Parameters())` / `assess(rows)` 调用仍有效；`model.Gates` 仍可导入，实际定义只有 protocol.py 一份。
2. 旧 `Parameters(baseline_s=..., flush_s=..., recovery_s=...)` 改成 `replace(DEFAULT_PROTOCOL, ...)`，旧 `simulate(..., dt=...)` 改成协议的 `sample_period_s`；同一协议显式传入 simulate 和 assess。旧参数直接报 TypeError，不保留兼容的第二时序入口。
3. 每条观测新增版本、单位和协议指纹。无元数据的旧 CSV 不能直接判定，必须核实旧来源和时序后明确迁移，或重生成。CSV 字符串需按契约显式解码；本轮无自动导入器。
4. 导出 delta 仅作诊断，可缺失或与观测矛盾；判定总是以原始温湿度重算。无效诊断值由 NaN 改为 None／CSV 空单元格。
5. 完整默认行为未变；残缺周期更严格。恢复尾部缺失不再因为剩余段达到旧下限而通过；首尾特征窗不会移动。少量内部丢样若仍满足最大间隔和数量规则则允许，不会宣称每次缺一条都必须拒判。
6. 合成事件时间必须在周期内且对齐采样网格；变更采样协议时需要重排事件。负时间禁用，含浮点容差内的负小数也不会触发。
7. manifest 的协议快照独立于每个情景的物理 Parameters；各情景默认共用该协议。输出增加 `collection_authorized=false` 与协议指纹。没有 `--plot` 时清单明确不声称已有图片是本次重生成的产物。

## 未解决问题与交给协调者的动作

没有发现阻塞 T01 本角色交付的未解决实现问题。以下边界仍保留：

- 无硬件、独立标定或真实衣物数据。采样足够、窗口足够和算法门限只有软件／合成验证；新协议参数不能自动获得实物有效性。
- 正确单位声明下仍可能出现隐藏的生产者单位错误，单看合法数值不能完全识别；协议指纹也不证明设备真实执行了换气。
- 特征采用样本算术均值，当前检查容许有限抖动和少量内部缺样；没有实现任意不均匀采样重建、跨周期身份、批次隔离或重启状态机。
- README 中早先的器件规格和论文背景未重新做文献研究；本轮只重构已有模型协议，没有新增物理推导或改变物理结论。
- 主测试收据现已过期，这是团队所有权安排下的预期状态；本报告的 37 项结果不能代替协调者的最终主线验收记录。

协调者下一动作：先审查本报告及测量契约，按上述 unittest 和仿真命令复核；通过后由协调者刷新主测试记录和 STATUS，决定是否接受 T01、解决 U04 并按依赖接续 T02。建议同步修正 PROJECT 中“代码参数入口全部在 model.py”的旧描述，改为物理 Parameters 在 model.py、测量协议和演示门限在 protocol.py。如记 DECISIONS，可记录“完整周期和固定时间窗、显式协议绑定、保持原阈值”的理由；本角色未写全局文件。
