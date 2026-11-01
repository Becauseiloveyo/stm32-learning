# T10 M6 工作报告：同一 M5 状态的条件筛选与情景时间

2026-11-01。仅写 `research/garment_prediction/prediction_m6/**`、`docs/GARMENT-PREDICTION-INTEGRATED.md` 和本报告；旧六包、验收收据和全局只读。未采购、部署、提交、创建代理或进入 T12/T11。T10 完成状态由协调者决定。

读取 AGENTS、PROJECT、STATUS、TEAM、M6 任务书、M4/M5 验收与 M5 状态、M2a 接触时间及 M2b 目标边界；`python -B -X utf8 tools/project_status.py` 和 `tools/check_garment_m1.py --check-receipt`、`tools/check_garment_m2.py --check-receipt`、`tools/check_garment_eta.py --check-receipt`、`tools/check_garment_observation.py --check-receipt`、`tools/check_garment_ensemble.py --check-receipt`、`tools/check_garment_coupled.py --check-receipt` 均返回 0。M6 冻结 23 项指纹匹配；重放逐项核验六份已验收来源。

`integrated.predict` 用 M2a 真实可得时间/接触/热状态及外部传感器历史声明，按观测实际时刻用 M5 动态模型推进有限候选，局部/环境温度匹配同一分段边界；没有套用 M3 恒温窗口门控。缺测、重复、未接触/未知、安装变化、历史覆盖不足、温度不符或模型/误差未知均保留信息不足；持久偏差在全窗口和 M5 已检查数值阶段 RH 域内取共同交集。有限初态只代表明确枚举的点，不是连续完整区间，结构/数值界、保护带全为未标定合成声明；外部声明真实性和真实模型误差未验证。

每个兼容变体直接用同一 M5 八项完整历史末态、末边界、区域与耦合参数推进每个未来假设，不调用旧 M2b 的不同状态。两个区域须同一网格点达标；`first_sample_s` 是相对时钟的绝对采样时刻，`remaining_sample_s` 从 as_of 起算，初始满足为 0。分段点也被检出，步间短暂事件可漏；不存在连续首次穿越保证或真实收衣阈值。出现再湿后继续采样；若已检出后计算/凝露失败，保留已知首次检出、返湿标志、最近成功态及失败原因，同时上界未知。未来源需明确标记 `synthetic_scenario` 且分析时已给出；字段本身无法识别被伪装的未来实况。

消费接口严格白名单，生成真值和未来实况不入参；`producer_fixture.json` 仅供离线 `verify_producer.py` 检查历史合成 RH 的来源和固定 +0.005 偏差，重放没有把该文件的隐藏状态交给消费者。历史结果随相同可得观测保持不变，即使改变未来情景或隐藏外壳内容；后来的证据不能以早于可用时刻的 as_of 使用。真实外部来源、接触和模型误差界仍无法由代码验证。

实际执行 `python -B -X utf8 research/garment_prediction/prediction_m6/test_integrated.py`：16 项通过。独立解析零交换/线性排液例 X0=(1,.8)、k=.001、目标 .3，解析约 1203.97 s；10 s 检出 1210 s、距 as_of=20 s 为 1190 s，完整历史/未来接续的八项状态与直接重放一致。另用无交换恒定 RH=0.5、两观测各需互不相交偏差区间检验全窗口固定偏差。还覆盖动态环境切换、错误候选先验、未测区多解、有限初始状态、缺测/重复/晚来证据/换装/接触不足、全不相容、失败保留、首次达标后再湿并失败、非重叠达标、未来隔离、重复标识与资源上限。解析与数值仅针对所述合成条件，不证明物理标定或连续时间精度。

`python -B -X utf8 research/garment_prediction/prediction_m6/replay.py`：核对 100 个冻结/源码输入、生成本包输出与清单，独立复核生产者样例。示例高/低水量候选相容、低活度候选条件不相容；兼容候选在两个未来场景的 60 s 时域未检出，`upper_remaining_s=null`。示例估计最多 318 积分步，本机运行约 0.04 s；硬上限为 200000 估计步，超限明确拒绝，不伪装为成功或丢弃候选。性能不作实机承诺。

新增 `integrated.py`、`test_integrated.py`、`config.json`、`producer_fixture.json`、`verify_producer.py`、`replay.py`、`results/prediction.json`、`results/manifest.json`、`docs/GARMENT-PREDICTION-INTEGRATED.md`、本报告。所有结果 `SYNTHETIC_UNCALIBRATED`、`collection_authorized=false`；参数、单位、seed=null、来源及哈希可复算。

限制：中央区域关系、真实接触、湿空气/壁面热闭合、传感器/材料与误差界均未标定；只验证给定合成模型、有限候选和网格，不是现实准确率/概率/收衣授权。请协调者独立核查时间可得性、候选/失败保留、全状态切换与资源限制后决定 T10 状态；本对话停止写入。

协调整合补记：工作对话停止后，协调者补齐结果中的请求/状态版本、参考时刻、目标单位、步长和独立复制的未来条件，使原 T10 输出解释项可直接核对。重放由协调者重新生成；没有修改数值方程或旧包。当前验收以 `docs/verification/T10-M6-tests.json` 为准，前述 16 项为工作对话交付测试数量。
