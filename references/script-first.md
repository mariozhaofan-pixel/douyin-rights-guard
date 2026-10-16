# 脚本优先操作

此文件面向执行代理。用户提供本地原片目录、作者后台/私密截图、白名单等资料，并亲自登录非作者检索举报账号；代理执行命令、整理结果，必要时在授权范围内访问作者公开主页/原作页补证。作者账号不登录、不请求登录授权。

按 **抖音 → B站/快手** 分阶段执行下面的命令链。抖音阶段先收齐用户截图、原作和权属材料，完成当轮目标的核验、应提交投诉与回执登记后，再用于站外同一原作的投诉准备。抖音受阻则保留具体待办继续解决，不能把未完成阶段写为完成；已提交待审核无需等待下架。原作权属附件可以复用，候选视频、来源页面及重合对照必须分别取证；既有提交与历史回执保留。

## 工具分工

| 工作 | 默认工具 | 仍需交互的情形 |
| --- | --- | --- |
| 原作与作者证明 | 先查授权本地目录，复用用户截图，case_manager 登记来源与哈希 | 授权后用非作者账号补公开页；后台/私密页仍用户提供 |
| B站公开检索线索 | search_public.py，调用 yt-dlp bilisearch | 索引失败或要核对实际首搜索页时，一次页面采集 |
| 抖音/快手搜索 | 已有导出/分享文本优先，必要时非作者会话一次采集 | 当前没有本包已验证稳定的匿名搜索脚本 |
| 搜索去重、首批分母 | workflow.py import-search / report | 无ID的短链需正常解析确认 |
| 下载与抽样对照 | media_batch.py + media_probe.py | 获取受限时补本地文件或正常平台保存；不能自动判侵权 |
| 原片目录清点、持续补证快照 | workflow.py followup | 目录需用户明确授权；集数/版本、公开链接及权属由代理核实 |
| 草稿、缺项、附件索引 | workflow.py plan / prepare | 代理实际查看证据，核实事实和授权 |
| 抖音/B站提交 | 已验证官方版权表单 | 必要的登录、上传、声明和最终按钮 |
| 快手提交 | 优先网页普通举报，general_report | 当前实际分类不适用或平台拒绝时才另寻专门途径 |
| 数量与反馈 | workflow.py report / case_manager.py report | 读取平台回执后登记；不会自动猜测审核结果 |

公开搜索 API/提取器不是官方举报 API。脚本没有发送投诉、邮件或自动读取浏览器 Cookie 的功能。

## 1. 案件与账号

```powershell
$SkillDir = Join-Path $env:USERPROFILE '.codex\skills\douyin-rights-guard'
$CaseRoot = 'D:\维权项目\作者-日期'
python "$SkillDir\scripts\case_manager.py" init --root "$CaseRoot"
```

已有案件省略 init。身份材料与作者截图沿用[本地档案](case-workflow.md)，作品必须已有真实 `work_id` 及用户确认。作者账号只登记标识；以下变量由已核实资料设置，不能直接使用占位值：

```powershell
python "$SkillDir\scripts\case_manager.py" account-set --root "$CaseRoot" --platform douyin --role author --account-id "$AuthorId"
python "$SkillDir\scripts\case_manager.py" account-set --root "$CaseRoot" --platform bilibili --role reporter --account-id "$ReporterId" --authorized --scope search --scope report --own-account
python "$SkillDir\scripts\case_manager.py" accounts --root "$CaseRoot"
```

`--authorized` 仅记录用户已经给出的对应授权，不会真正登录或征得同意。只有已知是用户自有账号才传 `--own-account`。作者标识不接受 `--authorized` 或 `--scope`。每个平台按实际账号分别登记，切换新 reporter 默认 pending；读取授权与对具体候选的提交授权仍分开。

用户提供的截图登记使用 `evidence-add --user-provided`；未知拍摄时间留空，脚本记录接收时间而不编造拍摄时间。代理从已授权公开页采集的截图按实际来源、时间登记，不标成用户提供，也不能冒充本人后台视角。不因缺作者登录就阻止已具备证明的案件。快手普通举报无需为本地检查额外索取身份证或作者后台截图；表单不要求的资料不外发。

## 2. 检索与一次导入

抖音阶段完成后，B站可先用公开检索脚本；本命令不登录：

```powershell
python "$SkillDir\scripts\search_public.py" --query "$WorkTitle" --limit 20 --out "$CaseRoot\03_search\bilibili-index.json"
python "$SkillDir\scripts\workflow.py" import-search --root "$CaseRoot" --file "$CaseRoot\03_search\bilibili-index.json" --platform bilibili --batch-id bili-index-01 --query "$WorkTitle" --sort public-index --account-ref anonymous --batch-kind links --original-id "$OriginalId"
```

