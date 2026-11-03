# T11 验收映射

2026-11-03。所有例子是合成电脑输入，非真实衣物或雨；以下“已证”仅指列出的受限测试及已接受收据。界面运行断言见本页后半部分，整体验收见 P3 审查。命令从项目根目录执行：`python -B -X utf8 tools/check_integration_events.py --check-receipt`、`python -B -X utf8 tools/check_integration_power_transport.py --check-receipt`；逐项重跑可用 `python -B -X utf8 -m unittest research.integration_t11.p1.test_controller research.integration_t11.p2.test_power research.integration_t11.p2.test_transport research.integration_t11.p2.test_integration`。定位均为 `research/integration_t11/` 下对应测试函数；收据核对不等于本次重跑测试。

| ROADMAP 条件/界面断言 | 可复现证据及缺口 |
|---|---|
| 夜雨/太阳雨、高湿无雨、阴影 | `p1/test_controller.py::test_environment_labels_do_not_override_sensor` 同观测不因标签改变；湿/干与预报不可用分别输出。不是环境识别器。界面核对本地湿与预报各自状态。 |
| 露水/喷溅 | `test_truth_and_future_suffix_isolation` 相同观测不识别成因；显示本地受水，不能标为确定下雨。 |
| 遮挡、故障、过期预报 | `test_obstruction_fault_and_stale`、`test_rain_independence_and_forecast`、P1 审查；遮挡不可用，预报到期不可见。界面检查生效前、到期边界及下一帧。 |
| 缺预报不屏蔽本地湿；不一致 | `test_rain_independence_and_forecast` 与 `test_environment_labels_do_not_override_sensor`；独立来源分别呈现，不能互相覆盖。界面需实际检查有/无有效预报与湿读数冲突。 |
| 雨头与衣物再湿分开 | P1 `test_invalidation_and_no_automatic_revival` 和事件契约：本地受水撤销预测，不证明衣物已经再湿；界面明确两者不同。 |
| 缺观测/故障不复活旧预测 | `test_missing_sample_replaces_usable_prediction`、`test_m6_unknown_and_mismatch`、`test_invalidation_and_no_automatic_revival`；界面验无接触、失配、缺上界、过期、重启/换衣及失效后的下一帧，旧剩余不可见。少于两个观测包仍拒绝，须显式 unknown/fault 或到期。 |
| 感知与手机延迟分开 | P2 `test_wet_priority_cost_latency_and_causal_prefix`；P2 审查本地 62 s、模拟接收 68/148 s。界面区分本地发现、排队、服务保存、模拟接收，手机送达未知；时间仅合成。 |
| 低电/弱网/满队列安全语义 | P2 `test_manual_charge_and_missing_samples`、`test_wet_priority_cost_latency_and_causal_prefix`、`test_exhausted_retry_budget`、transport `test_overflow_restart_ack_and_withdrawal`、`test_eviction_count_and_ack_validation`；界面检查拒绝、积压、淘汰及未知而非假送达。 |
| 无光伏、无人充电、充电中断、满电限充、恢复、不借未来电量 | P2 `test_all_energy_balances`、`test_manual_charge_and_missing_samples`、power `test_charge_efficiency_curtail_and_empty`、`test_full_charge_boundary_cost_is_not_paid_by_curtailed_energy`、`test_admission_and_no_future_borrowing`；人工两段充电与无人充电可对照，限充及瞬时费用检查。界面检查插拔时热干扰撤销、拔线后未知及新证据恢复；不代表峰值供电。 |
| 换衣/移位、迟到、旧域、重启、撤销、确认丢失 | P1 `test_identity_and_old_domain`、`test_revocation_intent`；P2 `test_supersession_expiry_and_lost_ack`、`test_process_death_then_boot_before_upload`、transport `test_reorder_duplicate_domains_and_conflict`、`test_multiple_withdrawal_and_atomic_failure`。界面比较离线远端旧快照、其到期、重连后的状态及去重。 |
| 参数及峰值未知登记 | [P2 参数和限制](T11-P2-TRANSPORT.md)、[资源估算](T11-RESOURCE-REVIEW.md)；电池、转换效率、Wi-Fi 峰值、热、真实充电与存储耐久未实测，平均能量不证明供电。 |

前两包的审查分别见 [P1](verification/T11-P1-review.md) 与 [P2](verification/T11-P2-review.md)。下列为协调者对上表界面检查项的整合结果。硬件、手机投递与真实判干率需后续实测。

| 界面核对 | 可重复的时刻与读数 |
| --- | --- |
| 传感器与预报相互独立 | 无预报时第 62 秒仍显示受水。有预报的反例在同一秒同时显示“当前未报风险”和“已受水”。昼夜、阴影、露水、喷溅使用上表的同输入反例，前端只显示公开读数和可能成因。 |
| 预报过期、遮挡、故障 | 第 36 秒远端收到未来预报，断网后第 60 秒生效，第 90 秒过期，接收时刻一直为第 36 秒。遮挡与故障情景的第 35、36 秒均无旧剩余时间。 |
| 观测不足与衣物身份变化 | 缺包和失配都在第 26 秒隐去数字，文案区分二者；失去中央接触在第 35 秒显示未接触。换衣或重启时，远端第 74 秒仅可见上次同步的有效证据，第 101 秒已无旧时间，第 148 秒新状态到达。迟到、旧域、旧 ACK 的拒绝在 P1/P2 测试中验证。 |
| 消息与能量 | 第 62 秒本地入队；断网情景至第 148 秒到达模拟端，全程手机未知。确认丢失情景第 68、148 秒分别新增 1、0 条提示。满队列第 35 秒有淘汰，低电未充第 60 秒仍未接收。插拔充电第 4、14、50 秒分别显示受干扰、未知和新证据排除。 |

逐帧浏览器断言在 [browser_review.js](../research/integration_t11/p3/browser_review.js)，覆盖各帧数值显示与队列计数，并核对以上选定时刻。完整范围与结果见[总体审查](verification/T11-P3-review.md)。
