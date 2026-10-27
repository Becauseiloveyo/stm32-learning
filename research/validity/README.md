# T03 适用性与误差复算

全部是 **SYNTHETIC_UNCALIBRATED** 假设情景；没有真实织物实验、干燥时长、整机准确率或收衣授权。正式推导、数字与未实现边界见 [MODEL-VALIDITY](../../docs/MODEL-VALIDITY.md)，一手资料已读范围见 [sources](sources.md)。

在项目根目录运行（Python 标准库，无网络或新依赖）：

```powershell
python -B -X utf8 research/validity/run.py
python -B -X utf8 -m unittest discover -s research/validity -p "test_*.py" -v
python -B -X utf8 research/validity/run.py --verify
```

生成命令只写本目录 results。`--verify` 核对上游／自身／输出 SHA-256，重放保存的合法观测，再在内存完整重算并逐字节比较全部输出、清单元数据；成功和失败均不修复文件、不改时间戳。程序禁止 Python 字节码落盘。修改本目录程序、测试、来源或 README 后须重新生成，再验证。

两种时序来自当前 `theory.run_simulation.verification_protocols()`：默认及 three_second_long；MeasurementProtocol 是唯一时序来源。墙源积分最大内部步长 0.05 s 是数值精度设置，不是另一个采样／窗口默认值。实际积分还受各动态时间常数的 1/10 上限约束。

输出含 70 个完整周期尝试：8 个局部传感器响应、36 个 RH／温度读数偏置、12 个缓变偏置、10 个有限壁源、4 个超域反例。66 个适用周期向当前 `model.assess` 传入 11 项合法观测；4 个超域案例记录 NOT_APPLICABLE、空分类和原因，不裁剪、不提交残缺周期。该适用性标签是研究包装状态，不是新增产品状态。共同环境阶跃的 10 例和温度隔离恒等式的 21 行只算误差／表面势，不分类。所有收衣授权始终 false。

| 文件 | 内容与单位 |
|---|---|
| results/manifest.json | 输入、来源、输出哈希，协议快照／窗口，全部周期参数、种子、单位、数量、数值方法 |
| cycle-summary.csv | 完整周期尝试、观测判定、上升／尾部 Pa、壁源质量残差 ug；不适用留空 |
| observations.jsonl | 每周期包装含 11 字段合法观测及重放结果，无物理真值字段 |
| wall-traces.csv | 单独研究真值：气体／传感器 Pa，壁库存 ug、释放 ug/s，有符号织物／出口转移 ug，守恒残差 |
| response-error.csv / response-summary.csv | 同一真实气压阶跃下双传感器失配；误差 Pa，时刻 s，无分类 |
| temperature.csv | 温度读数偏置、真实温差同分压、真实表面温度势三类隔离计算，无动态热模型 |
| uncertainty.json | 假设的单点残余测量标准不确定度、协方差和灵敏度；未知模型误差与整机准确率为 null |

路径约定：`input_sha256` 相对项目根，含 T02 已验收清单的传递输入以防基线漂移；`source_sha256` 相对 research/validity；`output_sha256` 相对 results。输入清单校验不等于重跑旧测试。噪声设为 0；主模型仍显式使用其默认种子 20261023，新增扩展无随机数。

有限壁模型仅支持定温、定环境、固定表面势和单腔；新增壁库存 `M'=−M/τw`。初始气体和传感器设为**初始瞬时壁源**对应的被动平衡，随后壁源从 t=0 持续衰减。它不是有限库存可长期保持的平衡历史。扩展拒绝另外的恒壁源、读数偏置、噪声和事件，以免默默忽略参数或重复加源；需要测量误差时另由显式观测变换施加。

测试包括独立指数解析解、相等三时间常数解、独立 Simpson 通量积分、零源／长寿命极限、步长收敛、协议变化、偏置恒等式、协方差半正定／重复项边界、真值隔离、只读重算及篡改检测。质量残差很小只证明数值实现满足所写方程，不证明方程或假设代表实物。

不生成截图或沿用旧图作为当前证据。当前文档表格由本轮 CSV／JSON 数字整理；完整热平衡、双腔、冷表面凝结、吸附再湿润、未知漏气／织物覆盖和装壳动态仍需独立数据。