只有搜索回执为 `indexed` 且有有效结果才导入。文件已存在不会覆盖，应复用它或为新一次查询用新文件名。`--limit` 为1–100、默认20，前N条索引不是网页第一页；标题/作者为空时保持未知，不把作品 aid 当作者UID。该功能基于 [yt-dlp BiliBiliSearchIE](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)，2026-10-16有一次通用词1条的只读查询成功，不能保证其他词或登录结果等价。

三平台都可导入 JSON 数组或 `{"results":[...]}`；CSV字段相同：

```json
{"results":[{"url":"https://www.douyin.com/video/1234567890","title":"示例作品名","uploader_id":"example_uploader"}]}
```

以上仅为合成格式示例，执行时使用实际页面作品。文本 `.txt` 可直接保存分享链接。短链不发网络请求解析，保留 `pending_resolution`。通过正常官方页面确认规范作品链接后，使用 `plan` 中对应的批次ID和条目 `key` 登记解析依据：

```powershell
python "$SkillDir\scripts\workflow.py" resolve-entry --root "$CaseRoot" --batch-id "$BatchId" --item-key "$ItemKey" --url "$ObservedCanonicalUrl" --source-note '记录实际打开的官方作品页及证据文件路径'
```

链接必须为同平台规范作品URL，不能猜测ID。解析结果单独保存在 `03_search/workflow-resolutions.json`，保留原始批次字节和固定分母；相同解析可复用，冲突目标须先核实原记录。它不会自动创建候选或证明搬运，之后仍按 `plan` 关联真实原作并复核。`report` 的 `unique_resolved_count` 与 `resolved_duplicates` 区分解析后的独立作品和重复条目。

已实际采集整页/首批的 JSON/CSV，才使用 `first` 并注明页面边界：

```powershell
python "$SkillDir\scripts\workflow.py" import-search --root "$CaseRoot" --file "$CaseRoot\03_search\kuaishou-first.json" --platform kuaishou --batch-id ks-first-01 --query "$WorkTitle" --sort 综合 --account-ref ks-reporter --batch-kind first --source-url "$ObservedSearchUrl" --boundary-note '本次实际稳定加载的首批作品，页面证据另存' --original-id "$OriginalId" --complaint-route general_report
```

`first`需要实际官方来源页与边界记录；纯分享文本只能 `links`。后续滚动用 `extension --parent-batch ks-first-01`，不修改原首批分母。`--original-id`只对已存在原作建候选；全系列结果不应一律绑第1集，不能确定对应集时先不传，复核后关联。输入中的“已复核/已授权/已提交”等字段不被采信。

官方搜索页正常加载并明确显示零结果时，可用 `{"results":[],"observed_empty":true}` 配合 `first`、实际来源页和边界记录，保留真实的零结果批次。普通空数组、空文本、公开索引失败、登录限制或未加载页面不能这样登记。该记录只描述本次页面，不表示全网没有搬运。

快手新候选默认工作流选择 `general_report`；抖音/B站使用 `copyright`。既有候选的旧路线、权属、回执不自动覆盖，避免重新统计或误重投。

## 3. 批量媒体准备与续跑

先把用户给的原视频、已保存候选视频登记为 `original_video`、`candidate_video`，分别绑定原作ID/候选ID。运行：

```powershell
python "$SkillDir\scripts\media_batch.py" --root "$CaseRoot" --mirror
```

默认只处理本地文件。确需公开下载时：

```powershell
python "$SkillDir\scripts\media_batch.py" --root "$CaseRoot" --download --mirror --limit 20
```

每次最多处理20个需要实际工作的候选；缓存复用不占额度，再运行会继续后面的对象。通过内容哈希及参数判断能否复用对照；证据变化会重新生成，原结果保留。输出包括 `04_candidates/media-batch/`、`05_comparisons/media-batch/`、`08_logs/media_batch_latest.json`。下载缓存按目标URL和文件哈希绑定。

缺对应集原片时返回 `original_media_missing_or_ambiguous` 与恢复动作；该项不消耗处理额度，不阻止后续快手本地准备。先清点授权目录、核对版本/集数和公开原作链接，将实际文件登记到对应 `work_id` 后重新运行，无需为此解除平台网络停止状态。多个有效版本须先明确选择，不能自动取首个；更换原作后旧人工复核须留历史并重核。

- 下载只用规范官方链接、默认匿名 yt-dlp。快手公开后端须显式 `--kuaishou-public`，失败后保守暂停该平台；尚未真实下载验证。
- 同平台两次连续 Cookie 要求会暂停，计数跨续跑保留；验证码、拒绝访问或登录限制立即暂停，不切换代理或身份重试。
- 修复原因后可显式 `--resume-platform douyin --retry-failed`。这两个参数不提供授权、不绕过限制，也不应在条件未变化时使用。
- 已有 `unknown/submitting/submitted` 候选跳过，先跟进回执；作者本人、当前操作账号、白名单和退出平台跳过。
- 新下载文件的来源验证仍为 pending，不自动登记成已核验取证。先核实作品来源，再 `evidence-add`；比较报告不自动改变人工复核、权属、白名单或提交授权。
- `--candidate <候选ID>`可重复，`--interval`和`--max-frames`控制抽样；片段核验仍需实际查看媒体。零匹配不排除搬运。

