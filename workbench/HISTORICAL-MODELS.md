# 2025 年起赛事历史与自动模型

2026-09-22 实际实现和核验。历史采集只在首次建立该赛事基础或明确重建时运行；每次预测读取历史缓存与拟合参数缓存，近期状态、排名、伤停等由独立动态信息通道处理。

## 接口

`backend/app/services/historical_samples.py`：

```python
ensure_historical_samples(scope, *, evidence_dir, bundles=(), rebuild=False,
                          allow_download=False, cutoff=None, max_requests=24)
ensure_football_data_history(competition, *, runtime_dir, rebuild=False,
                             allow_download=False, cutoff=None)
ensure_manifest_history(competition, *, runtime_dir, rebuild=False,
                        allow_download=False, cutoff=None)
```

返回 `rows / sources / rows_sha256 / version / coverage / excluded / source_errors / historical_market_rows`。默认只复用缓存及本地已校验原始来源；`allow_download=True` 允许首次采集。失败或零样本也留版本，后续不会每次预测重试；需要 `rebuild=True`。指定历史 `cutoff` 只允许读取当时已经建立的缓存，不允许今天抓取后冒充历史时点已知。

`auto_model.build_auto_models(..., evidence_dir, allow_historical_download=False, rebuild_history=False, workbench_dir=None)` 已接上述通道。相同数据、赛事/赛季作用域和方法参数复用 `auto_models/fits` 中拟合及评估结果；每次分析仍独立留档，不覆盖旧模型或预测。

## 实际样本

以下是本次真实下载、日期筛选、去重和来源校验后的数量，不是全球完整覆盖声明。

| 赛事 | 样本 | 实际日期 | 赛季 |
|---|---:|---|---|
| 英超 | 622 | 2025-01-01—2026-09-20 | 2024/25、2025/26、2026/27 |
| 英甲 | 923 | 2025-01-01—2026-09-19 | 同上 |
| 德甲 | 513 | 2025-01-10—2026-09-20 | 同上 |
| 德乙 | 513 | 2025-01-17—2026-09-20 | 同上 |
| 意甲 | 632 | 2025-01-04—2026-09-20 | 同上 |
| 欧冠正赛 | 276 | 2025-01-21—2026-09-10 | 同上 |
| 日职 | 660 | 2025-02-14—2026-09-20 | 2025、2026 特殊赛制、2026/27 |
| 世界杯本赛 | 104 | 2026-06-11—2026-07-19 | 2026；2025 无本赛届次 |
| 英锦标赛 | 34 | 2025-01-15—2026-08-19 | 官方 11890、13729、14954 |
| 亚运男足 | 10 | 2026-09-15—2026-09-20 | 官方 15364，当前届 |

前 8 赛事共 4,243 场，另两个官方图样本 44 场。所有缓存 `coverage.complete=false`：尚未同官方全量赛程逐场对账，不能据日期跨度或某文件预期场数声称没有遗漏。

## 来源、身份与赛果口径

- 官方历史使用已核验的 `getMatchResultV1.qry`，`termLimits=100, tournamentFlag=1`。从当前正式比赛及原始凭据中的真实 `sportteryMatchId` 有界扩展，不猜 ID。仅同一个 `tournamentId` 入库，保留不同官方赛季 ID。
- 比赛详情的赛事 ID 属供应商域；球队优先使用 Sporttery ID。缺 Sporttery 对手 ID 时保留 `uniform:<id>` 独立实体，不将同值 uniform ID 和 Sporttery ID 连接，不按姓名猜球队。
- Football-Data 的 2425、2526、2627 CSV 使用 `fd:<league>:<name>` 域；欧冠、日职、世界杯使用 `ext:<competition>:<name>` 域。不同供应商数值 ID 不混用。仅复用 `config/team_aliases.json` 中显式队名映射，并将映射文件哈希、来源名称和官方 ID 存入模型分析；未知映射回到官方样本通道。
- 英超等 CSV 仅纳入 `FTHG/FTAG/FTR` 一致的已完赛记录。日职仅纳入来源标记 `試合終了` 的进球列。世界杯使用 `score.ft`，不使用 `et` 代替。
- 欧冠 2024/25 原始 feed 没有独立加时字段，用 OpenFootball 同季文本的加时/点球标记识别日期；因跨源队名未建立完整映射，保守排除该日全部比赛，实际取得 2025 年部分 72 场。2025/26 使用现有已核验适配器，186 场。2026/27 当前有 18 场完赛。资格赛不并入正赛训练。
- 日职 660 场包含 2025 的 380 场、2026 特殊分区及排名赛 200 场、26/27 已赛 80 场。保留 phase 标签，并如实说明跨赛制历史不是完全同质样本。
- 原始文件不可变保存，载荷/字节 SHA-256 与版本索引哈希重新验证；冲突重复比赛整组隔离，同日只有日期精度的比赛不进入当日训练。

来源 URL 已逐项保存在缓存 source 中，主要地址：

- `https://webapi.sporttery.cn/gateway/uniform/football/getMatchResultV1.qry`
- `https://www.football-data.co.uk/mmz4281/{season}/{league}.csv`
- `https://fixturedownload.com/feed/json/champions-league-{year}`
- `https://raw.githubusercontent.com/openfootball/champions-league/master/2024-25/cl.txt`
- `https://raw.githubusercontent.com/mokekuma-git/JLeague_Matches-Bar_Graph/main/docs/csv/26-27_allmatch_result-J1.csv`
- `https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json`

## 参数与评估边界

每赛事独立拟合收缩 Poisson；2025-01-01 以前样本不进入新通道。俱乐部跨季球队参数沿用相同已确认实体，转会与换帅尚未直接建模，因此保持低置信/高风险，动态证据独立复核。青年/亚运跨届样本的球队参数加届次命名空间，旧届不能为当前队伍提供实力；最多参与同赛事进球环境。若只有旧届样本，则明确 `prior_only=true`，不破除对称先验的并列方向。

评估按比赛日期前缀训练，同日不参与训练；最多评估最后 24 个不同比赛日，输出 Brier、log loss、命中率及同前缀赛事均值基准。8 个真实历史数据集均完成拟合及该时间切分评估，具体样本数和指标保存在 `runtime/historical_samples/validation-20260922.json`。这是事后整理历史数据的时间切分，不是重放当日来源可得性的在线检验，未完成独立概率校准，也不证明优于市场。

不拟合主场加成，场地、首发、伤停未直接进入参数；原有专属模型可以作为对照保留。让平还需相应精确边界事实，青年边界证据仅使用当前届。无官方让球不生成让球方向。正式冻结资格始终为 false。

## 历史欧赔接口

Football-Data 3,203 场均取得 `B365CH/B365CD/B365CA` 或 `PCH/PCD/PCA` 同组收盘列，未混用博彩公司列。`historical_market_rows` 含实际赛果、来源哈希、球队域、字段名和 `closing_verified=true`；精确赔率观测时间及开球时间没有来源字段时为 null，不伪造时间。该批历史欧赔不是体彩官方固定奖金。auto_model 输出只保留当前双方相关历史行，供主流程计算爆冷事实；不能只按球队名气增加盘口参考预算。

## 验证与保护

历史、自动模型、冷启动相关 28 项测试通过：缓存只采集一次、明确重建保留旧版本、参数复用、源文件篡改隔离、真实未来来源拒绝、2025 起止与去重冲突、不同 ID 域、青年届次隔离、既有显式别名、收盘时间未知、世界杯90分钟与加时分开、欧冠加时日期保守排除。历史总库、规则库、原活动模型、旧导出均未改写；没有 Git 提交、部署或正式冻结。
