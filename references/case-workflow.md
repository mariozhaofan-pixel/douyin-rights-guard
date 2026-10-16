# 本地案件档案与命令

`scripts/case_manager.py` 使用 Python 标准库管理本地案件、文件指纹和提交前检查。它不会登录账号、判断身份证真伪、作出侵权裁决或联网提交投诉。作者后台/私密页和本人视角证明由用户提供截图，无需作者账号登录；经授权可用非作者检索账号查看作者公开主页/原作页补证，按真实来源登记。作品复核及官方表单操作由主工作流完成；批量操作见[脚本优先](script-first.md)。

默认活动范围为**抖音、快手、哔哩哔哩**。小红书字段和旧记录仅保留历史兼容；当前不安排小红书检索、补件或提交。旧授权、待提交包或本地gate曾通过，都不能覆盖用户后来取消平台的指令。

## 建立独立案件

在 PowerShell 中设置 skill 安装目录与本次案件目录。例如：

```powershell
$SkillDir = Join-Path $env:USERPROFILE '.codex\skills\douyin-rights-guard'
$CaseRoot = 'D:\维权项目\作者-日期'
python "$SkillDir\scripts\case_manager.py" init --root "$CaseRoot"
```

`$SkillDir` 应指向实际安装位置，`$CaseRoot` 应替换为本次案件的新目录。初始化接受尚不存在的目录或空目录；拒绝覆盖非空目录，也拒绝将案件放在 skill、其所属 `skills` 目录或 Git 仓库内。不要通过搬移案件或改写路径绕过这项隔离。案件文件、身份证、登录资料和带真实身份的草稿都不进入 skill 包、代码提交或安装压缩包。

初始化产生：

| 位置 | 用途 |
| --- | --- |
| `00_private/` | 姓名、联系方式、证件、手机主页截图及需要身份资料的填表副本 |
| `01_auth/` | 授权记录和登录状态说明；不将密码、验证码或 Cookie 写入普通日志 |
| `02_originals/` | 原作页面、账号主页、内容管理截图及原视频 |
| `03_search/` | 查询词、搜索结果和覆盖范围 |
| `04_candidates/` | 候选作品页面与候选视频 |
| `05_comparisons/` | 逐段对照、关键帧和人工复核记录 |
| `06_drafts/` | 不含真实身份信息的可审阅投诉草稿 |
| `07_receipts/` | 投诉编号、平台提示、进度和回执 |
| `08_logs/evidence_manifest.json` | 非私密证据登记与 SHA-256 |
| `case.json` | 账号授权状态、原作清单、白名单、候选和提交状态 |

“私密目录”是资料分类和使用边界，**不是加密存储**。脚本不会加密文件或自动设置 Windows 访问控制。按用户的本机存储安排保存，不宣称目录名能够阻止其他本机用户、云同步或备份服务读取。独立目录也不代表用户已授权向外部发送其中的资料。

## 私密资料接收与检查

初始化的 `00_private/claimant.json` 有以下空字段：

```json
{
  "real_name": "",
  "email": "",
  "phone": "",
  "id_front": "",
  "id_back": "",
  "profile_mobile": ""
}
```

版权投诉备用档案的六项包含**手机号**；`intake` 仍按六项检查。快手明确选择 `general_report` 时，`gate` 不强制这些私密资料或三种作者侧截图，只使用实际举报所需材料。姓名、邮箱与手机号仅在该私密文件中记录；不要作为命令行参数、公开案件字段或普通日志输出。用户已经提供的资料直接复用，不重复索取。

证件与手机主页图建议保存为 `00_private/intake/id_front.jpg`、`00_private/intake/id_back.jpg`、`00_private/intake/profile_mobile.png`。`intake` 子目录按需创建。上述三个文件字段填写对应的**案件相对路径**，使用实际扩展名；不得填写绝对路径、`..` 路径或项目外链接。先保存实际附件，再登记路径，不用占位文件假装资料已到齐。

```powershell
python "$SkillDir\scripts\case_manager.py" intake --root "$CaseRoot"
python "$SkillDir\scripts\case_manager.py" status --root "$CaseRoot"
```

`intake` 只输出每项 `provided`／`missing` 及 `complete`，不回显字段内容。姓名、邮箱和手机号只检查是否有非空文本，三个文件字段只检查路径有效且文件存在。它不检查号码有效性、图片内容、身份证真伪或证件人与账号的关系。