需要已有明确授权的平台专域Cookie文件时用 `media_probe.py download --cookies <本地路径>` 单条处理，或直接使用用户本地视频；批量脚本不自动取浏览器登录数据。

## 4. 材料准备与结果

每轮开始、收到新资料、实际复核后或平台要求补正时，保存续办快照。只在明确获准读取的原片目录上使用 `--original-dir`，可重复传入多个具体目录；省略时不扫描，也不沿用上次目录自动扩大访问：

```powershell
python "$SkillDir\scripts\workflow.py" followup --root "$CaseRoot" --original-dir "$AuthorizedOriginalDir"
```

清点仅枚举该目录树的视频，不跟随符号链接或目录联接，不扫描整个视频库。私密快照中的路径、文件名、大小、修改时间及SHA256仅用于找原片和核对文件，不能当作集数、版本、发布时间或权属已确认，更不会自动绑定作品。核对每集/版本后将原作链接和真实文件登记到相应 `work_id`；未知链接、待补权属保留为缺项。先查授权本地目录，再用已授权非作者账号查看作者公开作品，最后才集中说明仍缺什么。

输出每轮不可覆盖的 `08_logs/followup/run-<uuid>.json` 与当前状态 `08_logs/followup/latest.json`。快照保留候选缺项、下一补证动作、未登记搜索项、平台阶段和目录清单；CLI仅返回路径与计数。关联原作/候选/对照文件的实际内容哈希或原作绑定变化才提示重新比对与人工复核；无关集、文件mtime和跟进日志不会反复触发。重复运行保持待补/待重核状态，不自行结案；新人工复核应如实登记，不能为清除提示更改时间戳。已提交和结果未知的对象先查回执、按补正续办，不能重投。

登记新材料后重跑受影响候选，并实际复核，再刷新快照与材料包：

```powershell
python "$SkillDir\scripts\media_batch.py" --root "$CaseRoot" --candidate "$CandidateId" --mirror
python "$SkillDir\scripts\workflow.py" followup --root "$CaseRoot"
```

`followup`不改变 `case.json`、授权、复核结论或平台回执，不自动联网、提交或创建定时任务。快手本地测试和补证可继续；正式提交依然等待抖音阶段，不把资料不足作为放弃或跳过该阶段的理由。

```powershell
python "$SkillDir\scripts\workflow.py" plan --root "$CaseRoot"
python "$SkillDir\scripts\workflow.py" prepare --root "$CaseRoot"
python "$SkillDir\scripts\workflow.py" report --root "$CaseRoot"
```

`plan`输出待解析、待关联原作、缺项、可以进官方表单复核或应先查回执的清单。`prepare`按内容指纹输出 `06_drafts/workflow/<指纹>/`，每候选Markdown草稿、JSON附件引用、缺项以及按平台/路线/原作分组的目标清单。它不复制附件、不读入身份副本、不发送邮件、不提交举报；生成草稿也不代表门槛通过。已有相同包经完整性检查可复用。

`plan`与`prepare`按抖音、B站、快手排序，并输出 `execution_priority`。抖音待解析、待核、证据不足及结果未知分别保留原因；证据不足不改写为未处理或已完成。站外草稿仍可生成，`prerequisites_ready_for_official_form`表示材料检查状态，`submission_deferred_by_platform_order`表示提交顺序等待；等待项不进入 `ready_target_links_text`。抖音 `submitted` 仅剩审核跟进时不延后站外；历史已提交目标不重投。没有任何抖音范围记录会提示 `scope_not_recorded`，应补记真实检索状态；这不是新增用户审批。`case_manager gate`仍只检查材料与已有授权，顺序由工作流输出区分。

抖音同一原作的目标链接可分组填官方批量表单；平台识别丢弃或重复的链接单记，不按输入数计成功。B站同一UP主有多条已核实搬运、原作材料适用的稿件时，可使用“批量投诉更多”（实测最多50个）；它不支持跨UP主混批，其他情况逐条处理。逐项保留目标ID和回执关联，不把弹窗中未复核作品加入投诉。快手默认使用网页普通举报，优先简洁理由加原作链接，只选择真实适用分类，不为凑数量捏造违规类型；不强制手机或邮件。

`report`同时保留每个批次固定分母和候选状态；跨批作品可能重复，不直接相加成全网独立数量。`by_route`区分版权投诉与普通举报，审核成立和下架只取有证据的已核验状态。平台记录未查过就是未查过；不把普通举报收件当版权裁决。
