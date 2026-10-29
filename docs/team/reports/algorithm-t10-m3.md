# T10 M3 工作报告：受限等温观测物理

2026-10-29。工作范围仅 `research/garment_prediction/observation_m3/**`、`docs/GARMENT-OBSERVATION-PHYSICS.md` 及本报告，M1/M2a/M2b 及全局只读。没有进行集合更新、采购、部署或新增硬件。

先读 AGENTS、PROJECT、STATUS、TEAM、M3 任务书与指定参考：M1/M2a/M2b 协调验收、观测与 ETA 说明、T09 §5、测量契约、PROBE-CONCEPTS/MODEL-VALIDITY 气路/温度/壁源。`python -B -X utf8 tools/project_status.py`、`tools/check_garment_m1.py --check-receipt`、`tools/check_garment_m2.py --check-receipt`、`tools/check_garment_eta.py --check-receipt` 均返回 0；M3 冻结 17 项指纹全匹配。已验收的内容是此前限定范围，并非实物标定。方程依既有 T09 和 PROBE-CONCEPTS，不增加新经验来源；所有新参数显式为人为合成。

## 实现及限制

固定温度单中央区域：衣物水量 w、腔体水汽密度 ρc、传感器一阶状态 z；开放面 Mo 直通环境，覆盖面 Mc 在衣物与腔体以相同流量异号交换，换气和 S 按外部边界独立记账。方程、单位、域和不与 M1 全开放模型双扣的边界详见 `docs/GARMENT-OBSERVATION-PHYSICS.md`。S≥0 为显式外部无限源，不冒充守恒的有限壁库存；示例设 S=0 只是情景假设。温度固定、空气充分混合、无凝露和 0–50°C 条件均未实测；缺少覆盖热账、表面温度动态、真实几何/流量/壁源和传感器标定。没有把恒温当成实际加热结构或真实被动衣架能量闭合。

生成端只使用合成状态；消费端 `evaluate` 接 M2a 的外部可得字段，且要求至少两个顺序时刻、同安装和合成环境稳定门限。缺测、时序错误、环境变化、M2a 接触/热证据不足时暂停。重放输出示例的外部接触和热状态均为 unknown，消费者暂停；人工合成证据不证明自动检测。无接触不得生成衣物干燥记录，无真实收衣授权。

同一合成环境/传感器下 X0=0.6、Xb=0.15 与 X0=0.3、Xb=0.075 的局部 RH 序列 60 s 最大差 8.65e−5（比例），小于演示的 0.001 比例误差，初始水量差 0.03 kg。此为参数未知下的有限窗口近似多解，不宣称曲线严格相同或真实织物已经验证；它不支持唯一反演或集合自动收缩。

热敏感性：假设温度固定 25°C 的 60 s 示例水量损失约 0.00019922 kg，乘 2.5 MJ/kg 得潜热代理约 498 J；M1 假定初始热容约 390.8 J/K，绝热量级约 1.27 K，未计环境补热、辐射或温度依赖焓，不能解释为实际温降。仅将表面温度从 25°C 改为 24.5/25.5°C（空气仍 25°C）的瞬时传质比较为 −5.7%/+5.8%；未运行非等温轨迹。说明热边界不可忽略，不作为添加加热器依据。

## 实际验证

- `python -B -X utf8 research/garment_prediction/observation_m3/test_model.py`：10 项通过。覆盖零覆盖、零换气封闭总质量、外部壁源、解析气流过渡与传感器一阶响应、耦合平衡、步长与 midpoint、无信号传递、缺失/环境变化、域外、隐藏字段拒绝及多解。
- 与 0.01 s midpoint 对照，0.1 s RK4 的末端 RH 比例差约 1.45e−9，水量差约 2.98e−16 kg；同一方程不同积分法仅验证数值一致性。封闭系统总水差约 1.04e−16 kg；0.2/0.1 s 的端点梯形边界账残差约 −1.24e−14/−3.10e−15 kg，显示所选例的二阶收敛，不能证明参数真实。
- `python -B -X utf8 research/garment_prediction/observation_m3/replay.py`：核对 59 个冻结及验收来源，生成情景和 manifest。参数、单位、来源、seed=null、命令与哈希随包保存；浮点跨平台字节一致性未验证。

