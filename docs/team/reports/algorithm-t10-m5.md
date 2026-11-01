# T10 M5 工作报告：统一动态衣物与中央空气

2026-11-01。仅写 `research/garment_prediction/coupled_m5/**`、`docs/GARMENT-COUPLED.md` 和本报告；旧五包、收据和全局文件保持只读。未采购、部署、提交、增加测点或启动集合更新/ETA。

已读 AGENTS、PROJECT、STATUS、TEAM、M5 任务书、M3/M4 协调审查及 M4 连接缺口，核对 M1/M3 模型。`python -B -X utf8 tools/project_status.py`、`tools/check_garment_m1.py --check-receipt`、`tools/check_garment_m2.py --check-receipt`、`tools/check_garment_eta.py --check-receipt`、`tools/check_garment_observation.py --check-receipt`、`tools/check_garment_ensemble.py --check-receipt` 全部返回 0；M5 冻结 21 项匹配。未重跑或重写旧模型和收据。

本包实现单一库存的两区衣物动态温度/水分及中央局部空气质量、传感器 RH 一阶状态。开放/覆盖面积不重叠；覆盖通量在衣物和空气中等量反向。衣物显热、雨水/排液焓与 M1 基准一致；气腔水汽采用理想气体 `uv=hv−Rv(T+273.15)`，空气温度由外部给定，外部热库承接水汽内能与流动焓的差额。外部边界按太阳、对流、辐射、开放蒸汽、通风、液水、气腔补偿及验证用恒温热库逐项积分；不以状态导数和充当唯一账本。干空气热容、湿空气压力/混合功、壁热和吸附热未闭合，不能称为完整实物热系统；覆盖对太阳/对流面积仍用未校准的 M1 假设。单个 `Drive` 的雨水流量对两个区各自赋相同 R，属于受限合成情景，不是二值雨头推断。

传感器状态直接是 RH 的一阶滤波状态，假设敏感元件温度瞬时跟随腔体 Ta；环境温变时 z 连续，瞬时目标 RH 可以改变。不是 M3 的密度状态在温度改变后直接除以新的饱和值；未声称真实传感器等温或响应已标定。区域 0 与中央覆盖仅是场景假设，不证明中央最慢或代表整衣。

`run_segments` 要求初始完整状态与 prior_drive，之后每次环境切换保留两区水量/能量、气腔水汽和传感器状态；水汽内能跃迁 `m(cpv−Rv)ΔTa` 单列并加入外部能量账。模式 `dynamic` 为主，`clamped_verification` 仅严格布尔请求，额外虚拟恒温热库分项，不新增真实硬件。底层积分直接调用时由调用者确保与上一个边界匹配，包尚无持久化、身份或跨版本检查。

实际执行 `python -B -X utf8 research/garment_prediction/coupled_m5/test_coupled.py`：13 项通过，包括零覆盖对照 M1（含雨/排液）、外部恒温参考对照 M3、封闭质量与热账、零库存吸湿、独立太阳供热及 RH 指数响应解析、同温雨/线性排液闭式、边界分项/内部覆盖抵消、环境切换与全程账、采样步长和 midpoint 对照、凝露/域外与布尔模式。示例固定驱动 60 s：0.2/0.1 s 的端点梯形能量残差约 0.004456/0.001114 J、水量约 1.322e−9/3.306e−10 kg；0.1 s RK4 相对 0.01 s midpoint 的 RH 比例差约 1.98e−9、区 0 能量差约 1.15e−5 J。同一方程不同积分法不能证明实物参数。

`python -B -X utf8 research/garment_prediction/coupled_m5/replay.py` 核对 85 个输入后生成 `results/example.json` 和 manifest；两段切换、每段边界分项、空气水汽储能跳变、合成参数/单位/种子及来源哈希均保留。输出 `SYNTHETIC_UNCALIBRATED`、`collection_authorized=false`，没有真实天气或硬件实验。

文件：`coupled.py`、`config.json`、`test_coupled.py`、`replay.py`、`results/example.json`、`results/manifest.json`、`docs/GARMENT-COUPLED.md` 和本报告。剩余：实际中央与两区的对应、接触与覆盖几何/流量、材料与传感器/温度响应、壁面库存及完整热耦合均未标定；该版本没有观测更新或 ETA，且 M4/M2b 不能未经另行统一状态、面积和边界直接接入。请协调者独立审查后决定后续；交付后停止写入。