`status` 另外报告三个抖音截图种类是否已有完整、哈希一致的登记，以及平台授权状态、作品数量、候选数量、白名单确认标记和全案证据完整性。`screenshots` 仅为全案种类摘要；`work_screenshots` 按 `work_id` 单独报告作品页和内容管理截图，不能凭全案已有某类截图判断每集均有证明。它不能证明已经登录或拥有作品权利；其中 `whitelist_confirmed` 也只是本地标记，完整门槛仍由 `gate` 检查。

## 原作、授权与用户确认字段

可以在案件内手动编辑 UTF-8 JSON，保持合法格式并保留既有字段。不要将真实姓名、身份证号、邮箱或手机号写入 `case.json`。账号角色可用 `account-set` 登记，其他权属与用户确认字段仍按实际事实编辑；脚本另提供初始化、证据登记、候选登记、平台退出和检查。

在 `case.json` 的 `works` 列表中，为每条实际原作建立记录。下例是结构示意，文字占位符必须替换；未经用户确认时保留 `false`：

```json
{
  "work_id": "work_1",
  "title": "<实际作品名称与集数>",
  "url": "<实际原作链接>",
  "user_confirmed": false,
  "ownership_confirmed": false
}
```

`work_id` 在本案内唯一且保持稳定。`user_confirmed` 表示用户明确确认本次维权的作品名称与链接；`ownership_confirmed` 表示用户已核对该条作品的权属。系列不同集数建立各自记录。已收到链接、完成下载或算法发现相似，均不自动等于这两项确认。

其他状态只依据真实操作和用户已给出的确认更新：

| 字段 | 通过检查所需的值与含义 |
| --- | --- |
| `full_access_acknowledged` | `true`：已向用户说明权限要求，并核对本次环境满足所需权限 |
| `authorizations.<platform>.status` | 旧格式登录授权，仅兼容既有检索举报记录；作者截图不需要此字段。新案件用 `account_roles.<platform>.reporter`。活动平台键为 `douyin`、`kuaishou`、`bilibili`，`xiaohongshu`仅保留历史兼容。该字段不替代活动范围检查 |
| `authorizations.<platform>.account_id` | 自己在该平台的稳定账号 ID，用于识别自己的账号；不填显示昵称 |
| `authorizations.<platform>.authorized_at` | 实际授权时间，建议带时区的 ISO 8601 |
| `rights_review.identity_status` | `user_verified`：用户已核对身份与权利人或代理关系 |
| `rights_review.ownership_status` | `user_verified`：用户已核对本案主张的权属 |
| `rights_review.reviewed_at` | 实际核对时间 |

初始的 `pending`、`false` 或空值不能因为“需要让 gate 通过”而改写为已确认。用户此前已经明确确认的事项可据实记录，不要求重复确认；没有证据的 `user` 确认不得由脚本、相似度结果或代理自行编造。

初始化保留四个平台的授权字典结构以兼容旧档案，但活动范围明确只有上述三个。旧案已有小红书 `authorized` 或候选授权ID时仍不自动将其纳入活动范围；没有该授权字段时仅显示 `pending`。

## 活动平台与退出记录

新案件有以下 `platform_scope`；旧案件完全缺少该字段时，按相同三个活动平台只读解释，不改写旧文件。字段存在但格式损坏时报告 `invalid_platform_scope`，不默默恢复默认值放行。

```json
{
  "platform_scope": {
    "active_platforms": ["douyin", "kuaishou", "bilibili"],
    "withdrawals": {}
  }
}
```

用户明确放弃某平台后，先保留案件备份，再记录退出。例如已明确放弃小红书时：

```powershell
python "$SkillDir\scripts\case_manager.py" platform-withdraw --root "$CaseRoot" --platform xiaohongshu
python "$SkillDir\scripts\case_manager.py" report --root "$CaseRoot"
```

命令从 `active_platforms` 移除该平台，在 `withdrawals.<platform>` 保存实际登记时的 `withdrawn_at` 和 `reason_code: "user_cancelled_platform"`，并将已有对应候选的 `activity_status` 记为 `excluded_platform_withdrawn`。同一退出命令可重复执行，保留首次退出时间。只能依据用户确实作出的取消指令执行，不能把上传失败自行等同用户退出。

退出不会修改 `submission_status`、`submitted_at`、`receipt_id`、已核验结果或原有授权历史，也不会删除草稿、附件及原始证据。未提交仍为 `not_submitted`；`unknown`、`submitting` 等不确定状态同样原样保留，不清零以便重投。更新续办账本并在旧上传包说明顶部明确已停用，停止请求该平台补件或上传。

