# T11 P3 回放数据工作报告

范围：只读消费冻结的 P1/P2 来源，新增投影和测试；不改既有来源、收据或全局状态。已读 TEAM、任务书、P3 交接、冻结清单与 PROJECT；`python -X utf8 tools/project_status.py` 一致性通过。`python -B -X utf8 tools/check_integration_power_transport.py --check-receipt` 返回 190 files，收据当前。输入为合成、未标定、秒与焦耳；不代表真实衣物实验或手机送达。

交付 `research/integration_t11/p3/projection.py` 的 `build()` 每次逐秒运行实际 Rig，关键动作、输入与显示状态变化处输出帧；不读取后续状态。远端只取接收端 head，按查询时钟检查接触、充电影响、本地水、预测与预报生效期；不会读取未同步本地状态。剩余值为分析时刻条件估计，并非实时倒计时。`received_s` 在每次 simulated_receiver 实际分发时更新（包括同版本重发），receiver_query 不刷新；手机送达未知。

新增情景：`forecast_boundary`（预报生效、结束），`forecast_wet`（预报风险及本地湿），`contact_missing`（接触丢失），`queue_full`（容量 2、离线丢弃历史）。其余九项沿用 P2 scenario。额外字段 `local_seq`、`saved_seq`、`remote_seq` 是各端 head revision，非队列长度；`local_intents`、`saved_intents`、`remote_intents` 是各 head 未过期的意图类别，可能包含 `prediction_revoked`。`queue` 是待确认历史记录数，`drops` 是累积丢弃历史数；head 与历史队列独立，不能仅凭队列长度判断当前提醒。`conditions` 为中文合成未来条件列表。实际 trace 动作原样保留。

验证：`python -m unittest research.integration_t11.p3.test_projection` 4 项通过；覆盖所有九个 P2 场景动作逐条一致、JSON 有限值、离线远端与同步 revision、预报边界、预测到期、接触撤销及满队列丢弃。测试运行后源码无进一步改动。未实测设备、实际网络、手机送达或衣物预测精度；补测已增加独立失配与故障情景；missing_packet 仍展示缺失输入。协调者仍需审查用户界面、全局验收及其他 T11 条件。

本角色新增文件：`research/integration_t11/p3/projection.py`、`research/integration_t11/p3/test_projection.py`、本报告。交付供协调者整合，不宣称 T11 整体验收完成。

## 总体验收补测

新增 `model_mismatch`（覆盖或模型失配）和 `fault_revoke`（探头故障撤销），采用 P1 测试的 M6 配置和候选裁剪，通过 Rig 支付观测并触发实际 PC 分析；失配状态为 `coverage_or_model_mismatch`。故障前 26 秒为 conditional，35 秒故障及 36 秒下一帧均无剩余时间，41 秒远端接收撤销后也不可见。

`forecast_wet` 改为 `no_risk` 且本地受水；62 秒本地、68 秒远端均可见不一致。`forecast_boundary` 暂停测量使预报 36 秒到达，38 秒离线后远端在 60 秒进入 risk、90 秒过期；仍为真实 Rig 回放。新增情景的 `title` 为中文。`newly_notified` 是本帧 simulated_receiver 新通知数，重复同版本可为零，不包含 receiver_query 查询。`received_s` 是最近实际 simulated_receiver 分发时间，包括相同 revision 的重发，不代表手机送达。

新版执行 `python -m unittest research.integration_t11.p3.test_projection`：7 项通过（18.201 秒），包括前述路径、远端离线时钟及重发去重；同时九个既有回放的动作与 P2 一致。使用本机 `black` 整理两个 Python 文件，已将条件说明拆出函数。冻结包没有修改。
