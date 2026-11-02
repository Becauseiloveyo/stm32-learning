# T11 P1 角色交付报告

2026-11-02。按 `docs/team/assignments/integration-t11-p1.md` 完成 P1 代码、契约和离线证据，交协调者独立验收；本报告不宣布 T11 完成，也不代表 P2/P3 已执行。

## 交付

- `research/integration_t11/p1/controller.py`：严格版本事件、域与序号、原子验证、预报窗口、本地湿润及连续性、接触/热/故障/身份生命周期，真实 M6 调用和条件结果，待通知意图与有界撤销意图。
- `research/integration_t11/p1/config.json`：软件参数的单位、范围、来源、合成标签和 null seed。
- `research/integration_t11/p1/fixtures.py`、`replay.py`：公开合成输入与确定性重放；`results/events.json`、`snapshots.json`、`manifest.json` 分别是输入、输出与实际依赖/结果哈希。
- `research/integration_t11/p1/test_controller.py`：P1 回归；`docs/T11-EVENT-CONTRACT.md`：实际调用与边界。

读取 `PROJECT`、`STATUS`、`TEAM`、T11 任务书、集成计划、交接和 ROADMAP T11；冻结清单 `docs/team/baseline-t11-p1/snapshot.json` 160 个输入 SHA-256 均匹配。P1 生成 manifest 绑定实际执行用 M6/M5/观测依赖、公开输入、P1 源码、契约和生成结果，不绑定动态 STATUS/TEAM。M6 输入来自冻结的 `prediction_m6/config.json`，物理解析例只构造合成条件；雨头采样和预报都不是实测。

## 验证

- `python -B -X utf8 tools/project_status.py`：只读恢复通过，T11 已在进行中；旧收据检查不是对本工作新验收。
- `python -B -X utf8 -m unittest research.integration_t11.p1.test_controller -v`：18 项通过。包括实际 M6 的有限条件上界、未知上界及模型失配、合法缺测替代旧预测，失效后不能自动恢复、跨域及同刻键、重复事件推进过期、序号最大值、未来/隐藏字段拒绝、提前预报、湿润历史和有效连续链、缺报及遮挡/失效、标签和未来后缀隔离。
- `python -B -X utf8 -m research.integration_t11.p1.replay`：生成 9 个事件的轨迹；解析示例的 M6 合成采样上界为 1210 s，分析时的剩余采样值为 1190 s，均不表示实际衣物耗时。
- 以保存的 `events.json` 新建控制器逐事件重放，并以 JSON 规范化后比较 `snapshots.json`；9 个快照一致，manifest 所列 15 个哈希匹配。第一次直接比较 Python 对象与 JSON 对象失败，原因是 M6 输出 tuple 在 JSON 中变为 list；规范化比较通过，产物未因该检查更改。

## 限制与后续接口建议

所有阈值为软件假设，输出不是雨概率、实物识别精度或收衣授权；`collection_authorized` 恒为 false。真值标签仅留在生产端测试信封，不传给消费者。高湿、阴影与夜间不单独触发雨情；有效暴露探头湿不依赖预报，但不能判定雨/露/喷溅或衣物实际吸水质量。缺失观测可见 unknown，不将长期断档误称连续湿。连续湿使用重叠新鲜窗口假设，不能证明窗口间一直湿润。

使用可信单写者模拟时钟，拒绝发生时间早于上次判断的迟到事件；不具备多写者时钟、物理接触/遮挡/热检测或掉电恢复。事件 ID 绑定完整域与序号；仅保存最近事件哈希和高水位，无法查询任意历史。预测撤销仅保留最后一项，不能保证多个撤销可靠到达；计算结果及快照存在 Python 动态对象，未做目标 MCU 内存测量。

请协调者独立验收 P1；接受后，P2 应以完整域事件键和 `prediction_revoked` 为输入，建立持久 head/多撤销、域切换、TTL、ACK 与去重，并在 T05/T07/T12 适配中核对能量和断网语义。不得把此处待通知意图视为已排队、已发送或手机已接收。