`status` 和 `report` 返回派生的 `platform_scope.active_platforms`、`withdrawn_platforms`、`inactive_platforms`。`gate` 对已退出目标报 `platform_withdrawn_by_user`，对其他非活动目标报 `platform_not_active`，直接停止，不再为该目标读取身份材料或媒体证据；旧登录授权、旧提交授权或误把平台重新加进活动列表均不能覆盖退出记录。脚本没有自动恢复平台的命令；恢复须有用户新的明确指令并重新核对范围，不能删除退出历史来绕过当前取消。

## 白名单与账号 ID

`whitelist` 的初始结构为：

```json
{
  "user_confirmed": false,
  "confirmed_at": null,
  "entries": []
}
```

用户明确说“无白名单”后，才可把 `user_confirmed` 记为 `true` 并填入实际 `confirmed_at`，同时保留空 `entries`。未询问、未回复和空数组本身都不代表已确认无白名单。

有白名单时，每项至少包含 `platform` 和 `uploader_id`。从实际账号主页核实稳定 ID；只有昵称、头像或模糊主页线索时继续核对。可以额外保存主页链接和用户陈述的授权作品范围，但当前 CLI **只按平台加账号 ID 精确匹配**，不解析作品范围、授权期限或 `scope`。

当前实现对匹配的白名单账号作保守的整体排除：即使补充信息描述“只授权部分作品”，命中后也会阻止本候选进入提交复核。部分授权、过期授权或超范围使用需要另外核查并回到用户确认，不能为了绕过检查而删除已知白名单或更改账号 ID。

候选自己的 `uploader_id_verified` 也必须是真实核对后才设置为 `true`；仅填写一个看起来像 ID 的字符串不算已核对。脚本不联网验证 ID。

## 证据登记与完整性

先将实际截图或媒体保存到案件中，再登记。文件路径必须为案件内相对路径。身份材料留在 `00_private/`，不得通过 `evidence-add` 放进普通证据清单。

下面假定 `$OriginalUrl` 已设置为实际原作链接，`work_1` 已存在，文件已经保存：

```powershell
$CapturedAt = (Get-Date).ToString('o')
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '02_originals/original-page.png' --kind douyin_work --source-url "$OriginalUrl" --work-id 'work_1' --captured-at "$CapturedAt"
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '02_originals/original.mp4' --kind original_video --source-url "$OriginalUrl" --work-id 'work_1'
python "$SkillDir\scripts\case_manager.py" verify --root "$CaseRoot"
```

`--captured-at` 应使用**实际采集时间**，必须为带时区的 ISO 8601。上述命令适用于刚由代理采集的材料：默认 `provenance: "agent_captured"`，省略 `--captured-at` 时使用当前时间。登记旧材料应填写已知的实际采集时间，不用当前时间替代。

创作者中心**内容管理截图默认请用户提供**，不以登录后自动进入后台截图作为默认步骤。请用户截到本次作品名称、作品项与发布时间等必要上下文；需要补图时只说清缺少什么。收到后核对截图对应的作品，不把它写成代理亲自登录采集的证据。其他用户提供的截图和媒体也遵循同样的来源记录方式。

用户提供的文件必须加 `--user-provided`。不知道拍摄时间时不要传 `--captured-at`：脚本保留 `captured_at: null`，写入 `received_at` 为此次接收登记时间，并标记 `provenance: "user_provided"`；接收时间不冒充拍摄时间。只有用户给出或可靠证据支持拍摄时间时才显式填写。例如，用户提供内容管理截图且拍摄时间未知：

```powershell
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '02_originals/user-content-manager-work-1.png' --kind douyin_creator_center --source-url 'https://creator.douyin.com/' --work-id 'work_1' --user-provided
```

支持的 `--kind`：

| 类型 | 应登记的内容 |
| --- | --- |
| `douyin_profile` | 作者抖音主页截图，注明用户本人视角或经授权采集的公开访客视角；后者不代替平台要求的本人证明 |
| `douyin_work` | 本次原作页面截图；必须用 `--work-id` 关联具体原作 |
| `douyin_creator_center` | 创作者中心内容管理中对应作品的截图；必须用 `--work-id` 关联具体原作 |
| `original_video` | 原作视频；必须用 `--work-id` 关联原作 |
| `candidate_video` | 候选视频；必须用 `--candidate-id` 关联候选 |
| `candidate_page` | 浏览器视觉模式的官方候选页截图，显示作品及作者上下文；同时绑定 `--candidate-id` 和当前 `--work-id` |
| `candidate_frame` | 浏览器视觉模式的实际候选播放画面截图；同时绑定 `--candidate-id` 和当前 `--work-id` |
| `comparison` | 对照图或逐段报告 |
| `search` | 搜索记录或搜索页截图 |
| `receipt` | 不含不必要身份资料的回执证据 |
| `other` | 其他非私密证据 |

