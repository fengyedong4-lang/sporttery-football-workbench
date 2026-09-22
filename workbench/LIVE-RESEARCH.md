# 动态检索接口

`backend/app/services/live_research.py` 提供每次点击预测时重新检索的独立服务：

```python
collect_live_research(
    fixtures,
    *,
    evidence_dir,
    model="gpt-6-astra",
    effort="medium",
    timeout_seconds=240,
) -> dict
```

`fixtures` 可传 `Fixture` 对象或字典；每项至少有 `match_id`、`competition`、`home_team`、`away_team`、`kickoff_time`。服务不读取旧动态新闻缓存。每次调用建立新的 `request_id` 和请求目录；相同 URL 只在本次调用中复用。

检索按每场一个独立小批执行，最多三场并行；每场搜索提示限制八个搜索/打开动作。调用方传入的 `effort` 高于 `medium` 时，动态采集内部封顶为 `medium`，批次同时记录 `requested_effort` 和实际 `effort`；后续证据定性分析可继续使用调用方配置的更高强度。

检索分两步：

1. 已登录 ChatGPT 的本机 Codex CLI 使用原生 `--search`，按每批最多三场搜索伤停停赛、转会、教练、阵型、爆冷历史和公开欧赔/亚洲盘来源。
2. Python 实际抓取候选 URL，保存正文与 SHA-256 回执。随后一个禁用联网和工具的 Codex 调用只从已抓正文提取事实。抓不到正文、正文片段不匹配、摘要数字不在正文、来源过期或球队级别不符时，事实不会进入结果。

搜索调用只允许原生 `web_search` 事件。`shell_tool`、插件、应用、浏览器控制、计算机控制、多代理和记忆均禁用；环境变量只转发 Codex 登录查找和 Windows 进程必需项，不转发 API Key 或代理凭据。URL 抓取拒绝私网、本机、非 HTTP(S)、带账号密码、非常用端口和非文本响应，不登录、不越过付费墙、不发送消息。

本机 TUN/Fake-IP 会把公网域名映射到 `198.18.0.0/15` 或 `fdfe:dcba:9876::/48`。遇到这两个固定合成网段时，服务先通过固定 HTTPS DoH 核验原域名的真实地址全部为公网地址，再把正文传输固定交给 `https://r.jina.ai/`；回执保留原始 URL、`retrieval_method=jina_reader_fake_ip` 和中介地址。普通 `127/8`、`10/8`、`172.16/12`、`192.168/16`、`169.254/16`、链路本地和 IPv6 私网仍拒绝，原始 URL 及每次重定向都执行同一检查。

## 返回合同

批次级关键字段：

```json
{
  "request_id": "live-...",
  "started_at": "...",
  "completed_at": "...",
  "status": "completed | partial | unavailable",
  "dynamic_refresh": true,
  "cache_policy": "no_cross_request_cache; same_request_url_dedup_only",
  "calls": 2,
  "usage": {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0
  },
  "fixtures": [],
  "sources": [],
  "attempts": [],
  "request_dir": "..."
}
```

每场包含同一 `request_id`、`started_at`、独立 `status`，以及：

- `dimensions.injuries/transfers/coach/formation/market/upset_profile`：均有 `attempted`、`found`、`missing`、`source_ids`。
- `facts[]`：含 `dimension`、`summary`、`support_excerpt`、主客映射、球队级别、`scope`（`matchday/recent/long_term`）、有效期、确认状态、`source_ids` 和完整 `sources` 引用。
- `sources[]`：含 URL、真实页面标题、`published_at`（无法解析时为 `null`）、`fetched_at`、来源级别、来源级别声明及是否经域名验证、正文哈希和回执路径。
- `market.observations[]`：含 `fixture_id/source_id/bookmaker/market_type/observed_at/published_at/valid_time/line/home/draw/away/support_excerpt`。
- `market.comparisons[]`：只有同一书商、同一市场、同一亚洲盘线、两个不同观察时间且数值确实变化时才生成。单快照永远不称为变化。媒体只有升降盘文字时保存为 `market` 事实，不补造数字。

事实引用的来源必须明确归属于该场 `fixture_id`，主客队映射也必须被来源映射覆盖。有效期必须覆盖目标开赛时点。已公告、尚未生效但会在开赛前生效的转会可保留为 `temporal_status=announced_future_effective_by_kickoff`，同时标记 `currently_effective=false`；公告发布时间必须早于检索时间，生效日期必须能在正文中核对。

`retrieval_snapshot` 的 `observed_at` 由程序强制设为来源 `fetched_at`，不接受模型自填的历史时点；`source_timestamp` 必须在正文中同时找到日期和时间且不得位于未来。`valid_time` 同样必须由正文支持。

`source_level=official/major_media/specialist_data/odds_source` 只有域名在代码白名单内才保留，否则降为 `other` 并设置 `source_level_verified=false`。`other` 和赔率站不能把伤停标成权威确认。已过期伤停、比赛日之后生效的资料和超出维度有效期的旧资料会被过滤。

## 已核验域名注册

下表链接只用于核验域名身份，不是任何比赛的人员、伤停或战术事实：

| 级别 | 域名 | 域身份核验页 |
|---|---|---|
| official | `jfa.jp` | <https://www.jfa.jp/eng/national_team/u21_2026/asiangames_2026_men/> |
| official | `thecfa.cn` | <https://www.thecfa.cn/cjbss/index.html> |
| official | `mkdons.com` | <https://www.mkdons.com/club/> |
| official | `gtfc.co.uk` | <https://gtfc.co.uk/> |
| official | `nottscountyfc.co.uk` | <https://www.nottscountyfc.co.uk/club> |
| official | `wiganathletic.com` | <https://wiganathletic.com/latics-matchpack-content/latics-matchpack-01-official-wigan-athletic-supporters-club/> |
| official | `blackpoolfc.co.uk` | <https://shop.blackpoolfc.co.uk/page/dataprotec> |
| official | `crawleytownfc.com` | <https://www.crawleytownfc.com/club/> |
| official | `crawleytownfcshop.co.uk` | <https://www.crawleytownfcshop.co.uk/contact-2-w.asp> |
| major_media | `news.cn` | <https://www.news.cn/sports/zgzq.htm> |
| major_media | `xinhuanet.com` | <https://www.xinhuanet.com/?lang=cn> |

单条提取结果验证失败时，该条会被严格拒收并在对应比赛的 `missing/warnings` 记录原因；同一提取包中其他独立通过的事实和盘口观察继续保留。整体结构错误或未知 `fixture_id` 仍拒绝整包。

正文保存为 `sources/*.body`，相邻 `*.receipt.json` 记录最终 URL、重定向、抓取方法、Content-Type、字节数、SHA-256 及落盘后复核结果。`attempts.json` 保存每次 Codex 调用的阶段、时间、状态、搜索动作、用量和提示词哈希，不保存提示词或 CLI stderr；`batch.json` 保存整批合同。

此模块不判断胜负，不填写或推断中国体彩官方让球、销售状态、固定奖金，也不把市场信息当作主基本面。调用方可把通过校验的事实加入后续证据包，但仍需按项目规则处理反证、让平边界和正式冻结。

搜索阶段的8次动作约束是提示词预算，不是进程强制计数上限；实际动作写入 `attempts.json`，单次调用由超时终止。Markdown正文会先去掉链接地址与重复导航再截取16000字符；原始正文文件保持原字节与哈希，收据另记正文及截取长度。提取为空或资料缺失不会回退到编造事实。
