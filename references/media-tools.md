# 视频获取与本地对照工具

核查日期：2026-10-14。`scripts/media_probe.py` 只负责获取单条媒体和生成本地复核资料，不登录账号、不搜索全平台、不判断法律侵权、不提交投诉。下载成功后仍要人工核对标题、作者、画面和来源页面，避免把推荐视频当成目标。

## 安装与检查

要求 Python 3.10+、FFmpeg/ffprobe、Pillow 和 yt-dlp。先查找现有 Python，不能假定 `python` 已加入 PATH；在 Codex 桌面可定位已有运行时。建议在维权项目的工具目录建立独立虚拟环境，然后运行：

```powershell
python -m pip install -r requirements-media.txt
python scripts/media_probe.py doctor
```

FFmpeg/ffprobe 必须可从 PATH 调用。安装包来源用 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)；此 skill 不夹带二进制文件。Pillow 仅在本地处理画面，未使用人脸识别、云端模型或外部分析服务。

## 获取单条作品

先记录用户确认的原作品/候选作品链接及取证时间。每条作品独用一个空目录：

```powershell
python scripts/media_probe.py download --url "https://www.douyin.com/video/实际作品ID" --out "D:\维权项目\media\original-001"
python scripts/media_probe.py download --url "https://www.bilibili.com/video/实际BV号" --out "D:\维权项目\media\candidate-001"
```

上述 ID 是占位符，执行前必须替换。接受抖音、快手、B站的 HTTPS 单条作品页/已支持分享短链。小红书已移出当前范围，新下载请求会在读取 Cookie、创建目录和联网前拒绝；既有媒体、回执和离线对照资料保留。主页、搜索页、合集、直播不支持。yt-dlp 关闭用户配置、第三方插件、浏览器 Cookie 自动导入及磁盘缓存，先在内存中检查结果类型，再限制为单条下载；不会写完整 `info.json`、截图、字幕或服务端原始日志。

需要登录态且用户已授权该平台时，可显式传入**仅包含该平台域的本地 Netscape Cookie 文件路径**：

```powershell
python scripts/media_probe.py download --url "https://www.douyin.com/video/实际作品ID" --out "D:\维权项目\media\original-002" --cookies "D:\维权项目\private\douyin-cookies.txt"
```

不要让用户把 Cookie 文本粘贴进聊天，不读取浏览器全站 Cookie。脚本检查域名，复制到临时目录供 yt-dlp 使用，结束时清理临时副本，不修改源文件；源文件仍由用户按项目私密资料策略保管和清理。回执只记录是否使用 Cookie，不记录其路径或内容。官方 [yt-dlp Cookie 文档](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) 说明了 Netscape 文件格式，也说明浏览器批量导出可能包含所有站点数据；本封装不启用该导出方式。

快手提供一个保守的公开分享页后端，必须**显式选择**：

```powershell
python scripts/media_probe.py download --backend kuaishou-public --url "https://www.kuaishou.com/short-video/实际作品ID" --out "D:\维权项目\media\candidate-ks-001"
```

该后端不接受 Cookie，不运行页面脚本，不调用签名生成接口。读取公开 HTML 的 video/og:video 元数据；没有元数据时仅接受页面中唯一的 MP4 候选。多个候选、非允许平台/CDN 域、访问失败或风控即停止。大小上限为页面 4 MiB、视频 1 GiB。不会打印或保存带签名的媒体直链。不要因 yt-dlp 遭登录、验证码或访问拒绝而自动切换后端。

通过链接与输出目录校验、进入下载阶段后生成 `download-receipt.json`，包含去掉查询参数的页面地址、获取后端、UTC 时间、成功/失败状态；成功时含视频属性和 SHA-256。查询参数中的分享 token 不写入回执。精确可分享页面链接应由取证工作流从浏览器核实后单独登记。失败目录可能留有 `.part`，不得当成完整媒体或证据。

