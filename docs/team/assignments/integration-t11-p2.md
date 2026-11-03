# P2 完整动作与持久消息

读 TEAM、[P2 交接](../../T11-P2-HANDOFF.md) 和 [冻结输入](../baseline-t11-p2/snapshot.json)。包版本 t11-p2-v1，整数模拟秒，电池能量单位 J。与既有 P1 合作的两条接口如下。未列出的接线、验证、结果、文档归协调者。

- 电量工作者独占 research/integration_t11/p2/power.py、test_power.py 及 docs/team/reports/power-t11-p2.md。实现 Battery(initial_j=None,capacity_ah=0.25)，属性 now、stored_j；advance(to_s) 推进当前外接功率、基线与已准入动作；charge(input_w,efficiency) 即刻改变人工充电输入，0 表示拔线；start_measurement()、start_radio() 均返回布尔，按全程最坏能量预留，互斥。cancel_measurement() 支付必要安全关闭；在 6 秒成功时由调用者 radio_success() 提前结束，无成功到20秒超时；属性 active 为 None/'measurement'/'radio'，ledger() 返回可 JSON 序列化的当前账。T05 阶段费用与无线 6/20 秒，T12 基线/容量/效率；实际支付随时间发生。实现并测试费率、边界、取消、限充、空电和不借未来。额外雨头采样/唤醒成本提供 spend(load_j) 即时准入，注明基线覆盖范围。准备好后交付并停止。
- 消息工作者独占 research/integration_t11/p2/transport.py、test_transport.py 及 docs/team/reports/transport-t11-p2.md。以 T07 SQLite 辅助与事务语义和实际 P1 快照实现版本化持久 outbox、独立安全 head、模拟服务保存及接收，绑定设备。API Outbox(path,create=False,capacity=32)、Service(path,create=False)、Receiver(path,create=False)，capture(snapshot) 接收实际 P1 快照，envelope() 返回 JSON 可序列化报文；submit(envelope,now_s) 服务保存并返回 ACK，acknowledge(ack) 仅释放匹配行；dispatch(receiver,now_s) 依据最新 head 和原 TTL 去重/撤销，均提供 close()。队列满有安全失效策略，重开/中断/旧 ACK/乱序/重复/跨域/多撤销均测试。文档说明安全 head、ACK、receiver 输出及复用范围。准备好后交付并停止。
- 两工作者仅写各自文件，不提交/推送，不改既有源码/收据和全局状态，不新建对话/代理或向其他对话发消息。合成输入、无真实收衣授权，当前不开展 P3。
