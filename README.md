# 抖音作者维权 · Codex Skill

用脚本批量整理搜索结果、核验视频重复、准备举报材料并统计处理结果，减少反复操作浏览器。支持抖音、快手和哔哩哔哩。

## 当前流程

1. 用户提供作者主页、作品页、创作者中心截图和原片，**无需授权或登录作者账号**。
2. 只授权登录有权使用的**非作者检索举报账号**。作者账号可能被搬运者拉黑，搜索不到不代表没有搬运。
3. 脚本导入、去重、下载、抽帧、生成对照和草稿；代理实际复核内容并复用已有授权。
4. 抖音和B站走版权侵权入口；**快手优先网页普通举报**，不强求手机或侵权专用通道。
5. 分别统计普通举报提交、版权投诉提交、审核结果与下架结果。

登录账号与著作权人分开记录；填写本人或代理身份依据真实关系，不能为了通过表单冒称原作者。账号切换不会继承旧账号授权，也不会丢失原作证明或历史回执。

## 哪些不用 Computer Use

| 工作 | 实现 |
| --- | --- |
| B站公开索引线索 | `search_public.py` 封装 yt-dlp bilisearch；默认20、最多100条，不登录 |
| 三平台清单导入、去重、首批统计 | `workflow.py import-search`，接受 JSON、CSV、分享文本 |
| 媒体批处理与续跑 | `media_batch.py`，优先本地视频，按需有界下载，内容哈希校验与缓存复用 |
| 抽帧、镜像对照、时间定位 | `media_probe.py`，本地处理 |
| 草稿、缺项与附件索引 | `workflow.py plan / prepare`，按平台、原作和举报路线分组 |
| 账号角色、证据哈希、提交前检查与状态统计 | `case_manager.py` 与 `workflow.py report` |

CU集中用于检索举报账号认证、必须交互的页面采集、官方表单和回执核对。作者侧截图全部由用户提供。Python脚本不自动提交举报、不发送邮件、不读取浏览器Cookie数据库。

## 平台现状

| 平台 | 检索/材料 | 提交方式与已测边界 |
| --- | --- | --- |
| 抖音 | 导入结果；媒体下载受登录态影响，可用本地原片/候选 | 已实测侵权专用入口与正式提交；同一原作可用官方批量表单 |
| 哔哩哔哩 | 公开索引脚本、单条下载、本地比对 | 已实测个人知识产权表单提交；索引前N条不代表网页第一页 |
| 快手 | 导入结果、本地比对；公开分享页下载后端尚无真实下载验证 | 当前默认网页普通举报；先前已打开普通表单，本版尚未真实提交该路线，不保证所有账号/分类可用 |

抖音/B站使用“侵权 → 其中适用的搬运/抄袭”。快手根据用户选择走 `general_report`，只选真实适用分类、填写核实事实，不把普通举报计作版权投诉受理。没有适用类别或平台拒绝时记录具体阻碍，再考虑专门途径；不强求手机接续。旧记录不自动改写路线或回执。

## 安装

把仓库放在 Codex 技能目录：

```powershell
git clone https://github.com/mariozhaofan-pixel/douyin-rights-guard.git "$env:USERPROFILE\.codex\skills\douyin-rights-guard"
```

也可下载ZIP并将目录命名为 `douyin-rights-guard`，确保根目录含 `SKILL.md`。配置了 `CODEX_HOME` 时用其下的 `skills/`。安装后调用 `$douyin-rights-guard`，未识别时重新打开 Codex。

需要Python 3.10+。媒体功能另需FFmpeg/ffprobe与以下依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-media.txt
.\.venv\Scripts\python.exe scripts/media_probe.py doctor
```

macOS/Linux使用 `.venv/bin/python`。FFmpeg从[官方来源](https://ffmpeg.org/download.html)安装并加入PATH。

## 开始使用

向Codex输入：

> 使用 $douyin-rights-guard。我会提供作者截图和原片，只登录非作者检索举报账号。请用脚本优先处理三平台首批作品；抖音、B站走版权投诉，快手优先网页普通举报，分别统计处理结果。

代理执行脚本，无需用户手动逐条运行。完整命令、导入格式及续跑规则见[脚本优先操作](references/script-first.md)。

案件保存在仓库外的私密目录。版权投诉备用资料包括真实姓名、身份证正反面、邮箱和手机号，只在实际官方表单需要且已授权时使用；快手普通举报不强制这些无关资料。不得把证件、Cookie、验证码、真实案件附件提交到GitHub。`.gitignore`不是加密，也不能代替发布前检查。

## 如何理解反馈

首批清单固定分母，新增滚动结果另列扩展批；分享链接/公开索引只作线索批。跨批可能重复，不能直接相加当全网独立数量。

抽样相似不等于侵权，零匹配也不证明无搬运。实际复核权属、白名单和具体表达后才准备举报。`prepare`、`gate`通过、身份审核都不算举报成功；只有真实平台回执才记录已提交。普通举报、版权投诉、审核成立和下架分别记账，不保证平台裁决、时效或下架。

验证码或拒绝访问停止该方式；连续Cookie要求和下载停止状态跨续跑保留。提交结果未知先查官方记录，不重投。退出平台保留历史、停止办理。

## 文件与验证

- [SKILL.md](SKILL.md)：代理执行流程。
- [脚本优先](references/script-first.md)：最短命令链与能力边界。
- [本地档案](references/case-workflow.md)：作者标识、举报授权、证据和分路线统计。
- [检索与复核](references/search-and-review.md)：覆盖范围、实际核验和证据深度。
- [平台通道](references/platform-channels.md) · [提交与回执](references/submission.md)。

```powershell
python -m unittest discover -s tests -v
```

2026-10-16新增账号分工、快手普通举报、搜索导入、批量媒体、材料包完整性与统计测试。合成/模拟测试不代表各平台真实投诉已通过。本版B站公开索引有一次通用词1条的只读实测成功；正式平台投诉历史见通道文档。

[项目地址](https://github.com/mariozhaofan-pixel/douyin-rights-guard)。反馈问题前请移除个人资料、登录数据和案件附件。
