# 中国体彩足球预测与复盘工作台

工作台读取现有权威 JSON，默认把来源、缓存、候选、模型、复盘和备份写入 `workbench/runtime/`，不引入 SQLite。进入页面单次获取官方当前赛单，不持续轮询；预测和赛后复盘分别由点击触发，正式冻结默认关闭。

历史查询操作见 [HISTORY-PREDICTIONS.md](HISTORY-PREDICTIONS.md)。本地交接和验收记录另存于当前工作目录；公开分发范围见项目根目录的 `DATA_NOTICE.md`。

## Windows 启动

PowerShell 进入 `workbench` 后执行：

```powershell
.\setup-windows.ps1
.\start-windows.ps1
```

浏览器打开 `http://127.0.0.1:5173`。后端文档为 `http://127.0.0.1:8000/docs`。两个服务均只监听本机。

## macOS 启动

```bash
chmod +x setup-macos.sh
./setup-macos.sh
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 新终端
cd frontend && npm run dev -- --host 127.0.0.1
```

## 当前日常工作流

1. 核对官方赛单并选择编号日期或北京时间开赛日，保留比赛顺序。
2. 点击“运行自动预测”后，所有身份与时间有效的赛前场次主动核验官方事实并重新动态检索，包括已有登记模型的比赛。模型损坏、未来数据泄漏等安全失败不能借此绕过；已经开赛或分析完成时已越过开球的场次不会补作赛前候选。
3. 首次建立赛事历史基础时采集2025年起的真实来源，随后预测复用历史缓存与相同输入下的拟合参数。伤停、停赛、转会、教练、阵型、欧亚盘等动态信息每次建立新的检索请求，不用上一轮新闻缓存冒充本轮核验。
4. 每个赛事独立建模并保留球队身份、赛季、阶段和青年届次区别；实际样本、来源、排除原因和参数版本均可追溯。未知映射不模糊猜测，样本不足或先验主导时如实标记。
5. 独立模型保存研究概率，Astra结合可追溯事实说明支持、反证及其影响。定量概率与定性方向分开保存；只输出胜平负、让球胜平负两项，未开售、让球缺失或证据不足保留状态，不补造正式选项。
6. 保存独立草稿和本次来源，逐场可展开动态检索、模型覆盖、概率、反证、缺口与候选经验；不自动写入正式历史。
7. 关闭后重新打开，进入左侧“历史预测”浏览全部草稿、正式版本及赛前原始存档；按类型、日期、队伍或赛事筛选后打开完整详情。读取不重新调用模型。原“历史与版本”改为“正式记录明细”，原数据保留。

详细来源及模型限制见 [HISTORICAL-MODELS.md](HISTORICAL-MODELS.md)，动态调用和来源验证见 [LIVE-RESEARCH.md](LIVE-RESEARCH.md)。历史数量以当前页面和 `GET /api/historical-coverage` 对应缓存版本为准；专项文档数量是注明日期的核验快照，不表示完整全球覆盖。

## 历史缓存与早期赛季工具

新历史通道从2025-01-01开始，首次采集后保存不可变来源与版本。后续预测复用缓存；失败或零样本也记录，不每次重新下载同一历史基础。明确重建使用历史服务的 `rebuild=True`，详见历史专项文档。180天仍用于部分近期状态摘要，不是新历史模型的总时间范围。

`runtime/imports/season-2025-2026-manifest.json` 保存早期导入批次。当时2,889场原始比赛、2,880场规范化记录只代表该归档批次，**不是当前历史总数**。旧来源、预测引用与模型保留追溯，不覆盖或删除。

早期赛季导入与训练命令继续保留为手工工具，不是新通道每次预测的入口：

```powershell
cd backend
.\.venv\Scripts\python.exe -X utf8 -m app.cli sync-season-datasets
.\.venv\Scripts\python.exe -X utf8 -m app.cli train-season-models
```

## 手工数据和训练