手机端本人主页截图由私密 `profile_mobile` 字段检查；它不自动替代上表三个网页/后台截图登记。后台首页也不能登记成内容管理页来凑齐类型。

`douyin_profile` 可以全案共享。`douyin_work` 和 `douyin_creator_center` 必须与本次候选所对应的同一个 `work_id` 绑定；第 1 集截图不能让第 2 集通过检查。当前每条证据仅支持单一 `work_id`，没有 `work_ids` 数组或多次 `--work-id` 的语义。如果同一张真实内容管理截图清楚包含多集，可将原始文件逐字节复制为不同文件名，分别绑定对应作品；两份应保留相同哈希，不伪造截图内容。旧记录未绑定作品时不会作为具体作品的提交证明，须核实内容后按上述方式补登记。

每次登记保存相对路径、SHA-256、来源 URL、已知的采集时间、登记时间、来源类型以及关联 ID；用户提供材料另记接收登记时间。来源 URL 仅接受 HTTP(S)，并删除查询参数和片段以避免把临时令牌带入清单。应提供可独立定位内容的公开链接；若作品 ID 只存在于查询参数中，先取得平台的规范作品链接，另在非私密取证说明中保留必要定位信息。用户后台截图可使用实际官方后台入口作来源，并在取证说明中记录截图内的作品定位信息；不得宣称代理亲自访问过。不要提供带登录凭据的 URL。

相同路径、哈希、类型及作品/候选绑定相同时返回 `already_registered`；同一已登记文件发生变化时拒绝覆盖原登记。同路径改绑另一作品、候选或类型也会明确拒绝，不能靠重复命令静默更改。需要复用同图时按前述办法复制新文件名再登记；标注、裁剪或转码也另存新文件再登记，保留原件。清单以完整 JSON 原子替换写入，避免半条记录；不要让多个进程同时修改同一案件。

`verify` 返回各证据 `verified`、`changed`、`missing` 或无效路径状态。它只检查已登记文件与已存哈希是否一致；空清单也可能返回 `ok: true`，因此不能替代材料齐备检查。SHA-256 不证明创作时间、原创性或公证效力。

## 候选登记与逐段复核

先核实平台视频 ID、发布账号 ID、标题和对应原作，再执行。以下变量须来自实际候选页：

```powershell
$Registration = python "$SkillDir\scripts\case_manager.py" candidate-add --root "$CaseRoot" --platform douyin --video-id "$CandidateVideoId" --uploader-id "$UploaderId" --title "$CandidateTitle" --original-id 'work_1' | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '候选登记失败，请查看错误并保留现有案件。' }
$CandidateId = $Registration.candidate_id
```

`--platform` 接受 `douyin`、`kuaishou`、`bilibili`，并保留 `xiaohongshu` 旧档案兼容。保留解析能力不代表该平台仍在活动范围，不能据此继续已取消平台的任务。`--uploader-id` 可在发现阶段省略，但提交前必须补全并核实。脚本以平台和作品 ID 去重，生成 `candidate_id` 与规范 URL；相同作品再次登记返回原 ID，不新增记录。去重不合并新的原作关联或复核内容，跨集搬运需据实完善该候选的分析与本次主张范围。

小红书仅保留历史schema兼容：`video_id` 存24位十六进制笔记ID，大小写统一后去重；规范URL为 `https://www.xiaohongshu.com/explore/<note_id>`，不含 `xsec_token`、`xsec_source` 等访问参数。规范链接打不开不能推断笔记已删除。媒体下载已停用，`media_probe.py` 拒绝新的小红书下载；这些历史结构不构成继续访问、补件或投诉的操作指令，分享旧材料时仍须排除访问token。

候选视频保存后登记：

```powershell
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '04_candidates/candidate.mp4' --kind candidate_video --source-url "$CandidateUrl" --candidate-id "$CandidateId"
```

在对应 `case.json.candidates[]` 记录中，填写具体连续区段。时间单位为秒，起点不得为负，终点必须大于起点。以下仅展示结构，不表示已复核：

```json
{
  "original_start": 0,
  "original_end": 4,
  "candidate_start": 1,
  "candidate_end": 5,
  "description": "<指出这段实际重复的动作、物件、构图或连续镜头，并引用对照文件>",
  "human_verified": false
}
```

将真实区段加入 `overlap_segments`，人工确认后才记 `human_verified: true`。不能只写“同角色”“同场景”或一个算法分数。`gate` 只检查结构、时间范围和确认标记，不检查描述是否真实或区段是否存在于视频中。

### 候选无法下载时的浏览器视觉模式

