# P3 角色与界面数据契约

先看 TEAM、[任务范围](../../T11-P3-HANDOFF.md) 和[冻结输入](../baseline-t11-p3/snapshot.json)。所有时间为合成秒，电脑离线，收衣授权为 false。角色仅写列出的文件，报告交付后停止。协调者独占界面、源码绑定收据与全局记录。

- 数据工作者负责 `research/integration_t11/p3/projection.py`、`test_projection.py` 和 `docs/team/reports/projection-t11-p3.md`。实现 build() 返回 JSON 安全 dict，含 cases 数组，每项 {id,frames}。逐次运行真实 P2 Rig/P1，用有限个有意义的时刻输出可选择的帧，不预读未来。九条已验收回放必须一致，可增加有限边界情景，特别是预报风险/过期、不一致、缺接触、失配和满队列。每帧 {t,local,remote,saved_s,received_s,link,queue,drops,energy_j,capacity_j,actions}。local/remote 为 {boot,batch,installation,contact,heat,wet,forecast,prediction,reason,analysis_s,remaining_s,expires_s,conditions}，remote 无接收时 null。字符串状态采用原 P1 枚举，conditions 为可读的条件假设字符串或列表。remaining_s 只给有效 conditional 的 M6 upper_remaining_s，保留分析时刻。远端按已接收快照及查询时钟过滤每项过期证据，包括预报生效边界，不能偷取未同步本地证据；本地/远端剩余值仅代表分析时刻估计。actions 为本帧实际 trace 中 action 名的字符串数组。经济化压缩，保留关键到期与所有对 UI 有影响的时点。明确服务保存与模拟接收，未确认手机送达。新增字段可向协调者说明。
- 资源与验收工作者负责 `docs/T11-RESOURCE-REVIEW.md`、`docs/T11-ACCEPTANCE-MATRIX.md` 和 `docs/team/reports/resource-t11-p3.md`。从已接受来源逐项映射 ROADMAP T11 条件，给出确证路径、缺口与还需界面验证项。给出 MCU RAM/非易失队列与 GC 的可复算估算，列出结构/字段位宽、上限、计数、耐久和未验证项，核对已有 C 基线，不能把规划当实现。
