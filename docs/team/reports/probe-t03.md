# T03 模型适用性与误差交付

日期：2026-10-24。角色：气路／模型误差。状态：本角色交付已提交，等待协调验收；不代表全局 T03 已完成。

已读 PROJECT、STATUS、TEAM 的 T03 补充、任务书、ROADMAP 的 T03、T02 结论、旧适用性草案与来源登记。只写 `research/validity/**`、`docs/MODEL-VALIDITY.md` 和本报告；theory、research/probe、草案、旧报告均只读。

开始时 `python -B -X utf8 tools/project_status.py` 退出 0：T03 进行中，既有主模型 37 项、气路 25 项及 T02 清单指纹匹配。本轮没有重跑这些旧检查。

## 交付与范围

正式文档见 [MODEL-VALIDITY](../../MODEL-VALIDITY.md)。已完成任务书范围内的受限扩展、敏感性、误差预算、适用／不适用边界与可复算产物。当前代码没有修改主模型、协议或产品状态接口。

| 交付 | 实际文件 |
|---|---|
| 正式适用性与推导 | docs/MODEL-VALIDITY.md |
| 标准库生成／只读复算 | research/validity/run.py |
| 19 项独立与边界检查 | research/validity/test_validity.py |
| 入口、参数边界与来源 | research/validity/README.md、research/validity/sources.md |
| 结果（7 个输出及清单） | research/validity/results/manifest.json、cycle-summary.csv、observations.jsonl、wall-traces.csv、response-error.csv、response-summary.csv、temperature.csv、uncertainty.json |
| 本次交接 | docs/team/reports/probe-t03.md |

上述为本轮全部写入范围。theory、research/probe、旧草案／报告、全局状态／决策／路线图／团队文件和主测试收据保持只读。没有新建对话或代理、跨对话发消息、提交／重置／清理、采购、设备操作或部署。

## 输入、来源与可追溯性

本轮已核读三项一手来源指定范围：Sensirion SHT4x V7.3（2026-06）p4/p6/p7，Sensirion Design Guide V2（2024-03）§5–5.1，NIST TN1297 Appendix A 的 A.1–A.5。链接、页码、定义及取得范围见 sources.md；不把典型精度当标准差，不推断装壳参数。Magnus 原文全文本轮没有取得，沿用主程序近似但未量化近似误差；不复用旧人体蒸发数值作为织物参数。

输入哈希覆盖 43 项，含 T02 传递输入、2 个 T02 源码、8 个 T02 输出和当前协议／模型。自身来源哈希 4 项（run.py、tests、sources、README），结果哈希 7 项。清单路径根严格按任务书约定。当前清单 SHA-256：`160d0e99f2de0b13dea6510b45c6723f6d38e09b0629c361e4041e45c2c53652`。

两协议均从当前 `verification_protocols()` 派生，不手抄草案旧窗口：default 指纹 `286acc0970edd40ace775bd55d1bca9bc34f603e29c863d9d3c99ac772e261bb`；three_second_long 指纹 `e6d6392dfa1cb10c0a1fd2262fe8bda4174099fa938819c755be9acacad6fdd5`。完整参数、采样／窗口时刻、单位、种子均在清单；所有新幅度、时间常数和相关系数是假设。

## 本轮实际检查

环境：Python 3.14.6，Windows AMD64；仅标准库。所有下列命令从项目根执行。

| 命令／检查 | 实际结果 |
|---|---|
| `python -B -X utf8 tools/project_status.py`（开始时） | exit 0；T03 in_progress；既有 37／25 项收据与来源匹配。这不是本轮重跑这些测试 |
| `python -B -X utf8 research/validity/run.py` | exit 0；生成 70 个周期尝试，66 个合法周期、4 个超域不适用；980 行壁源轨迹、10 个阶跃误差情景、21 行温度隔离结果 |
| `python -B -X utf8 -m unittest discover -s research/validity -p "test_*.py" -v` | 最终 exit 0；**19 tests，全部通过** |
| `python -B -X utf8 research/validity/run.py --verify` | exit 0；输入／自身／产物哈希、保存观测重放、全部产物内存重算及清单比较通过；不写文件 |
| 正式文档、README、sources 的本地 Markdown 链接检查 | 无断链；相同真实分压下 25.5 °C 对应 RH 独立核算为 58.2409660383% |