新候选默认 `evidence_mode: "local_video"`，旧候选缺少该字段也按此处理，仍要求原作与候选两条视频；未知模式阻断。候选媒体无法合法保存、但能在已授权官方页面正常播放时，可明确设置 `evidence_mode: "browser_visual"`。此模式仅替代候选本地视频要求，**原作本地视频、逐集账号/作品证明、白名单、真实视觉复核、投诉入口和已有提交授权全部保留**。它不下载blob、不证明平台一定接受截图，也不自动放行只有相似标题、人物或封面的对象。

本模式使用 `observation_method: "ordered_timestamped_screenshots"`，准确主张带媒体时间的已核实取样点。至少两组取样、每组至少两个不同候选帧，合计至少四帧且覆盖至少两个不同镜头；各帧记录对应原片秒数并制作对照图。同组可包含不同镜头，应逐点说明，不因此主张中间转场已观察。`overlap_segments` 的起止时间在此模式表示取样点的定位范围，**不表示已观察范围内全部中间帧**；报告和投诉理由只写已核实点，不把播放经过一段时间写成完整连续观看。两组候选定位范围不能重叠，原作镜头允许被重排。

`continuous_coverage_claimed` 必须明确为 `false`，`continuous_playback_reviewed` 可如实为 `false`。仅将后者改成 `true` 不能让截图模式主张连续覆盖；需要连续片段主张时须另补真实连续证据和相应复核，不能靠布尔标记扩大证据范围。

抖音一次实测可在目标播放器有焦点时用ArrowLeft/ArrowRight约5秒跳转并暂停截图；每次仍核对播放器显示的实际时间与目标画面。媒体时间必须来自实际播放器/可核实的播放定位，不能把截图采集时间、帧序号或未经验证的按键累计数当成媒体秒数；计时不可核实时保留待核，不能编造。

先保存并登记带作品/作者上下文的官方候选页，再分别登记四张候选帧和含四组原片对应画面的对照图。`$CandidateUrl` 是该候选规范官方地址，`$WorkId` 是其已登记原作ID：

```powershell
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '04_candidates/page.png' --kind candidate_page --source-url "$CandidateUrl" --candidate-id "$CandidateId" --work-id "$WorkId"
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '04_candidates/shot-1-point-1.png' --kind candidate_frame --source-url "$CandidateUrl" --candidate-id "$CandidateId" --work-id "$WorkId"
python "$SkillDir\scripts\case_manager.py" evidence-add --root "$CaseRoot" --file '05_comparisons/four-point-comparison.png' --kind comparison --source-url "$CandidateUrl" --candidate-id "$CandidateId" --work-id "$WorkId"
```

其余候选帧逐张登记并保留返回的 `evidence_id`，不能把文件路径填在证据ID字段。页面和帧登记只接受PNG/JPG/JPEG/WebP扩展名与对应文件头，必须绑定同一候选及其原作，来源URL去除查询参数后必须等于候选规范地址；此模式的对照证据也须具有同样绑定、来源和图片格式。文件头检查只排除明显占位文件，不验证画面真伪。原始截图保持不变，对照拼图另存；不复制或裁剪同一原图制造额外独立帧，页面图也不能替代对照图。

若四张原始候选帧中的某张完整截图同时显示作品及作者上下文，可只登记一次 `candidate_frame`，将其同一证据ID也填入 `page_evidence_id`，并在实际核验上下文后设置 `page_context_verified: true`。页面角色不额外计入四帧，也不要求第五个不同瞬间；四个取样帧之间的时间、证据ID及哈希仍须各不相同。只有视频区域的裁剪图不能冒充页面上下文，裁剪派生图也不新增观察点。

在候选记录中增加以下结构；时间和证据ID均为示例，所有事实确认值必须在实际观察后填写：