**可用性边界：**yt-dlp 官方列表含 Douyin、BiliBili，未检出快手条目；列出站点也不保证可用。[官方支持列表](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) 明确提醒站点变化会破坏提取器。遇到账号验证、验证码、地区/付费/DRM 限制时停止工具操作，交由用户完成正常平台交互或提供平台允许保存的本地文件；不轮换代理、不解 DRM、不使用在线解析站转交 Cookie。

## 对照本地视频

用户手动保存的视频也直接进入此步骤，无需上传云端：

```powershell
python scripts/media_probe.py compare --original "D:\维权项目\media\original-001\video.mp4" --candidate "D:\维权项目\media\candidate-001\video.mp4" --out "D:\维权项目\comparisons\case-001" --interval 1 --mirror
```

- `--interval` 是请求的最小采样时间间隔（秒），不是视频帧率。默认 1 秒，最小 0.1 秒。
- 默认每条最多 720 个样本。长视频会提高最小采样间隔；报告同时记录请求间隔、有效间隔和每帧真实 PTS。可用 `--max-frames` 调整到 8–2000。视频时长上限六小时。
- 时间戳来自 FFmpeg 解码后的 `showinfo`，以第一幅显示画面为 0 秒；变帧率不会用“帧号÷帧率”推算。实际间隔受源帧时间影响，例如请求 0.37 秒、10 fps 原片，下一采样点可能是 0.4 秒。[FFmpeg showinfo 文档](https://ffmpeg.org/ffmpeg-filters.html#showinfo) 解释了帧序号及 PTS 输出。
- 使用 256 位差分哈希与低分辨率灰度差异筛选候选；至少 3 个**相邻、顺序一致**的采样对，且两侧时间跨度近似一致。`--min-run` 可设 3–20。
- `--mirror` 另做水平镜像检查，对照图和 JSON 明示镜像；原视频与原始抽帧不改动。
- 近黑、近白、低纹理和静态采样点不参与相似序列。`byte_identical` 只表示两个文件字节相同，不能证明权属。
- 不识别人脸身份或语义场景，不分析音轨；大幅裁切、变速、重剪、少量片段可能漏检。未命中不能当作排除证据。

输出：`comparison.json`、`review.md`、`contact-sheet.jpg` 和两侧按顺序保存的抽帧。JSON 记录文件哈希、实际秒数、排除原因和最多 12 个候选序列。联系表展示候选段的起始/中间/末尾样本；无候选时展示方向性样本，图上明确提示不能当作匹配。候选区间的终点只是最后匹配采样帧，不能声称整段逐帧相同。

必须打开原视频与候选视频逐段复核：人物可见外观、场景独有细节、景别/构图/机位、动作及剪辑顺序、字幕水印、音画关系和遗漏内容。再结合原始工程、发布时间、权属、授权和白名单作判断。相同人物、相似场景或相同题材本身不构成搬运证据。

## 测试边界

```powershell
python -m unittest discover -s tests -p test_media_probe.py -v
```

已用合成几何画面测试截取片段、镜像、静态、黑帧与真实采样时间；Cookie 与远端响应测试只使用内存模拟数据。2026-10-14 实测一条获授权的抖音原作：yt-dlp 2026.08.19 无Cookie预检返回 `preflight/cookie_required`；在用户已登录、目标原作确实在播放的浏览器中，使用文档支持的 `video` 元素 `downloadMedia` 成功保存 196.95 秒、1280×720 的文件。另一次抖音候选浏览器保存失败；该回退同样不是成功保证。

同日B站一条公开候选通过修复后的CLI成功下载：视频轨约196.917秒、1280×720 HEVC，原作/候选本地比对输出5个顺序相似采样区间，关键帧已查看。修复点是预检不携带 `--max-downloads 1`，仅真正下载阶段限制单条，以免元数据输出前以101终止。此行为已加入回归测试。快手后端只有合成/模拟测试，尚无真实作品下载验证，不能把上述结果写成三平台全部可用。

同日两条抖音候选的无 Cookie CLI 预检均返回 `preflight/cookie_required`，未得到媒体；相同条件下不重复尝试其余候选。已有登录授权不等于提取器具备有效 Cookie，也不授权读取浏览器数据库。继续使用正常浏览器取证或用户提供的本地文件。

另一次通过已授权 Chrome 的目标 `video` 元素 `downloadMedia` 成功保存一条抖音候选：10.1 秒、1920×1080。以 0.5 秒间隔加镜像检查完成本地比较，输出零个顺序相似区间；人工查看21个候选样本后仍未确认其直接复制第1集。此结果只说明该次获取和复核完成，既不能据零命中排除全系列搬运，也不能推广为抖音候选均可下载。

浏览器回退：用当前工具文档操作已授权页面，先核对标题、作者和规范作品 ID；如有多个播放器，检查可见尺寸、时长和播放状态选择目标，不取隐藏推荐播放器。通过浏览器支持的媒体下载 API/平台保存按钮保存当前媒体，登记来源方式为 `authorized_browser_media`。不要抽取或回显签名媒体直链，不读取浏览器数据库或全站 Cookie；保存后核对视频时长和画面。此步骤是 agent 的浏览器工作流，不是 Python CLI 自动登录功能。

候选blob等媒体仍无法保存时，可使用[本地档案](case-workflow.md)的 `browser_visual` 模式保留官方页面、带媒体时间的取样截图及原片对应图。它不绕过下载限制，也不运行本地两视频相似度分析；只主张实际核验的至少两镜头四个对应点，不能把等待播放或顺序截图冒充全部中间帧的连续观看。

## 开源来源与选择

|项目|本工具中的用途|边界|
|---|---|---|
|[yt-dlp](https://github.com/yt-dlp/yt-dlp)|外部依赖，通过 CLI 封装单作品提取；遵循上游及依赖各自许可|未内嵌其代码；平台结构随时变化|
|[zyipeng/video-downloader](https://github.com/zyipeng/video-downloader)|参考快手公开 SSR 分享页方法，独立实现保守适配|MIT；不采用其全站 Cookie、直链日志、任意首个 MP4、高并发或质量参数修改策略|
|[DouK-Downloader](https://github.com/JoeanAmier/TikTokDownloader)|已调研的抖音项目，未集成|GPL-3.0；上游注明加密参数算法不再维护，不将其签名流程引入本工具|
|[KS-Downloader](https://github.com/JoeanAmier/KS-Downloader)|已调研的快手专用项目，未集成|独立项目，不能把其作者支持声明当成当前真实验证|

对照算法是本 skill 的本地辅助实现；不宣称任何项目提供法律侵权判定。安装时从官方 PyPI / 项目源获取依赖，不自动克隆运行其他项目。

为保留参考项目署名，附 [zyipeng/video-downloader 的 MIT 许可](https://github.com/zyipeng/video-downloader/blob/main/LICENSE)：

```text
MIT License

Copyright (c) 2026 zhaoyipeng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 脚本优先批处理（2026-10-16）

先读[脚本优先操作](script-first.md)。`search_public.py` 封装B站公开索引检索，`media_batch.py` 复用本案已登记媒体并有界下载/对照，`workflow.py`统一结果清单、草稿和报告。作者侧全部使用用户提供的截图和优先本地原片，不再要求作者账号授权登录；候选作品必要的浏览器取样使用检索举报账号。

`media_batch.py --root CASE` 默认离线，`--download`才启用公开下载；缓存绑定作品URL、内容哈希与比较参数，停止状态跨调用保留。遇取证限制转已有本地文件或正常平台保存，不把反复CU尝试当自动化。网络搜索/下载成功不代表来源、白名单、权属或人工复核已通过。