初次测试曾因峰值测试夹具把旧近似值写成过多有效位而失败 1 项；已改为独立闭式峰值 `300(1−r)r^[r/(1−r)]`（r=4/30）并保留合理精度的数值检查。修正后重新生成（更新测试源码指纹）、19 项全部通过，再独立执行 `--verify` 成功。未通过放宽物理判据改变结果。

测试证据覆盖：非相等指数率的气体／传感器解析解、三时间常数相等的退化解、S0=0 回归主模型、长壁寿命的恒源极限、独立 Simpson 通量积分、RK4 步长减半收敛、两协议及分数采样窗口、漂移均值时刻公式、温度恒等式、协方差偏导／半正定、重复机制与非残余项拒绝、真值字段污染不影响判定、超域不裁剪、只读核验前后哈希和时间戳不变。篡改测试即使同步更新结果哈希，也会被完整重算发现。测试临时目录均在拥有范围内。

没有执行协调者专用 `--record-validity-tests`，没有写 `docs/verification/validity-tests.json` 或主收据。既有 37／25 项检查本轮没有重跑。

## 主要结果与限制

- 66 个合法周期保存 6468 条观测，每条严格 11 字段；29 个 UNKNOWN、27 个 VAPOR_SOURCE_PRESENT、10 个 LOW_SIGNAL_UNCONFIRMED。4 个不适用不提交残缺观测，也不伪造状态。隔离误差的 10 例／21 行不分类。**所有收衣授权 false**。
- τs=120 s 时，默认协议 rise=78.231935 Pa、状态 UNKNOWN；长协议 rise=126.417490 Pa、状态 VAPOR_SOURCE_PRESENT。时间窗变动影响演示状态，不能当成物理标定。
- 实际同分压时仅加 +0.5 °C 温度读数误差产生 +57.295764 Pa 假差；正确真实 T/RH 配对仍得到零差。真实表面温度则改变表面势，三者已分开。
- 双路 30/4 s 响应遇 −300 Pa 共同阶跃，连续假差峰值 190.699054 Pa；这个反例只算动态误差，没有完整周期分类。
- r=0.5 Pa/s 缓变造成默认／长协议 rise 偏移 41.25／55.5 Pa；固定 Pa 偏置可抵消 rise，缓变不能。
- 有限壁源 S0=0.1 µg/s、τw=600 s、初始库存 60 µg，在环境平衡表面也得到默认 rise/tail=131.331424/146.724142 Pa 和 VAPOR_SOURCE_PRESENT；长协议为 157.590507/174.526621 Pa。信号无法由此归因衣物。壁库从 t=0 开始衰减，初始气体采用瞬时源被动平衡，不是旧草案恢复起点的历史。
- 980 行壁源轨迹最大质量残差 1.1349948758621053e−13 µg。该数值是数值守恒一致性，不能作为材料模型误差或实物精度。
- 假设单点 uRH=1 个百分点、uT=0.1 °C，独立两探头得 47.488662 Pa；相同量跨探头 ρ=0.8 时为 21.237575 Pa。保留协方差，不把显式动态／偏置再加成噪声；未知模型误差和整机准确率为 null。没有周期时间协方差，不能直接按 √N 缩小。

未解决：装壳响应、RH/T 动态耦合、织物传质／覆盖、漏风、壁源参数／再吸湿、冷表面凝结、热量闭合、背面交换、独立判干与完整衣物含水量。新增研究扩展仅支持定温、固定环境和单腔。完整热／双腔模型明确暂缓；正式文档列出守恒候选关系、不可辨识量和后续最小实验。当前理论任务不触发这些硬件实验。

## 协调者验收入口与停止点

建议首先审阅 MODEL-VALIDITY §5–7 与来源范围，再运行：

```powershell
python -B -X utf8 tools/project_status.py --record-validity-tests
python -B -X utf8 research/validity/run.py --verify
```

第一条会写协调者拥有的独立测试收据，**本角色未执行**。协调者需独立核对质量推导、初态、误差分项、已实现／未实现保护区别，以及当前清单指纹后，更新 T03-review 与 STATUS。若接受，再指定确切下一步 T04；本角色已在 T03 提交处停止，没有提前实现 T04，也没有标记全局任务完成。