```json
{
  "evidence_mode": "browser_visual",
  "browser_visual_review": {
    "candidate_url": "<该候选canonical_url>",
    "uploader_id": "<已核验的候选作者ID>",
    "page_evidence_id": "ev_page",
    "comparison_evidence_id": "ev_comparison",
    "download_unavailable_reason": "browser_blob_not_downloadable",
    "time_basis": "<实际播放器媒体时间的读取和核对方法>",
    "observation_method": "ordered_timestamped_screenshots",
    "same_source_expression_confirmed": false,
    "coverage_limitations": "仅核验所列截图点，不主张未观察中间帧或完整片段连续覆盖。",
    "continuous_coverage_claimed": false,
    "continuous_playback_reviewed": false
  },
  "overlap_segments": [
    {
      "shot_id": "shot_1",
      "original_start": 10, "original_end": 14,
      "candidate_start": 20, "candidate_end": 24,
      "description": "<第一镜头具体动作、物件位置、构图及表演的重合细节>",
      "human_verified": false,
      "screenshot_observation": "<两个实际取样点各观察到了什么，具体对应原片何处>",
      "candidate_frames": [
        {"time": 20, "original_time": 10, "evidence_id": "ev_frame_1"},
        {"time": 24, "original_time": 14, "evidence_id": "ev_frame_2"}
      ]
    },
    {
      "shot_id": "shot_2",
      "original_start": 30, "original_end": 34,
      "candidate_start": 40, "candidate_end": 44,
      "description": "<第二个不同镜头的具体同源表达细节>",
      "human_verified": false,
      "screenshot_observation": "<第二镜头两个取样点的实际观察记录>",
      "candidate_frames": [
        {"time": 40, "original_time": 30, "evidence_id": "ev_frame_3"},
        {"time": 44, "original_time": 34, "evidence_id": "ev_frame_4"}
      ]
    }
  ]
}
```

`download_unavailable_reason` 只写简短原因码或脱敏说明，不粘贴临时媒体地址、Cookie或原始服务错误。`candidate_url/uploader_id` 须与本候选已核验规范URL/作者ID一致。只有核验具体镜头、物件布局、动作和构图表达为同源后才将 `same_source_expression_confirmed` 记为 `true`；不能把同人物、同题材或同标题当作确认。已有 `uploader_id_verified`、下述 `manual_review` 与提交授权仍独立必需，不新增虚假的用户亲自复核记录。

`gate` 解析证据ID并检查唯一记录、哈希、类型、候选/原作绑定、规范来源及图片文件头。至少两组不同 `shot_id` 且候选定位区间不重叠，每组描述与截图观察非空；每帧媒体时间和原片对应时间均落在各自定位区间内。候选时间点、证据ID与文件哈希不得重复，同组原片时间也不重复。缺图、跨候选引用、文件变更、未确认具体同源表达或缺少人工复核都不能通过；保守门槛不足时继续待核，不能伪造第二镜头或视频文件凑齐。结构检查无法验证截图内容或说明的真实性，必须由实际查看证据的人负责。

具体连续片段或本模式的取样点完成视觉复核后，才将该候选的 `manual_review` 记为 `status: "confirmed_copy"` 和实际 `reviewed_at`；用户亲自复核记 `reviewer: "user"`，用户委托代理实际查看原作、候选及本次主张对应的片段/截图点证据时记 `reviewer: "agent"`。不要把代理复核冒记为用户亲自看过，也不要把相似度脚本输出直接当复核。当前 `gate` 接受这两种如实记录的复核者，但仍独立检查用户提交授权、原作证据与投诉入口。疑似重制或证据不足保留 `pending` 并汇总疑点，不套用已确认搬运标记。

每个候选另有投诉入口核验 `complaint_channel_review`，新建时为：

```json
{
  "status": "pending",
  "observed_path": [],
  "infringement_entry_verified": false
}
```

对copyright路线，人工在实际UI先确认并选择侵权入口，再选择其中的搬运分支，记录连续的实际入口关系后，才设置 `status: "verified"` 和 `infringement_entry_verified: true`。`observed_path` 必须是按操作顺序排列的非空字符串数组，每项记录实际观察到的页面或选项，不能只是一个笼统的“已确认”字符串。脚本不硬编码按钮文字，也不联网核验该陈述；应在案件记录中保留相应页面证据。直接进入搬运、仅见“盗搬稿件举报”标题或相似表单、仅有深链，均不满足这项核验。此前观察到B站自然人／原创本人表单不自动证明路线正确，不能据此填已验证。旧候选缺少此字段时同样阻断提交，不自动迁移为已验证。

## 提交前检查与回执

用户明确授权本批具体目标及敏感材料的官方去向后，更新 `submission_authorization`：

```json
{
  "user_confirmed": false,
  "confirmed_at": null,
  "candidate_ids": []
}
```

仅将实际获授权的候选 ID 填入列表，再按真实授权更新确认标记和时间。用户已明确授权对指定作品、当前活动平台首批范围内确认搬运的对象批量投诉时，可在实际核验后据该范围整理候选ID列表，并保存原话来源或授权引用；无需为列表内每条再索要确认。后来取消平台时，旧授权引用保留为历史，当前退出范围优先。收到资料、登录授权或泛泛排查请求本身不构成该批授权。超出范围、权属/白名单不明或新增敏感资料去向等重大缺项统一说明，继续不受影响项。