新增 `physics.py`、`consumer.py`、`test_model.py`、`config.json`、`replay.py`、`results/simulation.json`、`results/manifest.json`、`docs/GARMENT-OBSERVATION-PHYSICS.md` 及本报告。M3 是受限物理映射，不是完整实物观测适配或干燥预测；需协调者审查质量账、等温热边界及证据隔离，验收前不接集合更新、不进入 T12/T11。

## 响应记忆与热量补充

一阶传感器的状态依赖采样前历史；M2a 在单个样本时刻的接触/热覆盖不能自动消除历史影响。本修订令 `consumer.evaluate` 增加独立的 `sensor_history`：未知即暂停；外部声称有参考时刻、已表征初始状态及其误差和签发时刻时，最后一组接触及无已知热干扰证据须显式覆盖参考时刻至末次采样，且初态误差符合合成政策；否则暂停。真实性、参考初态如何取得仍未验证，无固定等待时间推断有效。测试覆盖历史未知、只有末次样本覆盖、误差过大、参考晚于样本和未来签发；示例仍 unknown。

498 J 只是示例窗口净失水乘常数得到的潜热量级，不能充当最大累计热量或严格温差界；若吸湿/蒸发往复，净值可能抵消而中间热通量更大。模型未闭合干空气显热、气流焓、压力功等，不是完整气体热力学账。上述补充不改变质量方程或数值算例；仍无新硬件或加热器。

补充后复跑 `python -B -X utf8 research/garment_prediction/observation_m3/test_model.py`（10 项通过）及 `python -B -X utf8 research/garment_prediction/observation_m3/replay.py`（重验 59 来源并生成当前结果和清单）。

## 协调审查后修订：采样时间和温度边界

首版只比较各组 `now_s`，实际四路样本均仍为 590 s 时，第二组 `now_s=601` 曾错误通过；只检查环境变化也会让局部空气 40°C、环境 25°C，或局部 20→40°C 的明显等温违例通过。首版通过数量作为历史保留，不能代表本版。

现对每个环境温度、环境 RH、局部温度及局部 RH 分别要求实际 `measured_at_s` 按观测组严格递增；重复或倒序标明该通道并暂停，仍保留对 now 和 M2a 证据时段的检查。新增环境温度、局部温度各自的窗口变化及每组两处空气温差筛选：对声明误差使用区间上界−下界和差值绝对值加两侧误差。示例各温度变化及两处差异的限值都是人为 0.5°C，环境 RH 窗口限值 0.05 比例，源于本包配置而非设备规格；误差未知或不可靠仍不能证明真实适用。M2a 允许的组内时间偏差没有构成真实同步或两测点温差的动态保证。

`applicable` 已明确收窄为必要的可观测条件检查通过，输出 `scope=necessary_observable_conditions_only`；通过不代表空气间的连续稳定，更不能推断未观测的织物表面温度或证明热账闭合。没有新增传感器或固定等待。

实际复跑 `python -B -X utf8 research/garment_prediction/observation_m3/test_model.py`：11 项通过，覆盖协调者的重复样本、局部恒 40°C 和 20→40°C、四通道逐个重复、倒序及误差界内外；`python -B -X utf8 research/garment_prediction/observation_m3/replay.py` 重新核对 59 个验收来源并更新结果/manifest。物理方程、同一合成配置下的 RH 序列、多解和热量级没有改变；门控筛选和结果范围标记已改变。更新 `consumer.py`、`test_model.py`、`config.json`（补充两个合成温度门限）、`replay.py`、`results/simulation.json`、`results/manifest.json`、本说明和报告；未改旧模型及验收收据。
