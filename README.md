# 抖音作者维权 · Codex Skill

为原创视频作者整理权属资料，检索抖音、快手和 B 站的疑似搬运，生成对照证据，并在用户授权范围内准备、提交和跟进版权投诉。

![抖音作者维权：三平台检索、证据对照、投诉记录](assets/promo.png)

## 能做什么

- 建立独立私密案件，整理原作链接、账号证明、内容管理截图和必要身份材料；已有资料与授权直接复用。
- 按作品名、别名和集数检索，固定首搜索页或首批结果，排除本人和已确认的白名单，逐条记录核验深度。
- 尝试获取单条公开视频，用本地抽帧、时间定位和镜像比较辅助复核；必要时使用官方播放页取样截图。
- 整理举报草稿、附件和回执；按作品 ID 去重，区分待核、补证、已提交、审核结果与下架结果。

| 平台 | 当前实际能力与边界 |
| --- | --- |
| 抖音 | 已实测官方侵权入口、举证和正式提交成功。下载受登录态及平台限制影响，可用已授权浏览器或合规本地文件补充取证。 |
| 哔哩哔哩 | 已实测单条视频获取、原作对照，以及“侵犯合法权益”中的个人知识产权投诉提交。 |
| 快手 | 支持检索、证据整理和保守的公开分享页提取后端；该后端尚未完成真实作品下载验证。网页提示作品侵权需到移动端提交，当前提供材料交接，未验证 App 最终提交。 |

实测状态截至 2026-10-14。操作时仍须核对当前页面。投诉须从**侵权入口进入，再选择其中适用的搬运/抄袭类别**；各平台路径不同，快手普通网页举报不能代替版权投诉。

## 安装

将本仓库放入 Codex 本机技能目录，文件结构应为 `douyin-rights-guard/SKILL.md`、`scripts/`、`references/`。

Windows PowerShell 示例：

```powershell
git clone https://github.com/mariozhaofan-pixel/douyin-rights-guard.git "$env:USERPROFILE\.codex\skills\douyin-rights-guard"
```

也可下载仓库 ZIP，解压后将目录命名为 `douyin-rights-guard`。默认位置是 `~/.codex/skills/`；配置了 `CODEX_HOME` 时，使用其下的 `skills/` 目录。安装后在 Codex 中调用 `$douyin-rights-guard`；未被识别时重新打开 Codex。

媒体工具需要 **Python 3.10+、FFmpeg/ffprobe、Pillow、yt-dlp**。在技能目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-media.txt
.\.venv\Scripts\python.exe scripts/media_probe.py doctor
```

macOS/Linux 将虚拟环境 Python 路径换为 `.venv/bin/python`。FFmpeg/ffprobe 需单独安装并加入 PATH，参见 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)。完整用法见[媒体工具说明](references/media-tools.md)。

## 开始使用

在 Codex 中输入：

> 使用 $douyin-rights-guard，为我的原创作品建立私密维权项目。先核对作品名称、原作链接、集数范围和白名单，再检索抖音、快手和 B 站，并按我的授权处理已核实的搬运。

流程需要联网、浏览器操作及本地文件读写，首次按技能说明为项目开启完全访问权限。**账号登录、验证码和必要签署由用户亲自完成**；权限开启不等于允许任意对外发送资料。

准备原作视频与链接、手机端本人主页截图、创作者中心内容管理截图，以及真实姓名、身份证正反面、邮箱和手机号作为备用资料。资料保存到**仓库外的私密案件目录**，仅在已确认的官方表单实际需要时使用。不要把证件、Cookie、验证码或案件截图提交到 GitHub；`.gitignore` 不能代替发布前检查。

浏览器操作依赖当前 Codex 环境提供的 computer-use/browser-use 能力；仓库中的 Python 脚本本身不会登录账号或提交投诉。读取账号授权与正式投诉授权分开记录，已明确覆盖的授权无需重复索取。

## 怎样理解结果

搜索覆盖数来自首批清单，案件候选和投诉状态来自案件记录，二者不能互相替代。尚待内容初核、已复核但证据不足、已确认但未提交、提交成功、审核成立和已下架分别统计；退出的平台保留历史但不继续处理。

人物、场景或题材相似不能单独证明搬运；自动匹配只辅助定位，未命中也不能证明不存在复制。截图只支持实际观察点，不代表完整观看或整段相同。投诉成功仅表示平台收件，**不保证侵权成立、审核时效或下架**。

遇到验证码、访问限制或重复连接失败会保存进度并停止同类重试；最终提交后结果不明时先查官方记录，避免重复投诉。

## 项目文件

- [SKILL.md](SKILL.md)：完整执行流程。
- [平台通道](references/platform-channels.md)：实际入口、表单要求与尚未验证的部分。
- [检索与复核](references/search-and-review.md)：首批覆盖、证据判断和人工复核。
- [本地档案](references/case-workflow.md)：案件目录、授权、证据及状态命令。
- [提交与回执](references/submission.md)：提交去重、补正和结果记录。

项目地址：[mariozhaofan-pixel/douyin-rights-guard](https://github.com/mariozhaofan-pixel/douyin-rights-guard)。反馈问题时请先移除个人信息、登录数据和真实案件附件。
