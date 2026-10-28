# T09 设计计算与公开数据

**2026-10-28 范围调整：光伏方案已退出当前产品。** 此目录保留 T09 来源与结果用于历史复算，PVGIS/面积计算不再是下一任务；NASA 天气和太阳辐照仍可支持衣物热湿模型。当前供电及实施计划见 [无太阳能开发计划书](../../docs/NEXT-PHASE-PLAN.md)。

本目录不修改 T01–T07 已验收模型。配套设计：[干燥预测/雨情/供电](../../docs/PREDICTION-RAIN-POWER-DESIGN.md)；来源：[资料登记](../../docs/references/T09-sources.md)。

- `data/weather-source.json`：NASA POWER URL、单位、字段、原响应指纹；武汉城市示例点 2024 年五变量，各 8784 小时。
- `data/pvgis-sources.json`：PVGIS v5_3 ERA5 URL、六种姿态、原响应指纹；2023 年每姿态 8760 小时。
- `data/weather-retrieval-failure.json`：首次生成收据缺少本机时区数据库的错误记录；原始数据下载成功，已用明确 UTC+08:00 修复元数据。不是未解决的下载失败。
- `assumptions.json`：仅本轮面积/电池预算的未实测假设，不覆盖原 T05 配置；确定性计算无随机种子。
- `analyze.py`：只读本地数据、核对单位/时序/填充值/指纹并生成统计和面积预算。
- `results/summary.json`、`summary.md`、`manifest.json`：计算结果和输入/产物指纹。

运行：

```powershell
python -B -X utf8 research/design_extension/analyze.py
```

仅覆盖本目录 `results/` 的三个同名生成文件，不下载、不连接设备、不运行旧模型测试。外部数据更新需另行下载到新版本文件并更改来源记录，不能只刷新摘要日期。

NASA 小时太阳量为 `Wh/m²`；PVGIS 是 `W/m²` 的小时平均，乘 1 小时后积分。以 UTC 日期分组，两套不同年份的数据分别计算。任何缺失填充值直接使分析失败，不自动按晴天或零值补齐。

本轮没有执行完整衣物干燥预测，也没有取得实物衣物标签。雨头功耗、光电效率、受光折减、无线会话时长和名义电池容量都是声明的设计算例；结果不证明准确率、价格、全年续航或任何一个真实阳台的表现。
