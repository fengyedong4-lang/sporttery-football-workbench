# 手动赛后复盘

页面的“手动赛后复盘”支持选择已保存研究草稿、某日某个历史版本，以及“最高合法赛前版”主统计批次。点击“抓取赛果并复盘”才查询官方赛果与过程；选批次和加载既有报告只读本地文件，不会持续联网。

研究草稿中正式选项与 `analysis_result` / `analysis_handicap_result` 有明确区分。后者只计研究对照；历史正式成绩只用开球前合法冻结的记录，不回填或覆盖赛前预测。赛后生成、保存时点不明、时间无时区的记录排除成绩分母。历史有效版本选择复用项目 `scripts/validate_prediction.py::select_effective_versions`，冲突日期不生成最高版本批次。单独历史版本仍可打开，但不与主统计混算。

## 官方来源与90分钟口径

2026-09-22 实际核验的公开页面：

- 赛果页：`https://www.sporttery.cn/jc/zqsgkj/`，全场比分列明确标记“90分钟”。
- 页面脚本：`https://static.sporttery.cn/res_1_0/jcw/default/jc/sgkj/jc_sgkj_gz.js`，`sectionsNo999` 对应全场比分；状态 `0`、`1`、`3` 分别非完成、待开奖、暂停兑奖。本适配器仅接受已观察到的 `matchResultStatus=2` 且 `poolStatus=Payout`，而不是把任意未知状态当作完成。
- 赛果接口：`https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry`。按日期与分页读取，固定参数 `isFix=0&matchPage=1&pcOrWap=1`，保存响应哈希及获取时间。
- 官方直播组件：`https://static.sporttery.cn/res_1_0/jcw/default/jc/zqzbComponent.js`，提供以下接口路径及阶段说明。
- 终态及分节比分：`/gateway/uniform/fb/getMatchScoreV1.qry?matchId=...`。
- 进球与事件：`/gateway/uniform/fb/getMatchEventV1.qry?matchId=...`。只保留上/下半场阶段 `1`、`2`，包括补时；排除加时 `3`、`4` 和点球大战 `5`。
- 技术统计：`/gateway/uniform/football/matchlive/getTeamStatisV1.qry?gmMatchId=...`。
- 阵容：`/gateway/uniform/football/matchlive/getPlayerStatisV1.qry?gmMatchId=...`。

全部请求限制为体彩官方域名，校验重定向、数字比赛 ID 和返回身份，限制响应大小、页数和超时。ID 缺失的历史记录仅可用官方全称/简称、编号、日期精确匹配；不模糊猜别名。重复冲突、未知状态、空比分、取消、无效场、延期不算 `0:0`，不计未中。

实际本地验收：研究草稿 `87deeb13299243b2887af0224975fafd` 中韩国亚—沙特亚匹配官方 `2041642`，90分钟 `2:0`，已完成兑奖；官方事件为主队67及69分钟进球。该草稿此场没有形成两项选项，故成绩分母仍为0。技术统计与阵容接口为空，未补造。其他尚无已完成赛果的比赛保持待确认。

## 两项统计与原因分析

每场保存原始理由、原始反证、两项选项、实际比分和方向、逐玩法命中/排除原因。让球使用**赛前保存**的官方主队让球 `h`，比较90分钟净胜球 `d+h`，不采用赛后接口的让球替换。

Brier使用三类平方误差**求和**（0—2），只接受完整有效的赛前概率。`independent_model.raw_probabilities` 优先，研究值用0—1；历史 `three_way_probabilities` 用0—100。Astra不输出、回填或改变这些概率；报告提供概率样本数、覆盖率和排除说明。

错因分为已确认输出偏差、赛前有记录支持的问题和待验证假设。过程接口无条目不意味着没有事件，进球事件不齐则保留时间线缺失。技术统计保留官方原始字段，未确认的字段及统计范围不会补猜。Astra接收已保存的赛前事实、理由、反证、确认赛果和带来源的过程事实；它只能作定性对照，不因相关性宣称因果。

勾选Astra后只有一次有界调用，固定 `gpt-6-astra`，复用既有登录态、环境白名单、工具事件拒绝及错误过滤。临时隔离目录、只读沙箱、禁联网、禁工具、多代理、插件、记忆，未放宽原安全防线。身份/schema/数字/过程引用不合格时拒绝解释输出；超时、登录不可用或服务错误时保存完整本地复盘及实际失败原因，不宣称Astra完成。

## 经验加入模型

不是每个错误都生成经验。Astra只有在确实存在可追溯的赛前记录和具体过程证据时，才能提出具体动作、触发场景、不适用条件，并引用本场过程 `fact_id`。无可用证据则候选为空。

“将此经验加入对应赛事模型”保存到 `runtime/model_lessons/<lesson_id>.json`，附源报告哈希、源记录哈希、来源、版本、采纳时间、比赛去重ID。重复点击同一候选幂等。状态始终为 `provisional`，单场仅作个案，概率调整禁止，增益无法评估。此动作不修改模型参数、原始预测、全局 `data/review_rules.json`。

后续预测调用：

```python
from app.services.manual_review import load_applicable_lessons
context = {**fixture_dict, "model_method": independent_model.get("analysis_method")}
lessons = load_applicable_lessons(runtime_dir, context)
```

所有已保存 scope 字段精确匹配，缺字段不当通配；同赛事/让球/模型方法/赛制或强弱描述必须一致。采纳时间必须早于将要预测的开球。返回的是待核验个案经验，调用方仍须核对自然语言触发条件与不适用条件，先存独立判断，再保存使用ID、实际效果或未应用原因；不能直接将提醒转换为固定概率偏移。

## 接口合同

在 FastAPI 主程序 `include_router(review_routes.router)`，前端 `import ReviewPanel from './ReviewPanel'` 并渲染 `<ReviewPanel />`。

| 方法 | 路径 | 输入 / 输出 |
|---|---|---|
| GET | `/api/manual-reviews/batches` | `items`，批次ID、kind、label、场数、来源哈希 |
| POST | `/api/manual-reviews/run` | `{batch_id, use_astra: true}`；返回完整报告，同步有界请求 |
| GET | `/api/manual-reviews/reports?batch_id=...` | 本地报告版本列表 |
| GET | `/api/manual-reviews/reports/{report_id}` | 校验哈希后返回既有报告 |
| POST | `/api/manual-reviews/reports/{report_id}/lessons` | `{match_id,candidate_id}`；`adopted` 或 `already_adopted` |

报告路径 `runtime/reviews/manual/<report_id>.json`，官方原始快照 `runtime/reviews/manual_sources/*.json`。每次执行独立UUID版本，报告哈希基于去除 `report_sha256` 的规范JSON。读取/采纳会再次核验。最终保存前重新检查源批次哈希，变动则拒绝错误关联。

本模块不自动改名、删除文件、提交Git或修改历史库。

## 验证

运行 `workbench/backend/.venv/Scripts/python.exe -m pytest`，专门测试文件为 `tests/test_manual_review.py`。覆盖两玩法分母、Brier SUM、官方状态/取消/空值、赛后泄漏、身份冲突、研究分析方向、独立概率优先、原稿不变、网络失败报告、经验幂等/条件匹配/哈希、Astra只读无工具且引用受限。前端以 `npm run build` 校验。