长期授权范围记录保存在私密案件中，具体字段与复用规则见[提交与回执](submission.md)。`submission_authorization.candidate_ids`仍只列实际确认属于该范围的候选，不能用空列表或通配符替代。长期授权不自动满足当前工具另行要求的声明动作确认；此类确认集中成具体批次处理。

```powershell
python "$SkillDir\scripts\case_manager.py" gate --root "$CaseRoot" --candidate "$CandidateId"
```

`gate` 检查：

- 版权路线检查六项私密资料、账号主页截图及绑定原作的作品页和内容管理截图；快手明确使用 `general_report` 时不强制这些私密材料与作者截图。
- 本条提交相关证据哈希一致：全案 `douyin_profile`、绑定当前候选的材料，以及未绑定候选但绑定当前原作的材料。带有其他候选 ID 的材料不因也标注了同一原作 ID 就阻断本候选。
- 目标在活动平台范围且未被用户退出；环境权限已说明，目标平台检索举报账号具有对应授权。作者标识无需登录授权；新案件使用 `account_roles`，旧案仅兼容既有目标平台授权。
- 权属为 `user_verified`，版权路线另检查身份状态；原作名称、链接与本条原作权属已由用户确认。
- 具有与原作 ID 关联的 `original_video`；`local_video` 模式另须绑定候选的 `candidate_video`，明确选择 `browser_visual` 时改查上述官方页面、两镜头四点截图及对照图。
- 白名单已明确确认，条目有平台与账号 ID；候选 ID 已核对，不属于白名单或对应平台的本人账号。
- `local_video` 至少一个具体连续重复区段已通过实际视觉复核；`browser_visual` 须按上文核验两镜头四点并明确取样局限。`manual_review` 的复核者为真实的 `user` 或受托查看证据的 `agent`，本条候选仍须在已记录用户提交授权内；算法输出本身不满足复核。
- 候选的 `complaint_channel_review.status` 为 `verified`，且 `observed_path` 为非空的有效字符串数组；版权路线另须 `infringement_entry_verified=true`，快手 `general_report` 不要求侵权入口标记。实际通道核验不能由目标授权或相似度判断代替。
- 规范 URL 与平台和视频 ID 一致，候选的 `submission_status` 仍为 `not_submitted`。

通过返回 `ready_for_submission_review`，同时明确 `network_submission_performed: false`；有缺项返回 `blocked` 和原因代码。当前提交不引用的其他作品、其他候选或未关联搜索资料发生丢失/变更时，在 `warnings` 中给出 `unrelated_evidence_integrity_failed`、证据 ID 和状态，不阻断当前候选。`verify` 与 `status.evidence_integrity` 仍报告全案完整性，因此它们可能提示异常而当前 `gate` 仅有警告。处理警告时保留原证据登记，不把它当成可以删除问题记录的理由。通过只是供实际提交复核，不能替代官方表单检查或真实的身份、权属和证据判断。

完成检查后，按照 [提交与回执](submission.md) 执行实际官方投诉。点击提交前把该候选状态记为 `submitting`；提交结果不明记 `unknown`；只有平台成功提示或投诉编号支持时记 `submitted` 并保存 `submitted_at`、`receipt_id` 与回执文件。脚本对任何非 `not_submitted` 状态均阻止再次进入提交，包含 `submitting`、`unknown` 和 `submitted`。发生结果不明时先核查平台记录；不要为重投擅自改回初始状态。

CLI 退出码：正常完成为 `0`，`intake` 缺项、`verify` 不一致或 `gate` 被阻止为 `2`，本地读取/写入或内容错误为 `1`。命令参数错误也可能由参数解析器返回 `2`。`status` 是摘要命令，返回 `0` 不代表全部条件满足，应读取其字段；`gate` 结果也不表示已受理或侵权成立。

## 按平台统计提交与处理结果

`report` 和 `summary` 是同一只读统计命令，只读公共 `case.json`，不读取私密身份资料、不联网、不修改状态，也不重新校验证据文件：

```powershell
python "$SkillDir\scripts\case_manager.py" report --root "$CaseRoot"
python "$SkillDir\scripts\case_manager.py" summary --root "$CaseRoot"
```

输出 `by_platform` 和 `totals`：`candidates_total` 为已登记候选数，`submitted` **仅计算** `submission_status: "submitted"`，并分别统计 `needs_supplement`、`failed`、`unknown`、`not_submitted`、`submitting`。未识别状态计入 `other_status`；未识别平台归入 `other`，避免静默漏数。“已提交”不等于“投诉成立”或“已下架”。

