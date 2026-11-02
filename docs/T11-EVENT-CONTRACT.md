# T11 P1 离线事件与预测契约

版本 `t11-p1-v1`；标签 `SYNTHETIC_UNCALIBRATED`，seed 为 null。本包是电脑上的合成研究原型，不是固件、传感器算法、真实降水判断或手机通知。入口为 `research/integration_t11/p1/controller.py::Controller`，`apply(event, decision_s)` 返回快照；请求及快照示例见同目录 `results/`。参数登记在 `config.json`；120 s 时效和 300 s 湿滞留均为软件示例，不是实物阈值。来源见 `docs/references/T11-P1-sources.md`。

## 身份、时间与原子性

所有秒数位于同一条可信、非负、模拟单写者时钟，范围 0–1,000,000,000 s；不是 UTC 或跨设备同步协议。事件严格字段：`version,id,seq,domain,occurred_s,received_s,kind,payload`；domain 严格为 `device,boot,batch,installation`，字符串为非空且至多 80 字符。序号全局单调，范围 1–2^53，即使身份变更也不重置。事件 ID 必须由 `event_id(domain, seq)` 生成：规范 JSON 的完整域 SHA-256 加序号；不同设备、启动域、批次、安装或序号不会共享业务键（依赖哈希抗碰撞）。单写者不得复用同域及序号；设备重启后的持久高水位尚未实现。

发生、接收、判断时刻分别记录，要求 `occurred_s <= received_s <= decision_s`。有意采用保守接收规则：新事件发生和接收都不得早于上一判断时刻；迟到事件拒绝而非按历史重排。同刻按单写者序号顺序处理，先前已判断的同刻证据不会由于后到事件被回溯修改；先后差异是输入顺序的责任。跨域事件拒绝；`identity` 事件在旧域内提交新 `boot,batch,installation`，至少一项变化并撤销旧预测；后续事件使用新域。新 ID 与序号绑定，所以旧 ID 不能以更高序号重新使用；仅保存最近事件内容哈希和一个高水位，旧序号一律拒绝，不能查询或比较任意历史内容，也没有掉电持久化。

最近事件完全相同的重发不重复执行、不改证据发生/接收时间与到期时间；若判断时间前进，先按新判断时间执行到期并推进时钟。最后事件同 ID 内容变化拒绝，旧序号、旧域、时钟回退、未来发生/接收、非有限数值、布尔数值、额外字段拒绝。事务在深拷贝上验证并提交，失败不产生部分到期或状态变更。调用方要获取随时间变化的结果，须传入新事件或相同最后事件并给出判断时刻，单纯读取快照不读取墙钟。

## 输入种类

- `tick`：空载荷，仅推进时间与到期。
- `contact`：`state` 为 `confirmed/missing/unknown`；`heat`：`clear/interference/unknown`。两者载荷为 `state,source,expires_s`。`source` 为 `synthetic_external/operator/external_evidence`，发生时刻起最长 120 s，过期边界为 `now >= expires_s`。它们只是上游声明，未实现接触检测或热恢复检测。
- `local`：上述载荷加 `exposure=exposed/obstructed/unknown`，`state=wet/dry/unknown`。湿表示暴露探头受水，非确定下雨，亦不表示衣物吸水质量。探头遮挡/失效/过期时当前观测不可用；干探头不排除未覆盖区域下雨。曾受水 `ever_wet` 在同身份保留；连续湿滞留 `stuck_wet` 在新鲜、暴露、连续湿样本链达到 300 s 时为 true，在有效干态为 false，在观测缺失、遮挡、unknown 或过期为 null。这里的连续是样本新鲜窗口相接的合成软件假设，并非证明两次采样间无中断；失去连续性后不能检测期间是否出现新的受水事件。
- `fault`：`state=active/cleared`。故障撤销预测，清故障仅解除阻塞。
- `forecast`：`issued_s,available_s,valid_from_s,valid_until_s,expires_s,risk,source`；来源必须是 `synthetic_forecast`，风险是 `risk/no_risk/unknown`。签发不晚于可得，可得不晚于接收；可得至到期最长 120 s，窗口前输出 `upcoming_*`，窗口内输出原风险，到达窗口末端或 TTL 后不可用。接收的预报签发时间必须严格新于保存的预报。预报不证明本地下雨，也不阻挡本地湿润提示。
- `prediction`：`origin_s,expires_s,request`，request 是严格 `m6-v1` 的真实输入。相对时间到可信时钟的映射为 `absolute_s = origin_s + relative_s`，`origin_s + as_of_s` 必须等于事件发生时刻；已接收的分析可晚于发生但年龄须小于 120 s。M6 请求各观测的安装 ID 必须等于当前 installation，至少两个观测包中所有存在的样本都发生在最近失效时刻之后，参考起点不得早于失效，样本接收不得晚于分析时刻。合法的缺失样本传给真实 M6，得到信息不足并替换旧预测；畸形请求事务拒绝，不改状态。输入版本、对象字段和时序经 P1 与真实 M6/M2a 验证，有限候选、先验、历史、假设及显式未来条件保留。源请求的候选初态和未来只是条件假设；不可放生产端真值、未来实况或标签。`rain_kg_s` 如在 M6 输入中出现，是调用方显式的质量流条件假设，与本地湿/干二值无自动映射。

## 生命周期及输出

启动、接触缺失/未知、换域、受水、探头遮挡/故障/过期、接触或热声明过期、热干扰、故障和预测到期撤销已有预测；每次有效撤销推进失效时刻。只有当前接触确认、热干扰排除、暴露探头有效干态且无故障才能重新计算。tick、重传、清故障、恢复接触和探头变干不会自行重新计算。新请求样本须严格晚于失效时刻；同刻新样本不视为新的独立证据。预测有效期取请求到期、三个状态声明到期和最早样本年龄到期的最小值；M6 版本失配或非法请求回滚事务，M6 缺失时撤销为 unavailable，M6 计算失配/失败/未知保留其原始结果和未知状态。条件有限上界也不称已干；`collection_authorized` 始终 false。

快照分开提供 `prediction`, `forecast`, `local_wetting`, `observation` 和 `collection_authorized`。`intents` 为当前意图：受水优先级 1，预报 2，预测和预测撤销 3；键按完整域及生成事件 ID 形成；含证据时间、有效期和撤销原因。预测撤销复用被撤销预测的键，可区分 `kind=prediction_revoked`；新预测取代旧预测时也生成 `superseded` 撤销，即使两次分析同刻也使用不同事件键。只保存最后一次撤销意图，且到期不再显示；它是有界内存快照，不是持久队列，不保证多个撤销的可靠传递。连续湿链使用首个湿事件键，后续湿样本更新证据时间及到期，但不变更其去重键。P2 必须独立处理持久序号、域转换、所有待撤销 head、消息 TTL、ACK、去重和能量准入；P1 不声称 T07 已接通。

该实现用 Python 对象、完整 M6 请求和结果；没有 MCU RAM 测量、掉电恢复或可信时钟实现，也没有自动感测、真实衣物实验、实测准确率、网络传送、手机接收或真实环境真值。示例 20 s 是合成历史采样范围；解析构造的 1210 s 也是模型内假设，不是实际干燥时长。
