# T11 P2 消息角色交付

输入：`docs/team/baseline-t11-p2/snapshot.json` 所列冻结来源，P1 示例快照 `research/integration_t11/p1/results/snapshots.json`；版本 t11-p2-v1，合成未标定，seed null，整数模拟时钟秒，证据时间允许小数秒。未连接真实服务或设备。范围仅 transport.py、test_transport.py、本报告。

实现复用 T07 的 Store、事务、规范 JSON 和哈希；没有复用 T07 Event 或上游投影校验。安全 head 独立持久化完整 P1 当前快照；队列满删除最旧历史行，head 始终保留，故可收敛最新状态但不保证每个历史通知送达。每次 envelope 带最新 head 和至多一条最旧记录：`version,head,record,record_id`；空队列仍携带 head。ACK 为 `version,device,record_id`，只删除该哈希行；重试不修改到期时间。服务绑定设备、按 revision 高水位接受 head 并记住已见 revision 哈希；历史记录保存独立于 head。Receiver 的 dispatch 返回当前未过期可见意图，排除撤销项；缺席即撤销，多个旧键同时失效。`receiver.notifications` 是最近一次接收中新出现的键，持久去重；非真实推送确认，进程在提交后取出前崩溃可能丢提示。跨服务和接收端持久模拟时钟禁止回退。P1 调用者必须每次状态变化捕获快照；断网时从未捕获的状态服务无法猜测。

验证：`python -B -m unittest research.integration_t11.p2.test_transport -v`，6 项通过，覆盖真实示例快照、容量溢出、重开、迟到/重复 ACK、重排、重复、跨设备、角色误用、多个旧可见项撤销、原 TTL、回退、提示去重、小数时间和子进程重开。早期一次角色误用测试暴露数据库句柄未关闭，已修复并重跑通过。

限制与协调验收事项：当前输入只做基础快照结构检查，不重新运行 P1 验证所有嵌套状态，必须使用可信本机 P1 生产快照；不提供认证、恶意客户端防护、设备重装后 revision 持久身份恢复，队列与去重历史没有长期回收设计。未进行进程强杀于事务中途的注入验证（仅使用 T07 SQLite 事务并测跨进程重开）；未覆盖全部合法 P1 状态的系统性生成、真实网络、手机或 MCU。接收提示与持久写入的外部副作用不能保证恰好一次。以上缺口须由协调者评估和补测，不能宣称完整 P2 已验收。

## 协调复审后的修订

`Outbox.dropped_history_count` 为只读的累计淘汰记录数，缺省为 0；删除历史行与计数更新同 head 和入队写入一起提交，ACK 删除不计入。属性供本地读取，未改变单记录 envelope 或协议字段；旧数据库没有计数时按 0 起算，不能追溯之前的淘汰。ACK 严格要求且仅允许 version、device、record_id 三字段，record_id 必须为 null 或 64 位小写十六进制哈希；无认证语义不变。重排 transport.py 的密集语句和验证条件，行为不变。

再次运行 `python -B -m unittest research.integration_t11.p2.test_transport -v`，7 项通过；新增覆盖累计淘汰、ACK 格式拒绝及不误删、子进程打开读取计数后继续累计。事务中途强制中断测试交由协调者独立实施，仍未在本角色报告中声称已通过。