1. 将可核验历史比赛转成 `runtime/imports/training_template.csv` 的列格式。`status` 只有 `completed_90` 会进入训练；加时、点球、取消、未确认记录都会排除。同一 `match_id` 只保留一条，不会把 V1/V2 当两场比赛。
2. 分别训练基准和候选模型：

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli normalize-football-data ..\runtime\imports\raw\E0.csv --competition "英超" --source-url "原始下载URL" --output ..\runtime\imports\clean\E0-normalized.csv
.\.venv\Scripts\python.exe -m app.cli train ..\runtime\imports\training.csv --competition "联赛名" --model poisson --name league-poisson-v1
.\.venv\Scripts\python.exe -m app.cli train ..\runtime\imports\training.csv --competition "联赛名" --model dixon_coles --name league-dc-v1
```

3. 按时间顺序执行重建式回测（不等于真实赛前冻结实盘）：

```powershell
.\.venv\Scripts\python.exe -m app.cli backtest ..\runtime\imports\training.csv --competition "联赛名" --model dixon_coles --min-train 80 --refit-every 20 --output ..\runtime\reviews\backtest-dc.json
.\.venv\Scripts\python.exe -m app.cli compare-backtests ..\runtime\reviews\backtest-poisson.json ..\runtime\reviews\backtest-dc.json --output ..\runtime\reviews\model-comparison.json
```

4. 网页核对官方赛单后执行模式 A；高级 JSON 编辑也可手动输入赛单。所有有效赛前场次都进入事实和动态信息核验。官方让球缺失、玩法未开售、证据不足的行仍保留，不产生伪造选项。

## 动态检索、独立模型与Astra分析

动态检索使用本机已登录 ChatGPT 的 Codex CLI，搜索阶段只开放原生网页搜索。Python实际抓取候选页面并保存正文和SHA-256回执，再由禁联网、禁工具的提取调用核对事实。正文不可得、摘录不匹配、来源过期、球队级别不符或时间不能确认时保留缺失，不补写。具体来源合同、抓取边界和市场时点验证见 [LIVE-RESEARCH.md](LIVE-RESEARCH.md)。

官方、主流媒体、专门数据源和盘口来源分层保存。伤停空列表不等于全员健康，预计首发不当成确认首发，19维检查允许明确缺口。主动检索有范围、超时和来源限制，不声称穷尽全网。

历史模型按赛事独立学习进球基准与收缩球队参数。青年/亚运不同届次隔离，不用旧届队伍实力冒充当前阵容；转会、换帅、主场、首发等尚未完整进入数值模型，动态证据另作核验。时间切分评估不等于真实赛前实盘，也没有完成独立概率校准。训练、缓存、身份域及评估口径见 [HISTORICAL-MODELS.md](HISTORICAL-MODELS.md)。

`independent_model` 保存独立模型研究概率，`statistical_baseline` 保留近期统计。最终Astra解释采用独立的禁联网、禁工具调用，默认 `gpt-6-astra / xhigh`。搜索、正文提取和最终解释是不同阶段，真实调用次数与用量分别记录，不能把整批流程说成只有一次模型调用。CLI不可用、超时、引用错配、无来源数字或玩法矛盾时拒绝不合格输出，保留已取得的本地统计和证据，不伪装完成。

无需新增 API Key，会使用现有账户额度。Windows原生CLI路径可自动发现；其他安装布局未验证或未找到时明确报告不可用。定性方向不冒充校准概率，让平仍要求独立精确边界依据；分析候选保持真实的低样本/高风险限制，不直接正式冻结。

### 基本面与盘口参考

通常按基本面80%、市场最多20%组织**定性证据关注预算**。只有可信赛前赔率与实际赛果的配对样本达到代码规定的频繁爆冷条件，市场参考上限才升至30%，对应基本面70%。未知历史或球队名气不能触发升级，没有可用盘口资料时实际市场贡献为0。

这些比例不是概率混合系数，不线性融合胜率、不修改独立模型概率，也不替代体彩让球或固定奖金。单个盘口记录只能称为快照；同书商、同市场、同亚洲盘线、两个可靠观察时点才能比较数值变化。门槛与字段位于 `backend/app/services/market_context.py`，来源核验见动态专项文档。

## 手动赛后复盘

选择已保存研究草稿、历史独立版本或当天“最高合法赛前版”，点击“抓取赛果并复盘”。服务主动查询官方赛果及实际可用过程，核验比赛身份、90分钟比分（含补时）和完赛/兑奖状态，不将加时、点球大战混入结算。

报告展示原始理由和反证、实际比分、两项方向差异、逐玩法分母及排除原因，以及完整有效赛前概率的Brier SUM（0—2）和覆盖率。研究分析方向只计研究对照；赛后生成、保存时间无法核实或同版本冲突不计赛前成绩。未开赛、未发布、延期、取消、冲突分别列明，不把未知比分写成0:0，不跨版本择优补选项。

可勾选Astra对照原赛前事实、让球和官方进球时间线、阵容、技术统计等可得资料，将已确认偏差和待验证原因分开。过程接口为空就保留缺失；分析失败仍保存本地完整报告及失败原因。选择批次或读取既有报告不联网，不持续自动查询。

只有具体证据支持的经验才出现采纳按钮。经验保存到 `runtime/model_lessons/`，保留版本、采纳时间、赛事/让球/模型方法等条件、来源和不适用范围。后续预测先保存独立判断，再读取匹配候选并记录影响；单场不改概率或模型参数，不自动升级长期规则，也不修改全局 `data/review_rules.json`。没有有价值候选时不强行新增。

完整接口与存储见 [MANUAL-REVIEW.md](MANUAL-REVIEW.md)。当前入口为 `POST /api/manual-reviews/run`；旧 `POST /api/reviews/settle` 已停用并返回HTTP 410，不再接受客户端自填 `verified_90_minutes=true` 生成成绩。

## 官方数据

- 进入工作台单次获取当前官方赛单，也可手动刷新；不持续监控。
- `/api/official/import` 导入用户已核实的官方 JSON 快照。
- `/api/official/fetch` 只在请求体中显式传入 `authorized=true` 时执行一次获取。起始主机和每次重定向都要通过 `sporttery.cn` / `lottery.gov.cn` 真实主机名边界检查。响应、来源、获取时间、源更新时间和解析器版本会一起保存。
- 首次加载与“刷新官方赛程”会调用体彩官方计算器 JSON 接口，默认保留全部返回分组及顺序，填入赛单、让球、两项玩法奖金和销售状态，并保存原始快照。
- 页面可分别按“竞彩编号日期”和“北京时间开赛日”筛选当前快照。例如周二编号的凌晨比赛可能实际在周三开赛；不得按编号日期伪造实际开赛日。切换筛选、重新抓取、编辑输入或抓取失败都会使旧预测失效。
- 这是当前已公布的竞彩赛单，不是全世界赛程、历史查询或完整赛季日历。筛选没有匹配项不代表那天没有比赛。
- 冻结不联网。预测会主动核验官方事实与动态来源，手动复盘会主动抓取官方赛果和过程；读取既有报告不联网。赛单抓取失败清空当前赛单并显示失败；证据失败逐场记录，不静默拿过期资料冒充本轮核验。
- 后端入口为 `POST /api/official/slate`，请求 `business_date` 与 `date_basis`（`all` / `business_date` / `kickoff_date`）。旧调用省略 `date_basis` 时仍按竞彩编号日期筛选，网页显式使用 `all` 获取完整快照后本地筛选。接口不再依赖官方计算器未执行的日期查询参数。
- 校验官方 `totalCount`、分组 `matchCount`、重复 ID、日期及必备字段；有差异会返回明确的 `invalid_rows` / `validation_errors` 并令 `complete_for_scope=false`，网页禁止将不完整赛单提交预测。原始快照及每次获取回执保留。

日常预测把 `model_version` 设为 `auto` 时，登记基准只读取 `config/active_models.json` 中的活动研究模型；随后所有有效赛前场次进入独立历史建模与动态证据流程，新通道不覆盖活动登记表。`config/team_aliases.json` 负责体彩中文简称到训练队名的一对一映射，未知队名进入自动取证建模通道，不模糊猜测或换用其他联赛模型硬算。比赛日不晚于训练截止日时直接拒绝，防止历史回填发生未来数据泄漏。

原登记模型使用schema v2，保留为研究对照；世界杯登记保留中立场约束。schema v1模型禁止用于新预测，未登记旧模型默认从活动列表隐藏，可通过 `GET /api/models?include_archived=true` 查阅归档。旧模型、预测引用和回测依据保留，不由文件更新时间自动选用新版。

## A / B / C 边界

- A：保存轻量候选草稿，不写权威历史，不自动发起 B。
- B：只接收真实 schema v4 formal 文档，调用现有 `scripts/validate_prediction.py --mode formal` 同等逻辑；再检查开赛时间、权威历史父哈希、幂等键、同版本冲突，备份后原子替换。默认关闭；仅在用户明确要求真实冻结时设置 `SPORTTERY_ENABLE_FORMAL_WRITE=1`。
- C：由手动复盘服务核验90分钟赛果和赛前版本资格，待定/取消不写0:0、不进分母；事实确认与体彩官方结算分别保存。原预测保持不变，单轮不修改全局长期规则。

## 可选配置

启动后端前可设置：

- `SPORTTERY_DYNAMIC_RESEARCH=0`：关闭主动动态检索及由该开关授权的首次历史下载，保留可用本地资料。
- `SPORTTERY_EVIDENCE_LLM=0`：关闭最终证据解释及复盘Astra分析。此开关与动态检索独立；如要关闭所有CLI模型调用，应同时关闭上述两个开关。
- `SPORTTERY_EVIDENCE_MODEL`、`SPORTTERY_EVIDENCE_EFFORT`：显式配置已获授权的模型与最终解释强度；动态检索/正文提取最高使用medium，单独记录请求强度与实际强度，手动复盘模型固定Astra。
- `SPORTTERY_EVIDENCE_TIMEOUT_SECONDS`：相关单次模型调用上限，默认360秒、最大600秒。多阶段检索、抓取与分析会令整体请求更长；按实际记录显示调用状态和耗时。

## 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
cd ..\..\scripts
..\workbench\backend\.venv\Scripts\python.exe -X utf8 -m unittest test_validate_prediction.py
cd ..\workbench\frontend
npm run build
```

训练或测试通过只证明相应工程流程可运行，不证明预测效果。来源覆盖、模型表现和页面验收以实际运行报告为准；本README不宣称完整全网采集、独立概率校准或最终真实验收已经完成。手工回测是历史重建，各报告的Brier定义需按记录核对，不与其他评分口径直接混算。
