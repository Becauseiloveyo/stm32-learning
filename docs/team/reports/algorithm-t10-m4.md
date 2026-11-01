# T10 M4：有限候选相容工作报告

2026-10-30。仅写 `research/garment_prediction/ensemble_m4/**`、`docs/GARMENT-ENSEMBLE.md`、本报告；旧模型、说明、报告、收据和全局只读。未创建代理、提交或部署，未连接 ETA、T12/T11。全局 T10 状态由协调者维护。

已读任务书、冻结来源、M3 审查/物理及消费端契约和 M2b 边界。`python -B -X utf8 tools/project_status.py`、`tools/check_garment_m1.py --check-receipt`、`tools/check_garment_m2.py --check-receipt`、`tools/check_garment_eta.py --check-receipt`、`tools/check_garment_observation.py --check-receipt` 均通过，16 项 M4 冻结输入匹配。最初误调用不存在的 `tools/check_garment_m3.py` 后定位正确的 observation 工具并通过；未修改任何旧收据。

本包只做 `finite_discrete_conditional` 兼容性，不是连续集合包络/SIVIA、概率或现实精度。M3 温度、气路、模型及误差界须外部显式声明适用；不适用或未知时保持信息不足，失败或越域留 `calculation_failed`。候选列举的多个初始响应状态不默认折叠为精确值；有限离散支持声明不是连续全覆盖。结构界 0.002、数值界 0.00001、边界保护 0.001 均为 RH 比例的合成声明而非严格数值界或标定，近边界保留信息不足；不应以该未验证界认定真实候选已被排除。

候选固定温度须在环境及局部每项读数的各自误差界内；协调者给出环境 25±0.01°C、局部 24.8±0.01°C 的具体反例，M3 宽泛门控通过但候选模型标记信息不足。独立持久偏差 b 的可行区间跨全窗口取交集，且与 M3 参考初态、每个 RK4 阶段和步末的偏置后 RH 0–1 域相交；域内只保证已检查数值状态，非步间连续性证明。无满足数值域的偏差保留为计算失败；此前代码仅检查环境温度及未裁剪偏差域，已修复。

示例高/低含水候选 X0=0.6、0.3（相应 Xb=0.15、0.075）均在给定有限初态、同一偏差及合成误差下相容；低活度 X0=0.03 的候选在声明条件下不相容。并非真实水量界或干燥结论。所有候选不相容时明确 `model_or_candidate_coverage_mismatch`，不选最近者；任何计算失败/信息不足使集合 unresolved，不产生确定水量界。

独立解析对照：无任何交换时空气和传感器初始 RH 均为 0.5，预测恒为 0.5；两次读数 0.51、0.53 且各误差 0.001 时，恒偏差应同时属于 [0.009,0.011] 与 [0.029,0.031]，交集空。测试亦覆盖共同偏差及保护带，防止逐点自由选偏差。观测包、假设和候选的签发或当前时间不得超过 as_of；晚来证据只能在更晚时刻重算，代码不持久化历史。严格字段拒绝隐藏真值与未来天气，不能验证输入方是否把未来实况伪装进合法字段。

`python -B -X utf8 research/garment_prediction/ensemble_m4/test_ensemble.py`：10 项通过，覆盖多解、限定排除、初态集合、接触/热/环境及历史不足、失败/超域保留、缺失候选、持久偏差解析、时间与隐藏字段、局部温度和 RH 域约束。`python -B -X utf8 research/garment_prediction/ensemble_m4/replay.py` 核对 72 个冻结或源码输入并生成当前结果及 manifest；命令、单位、seed=null、来源、错误界及 SHA-256 可追溯。具体输出及局限见说明。

新增 `ensemble.py`、`config.json`、`test_ensemble.py`、`replay.py`、`results/compatibility.json`、`results/manifest.json`、`docs/GARMENT-ENSEMBLE.md` 和本报告。真实中央映射、局部几何和温度/误差标定、未测区域及 M3→M1/M2b 统一质量热边界均未完成；不能将局部候选直接用于整衣 ETA，真实收衣授权始终 false。请求协调者独立检查当前证据与缺口后决定下一任务；本对话交付后停止写入。