以上全量计数始终保留所有平台，包括已退出平台。另有三个数用于区分当前任务与历史：`active_todo` 为活动平台中所有非 `submitted` 状态的记录，包含补正和结果不明等跟进；`excluded_from_active_todo` 为其他平台的非 `submitted` 状态记录；`historical_submitted` 统计所有平台曾有明确本地提交记录的候选数。其依据为当前状态 `submitted`，或同时有有效带时区的 `submitted_at` 与非空字符串 `receipt_id`／`receipt_path`。因此成功提交后转 `needs_supplement` 不会丢失累计数；只有时间或只有回执引用不推断已提交。回执引用只按元数据检查，不表示文件真实性已复核；每个候选最多计一次，不统计重试次数。待办中的补正、失败、结果不明和提交中只是需核查，**不是重新提交授权**；已提交的审核跟进仍看独立结果记录。`activity_status` 是退出时的说明标记，统计和gate都以 `platform_scope` 为准，不靠候选标记恢复活动。

新候选有两个独立结果字段，默认均未核验：

```json
{
  "review_result": {"status": "not_checked", "verified": false, "evidence_ref": null},
  "removal_result": {"status": "not_checked", "verified": false, "evidence_ref": null}
}
```

只有独立查到明确审核通过结果，才记录 `review_result.status: "approved"`；只有明确核实内容已经移除，才记录 `removal_result.status: "removed"`。对应项还须 `verified: true`、`evidence_ref` 为非空的实际证据引用，统计才分别计入 `review_approved` 和 `taken_down`。上传成功、提交成功、审核中、页面暂时打不开或24小时提示均不能填写这两个正向结果。引用不在统计输出中显示；该命令按已记录元数据计数，不代替证据真伪或当前状态复核。

每个平台有页码的第一页、无页码的固定首批已加载作品是否已全部处理，须另外在搜索记录中保留边界、实际结果条数、逐条处置和遗漏原因；候选统计不包含未登记的搜索结果，不能用 `report` 的数量冒充首批覆盖量或全部处理完成。后续新加载项另记扩展批，具体口径见[检索与人工复核](search-and-review.md)。

## 账号标识与举报登录（2026-10-16）

作者后台/私密页材料由用户截图提供，已授权公开原作可用reporter补证；`author`仅保存稳定账号ID，供关联作品和排除自有账号，不需要授权登录。仅`reporter`登录，记录search/report用途。已知自有账号、历史作者和旧自有记录均参与排除；切换reporter不会覆盖作者证据、真实权利人或历史回执。

```powershell
python "$SkillDir\scripts\case_manager.py" account-set --root "$CaseRoot" --platform douyin --role author --account-id "$AuthorId"
python "$SkillDir\scripts\case_manager.py" account-set --root "$CaseRoot" --platform kuaishou --role reporter --account-id "$ReporterId" --authorized --scope search --scope report --own-account
python "$SkillDir\scripts\case_manager.py" accounts --root "$CaseRoot"
```

`--own-account`只用于用户明确自有的账号；非作者账号不自动等于第三人或代理。`--authorized`仅记录已有授权，不执行登录或授予权限。作者角色不能传授权参数；reporter换号默认pending，不能继承前一个账号授权。旧案没有role字段时按旧目标平台授权兼容并提示角色未分开；不再因缺抖音作者登录授权而阻断其他平台案件。

`account_roles.<platform>`保存author、reporter、known_own_account_ids和history。reporter记录authorized_scopes、authorized_at和account_id；author为identified、无登录scopes。实际平台若要求原作者登录，`complaint_channel_review.account_requirement=author_account`记录为需用户接续的限制，不启动作者登录。实名要求使用verified_claimant_identity，并由reporter_identity_check的account_id/status/reviewed_at绑定实际举报账号，切换后重核。省略account_requirement只代表未记录额外账号门槛，不等于平台保证所有账号能投。

## 普通举报与版权投诉

`candidate-add --complaint-route copyright|general_report`；省略为copyright，general_report仅快手可用。工作流对新快手任务显式选general_report，旧记录不批量改写。未提交旧候选需要切换时先确认其status为not_submitted并据用户选择记录新route，保留变更依据；submitted/unknown/submitting先查回执，不能用换route重投。

快手general_report的gate不强制私密intake、身份验证标记和三类作者截图，避免为了普通举报收集无关资料；仍要求真实原作及权属陈述、已核实对应画面、白名单、目标授权、reporter report授权，以及实际普通举报路径status=verified/observed_path。它不要求infringement_entry_verified。抖音/B站和copyright路线保留侵权入口检查。

report.by_route分别给copyright/general_report/unknown计数，原totals仍是所有已登记举报的合计。审核和下架依独立证据状态；general_report的审核结果不能称作版权裁决。生成草稿、准备附件、gate通过都不会写submitted。
