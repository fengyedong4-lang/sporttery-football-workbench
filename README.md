# 体彩足球预测与复盘工作台

在本机查看官方赛单、生成带来源的研究预测、浏览全部历史版本，并手动对照赛果复盘。React / TypeScript 前端，FastAPI / Python 后端，JSON 保存记录。

## 功能

- **当日工作台**：获取官方公布赛单；点击预测后检索人员、战术与市场资料，建立赛事独立模型，再生成定性分析。
- **历史预测**：汇总工作台草稿、正式历史的所有版本和赛前原始存档；按日期、类型、队伍或赛事查询，分页打开完整只读详情。关闭页面后仍可查看，不会重新调用模型。
- **复盘与模型**：手动获取官方90分钟赛果，对比两种玩法、解释偏差；有事实支持的经验可由使用者选择保留。
- **来源与版本保护**：模型概率、定性分析、正式冻结分开；资料不足显示缺口，原预测和旧版本不覆盖。

## 启动

已验证环境为 Windows、Python 3.12、Node.js 24。先安装 Python 和 Node.js，再执行：

```powershell
cd workbench
.\setup-windows.ps1
.\start-windows.ps1
```

打开 <http://127.0.0.1:5173>。后端接口文档：<http://127.0.0.1:8000/docs>。服务只监听本机，不包含用户认证，不能直接作为公网服务部署。

macOS 安装与手动启动命令见 [工作台使用说明](workbench/README.md)。本项目的浏览器和网络联调以 Windows 为准，未宣称完成 macOS 实机验收。

### AI 与联网

动态检索和解释需要本机可用且已登录的 Codex CLI，以及账号支持的模型。项目不附带账号、密钥或额度，不代用户登录。模型名称可通过环境变量配置，能否使用以本机账号实际返回为准。读取已保存历史不需要 AI。

如只查看存档、运行本地模型或测试，可先设置：

```powershell
$env:SPORTTERY_DYNAMIC_RESEARCH = '0'
$env:SPORTTERY_EVIDENCE_LLM = '0'
```

这两个开关关闭 AI 检索与解释；进入当日工作台仍会单次请求官方赛单。没有网络时会显示来源错误，历史浏览仍读取本地文件。

## 数据布局

| 目录 | 内容 |
| --- | --- |
| `data/prediction_history.json` | 唯一有效历史记录源，保留各版本和旧字段 |
| `data/review_rules.json`、`data/review_rule_index.json` | 场景经验与可校验的索引 |
| `exports/**/prediction_V*.json` | 赛前原始存档，和正式历史分开展示 |
| `workbench/runtime/drafts/` | 每次完成后独立保存的研究预测 |
| `workbench/runtime/models/`、`auto_models/` | 登记模型与逐次研究模型 |
| `workbench/runtime/reviews/` | 复盘报告 |

开源分发包含经过本机路径脱敏的预测存档副本，保留原预测文字和版本。第三方网页正文、原始下载数据、个人环境、日志和重复备份不随包发布；历史数据中的来源 URL 和原始哈希保留用于追溯。分发副本不等于原机器的字节级冻结证据，不应拿它通过正式冻结或原始哈希验收。详见 [数据与分发说明](DATA_NOTICE.md)。

后续新生成的本地运行数据默认忽略，不自动提交或上传。首次建模按来源重新获取历史基础；已登记研究模型与存档可直接浏览。历史样本仅代表实际取得的覆盖范围，不能视为全球赛事全量。

## 验证

```powershell
cd workbench/backend
.\.venv\Scripts\python.exe -m pytest
cd ../frontend
node --experimental-strip-types --test tests/*.test.ts
npm run build
```

源码包可用 `python scripts/build_open_source_release.py --output <全新目录>` 生成，并附清单与 ZIP；构建器只读取源文件。实际发布使用全新隔离目录，不直接上传整个本地工作目录。

预测属于研究输出，原始概率未经独立校准，测试通过不代表预测可靠或保证收益。项目不包含投注、支付或自动下注功能，与中国体育彩票及资料来源网站无隶属关系。

## 许可证

自有程序代码使用 [MIT](LICENSE) 许可证。第三方服务、数据、名称和依赖各自遵循其权利人条款，MIT 不授予这些内容的额外权利。详见 [DATA_NOTICE.md](DATA_NOTICE.md)。
