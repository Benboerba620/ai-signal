# CPU Whisper 云端手动试跑

本页记录独立实验入口和历史试跑；实验入口本身不写生产 feed。2026-10-08 经维护者授权的正式接入另见 [生产维护说明](cloud-whisper-production.md)。只使用标准 Linux CPU、`faster-whisper` 的 `small.en` / `small`、int8 和最多 4 个线程；没有付费 ASR、GPU、服务器购买或自动回退。

## 边界

- `scripts/cloud_whisper_pilot.py --list` 只列出当前 feed 中、现有来源策略允许且缺少转录的直链音频。复用 `candidate_items()`，不使用 `force_channels`，不提取 YouTube 音频、不扩大名单。
- 必须指定一个精确 GUID，没有批量或定时入口。音频从 feed 原始 enclosure URL 获取，记录 feed/配置/音频 SHA-256；重定向也必须是无凭证的公开 HTTPS。
- 拒绝非 200 状态、HTML/JSON/播放列表、缺失或不合适的 Content-Type、长度不符、超出 128 MiB 的下载，以及不能解码或与 feed 时长明显不符的音频。下载后的实际时长/字节数再次检查现有来源最小值。
- 默认只转录从第 60 秒开始的 300 秒；样本从解码文件物理裁剪，不能当作完整单集字幕。单集音频最多 2 小时。
- 整个 worker（含 DNS、下载、模型下载与转录）最多 30 分钟；临时空间每 250ms 检查 2 GiB 上限、单文件上限 1 GiB；超过后杀掉进程组。空间上限检查存在短暂写入超调，不是文件系统配额。原始音频、PCM 和模型临时文件退出时清理。
- 首次模型下载也计入冷启动；不继承 API key、cookies、代理凭证或 Hugging Face token；HOME/HF/Xet 缓存均隔离在临时目录，禁用隐式认证和 Xet。此入口使用直接 HTTPS 网络，要求执行机器能解析并连接公开音频及 Hugging Face 模型域名。网络限制/拒绝直接失败，不换下载源绕过。
- 输出只有实验 transcript、时间戳 segments 和 result JSON；仓库内输出只能放在 gitignored `pilot-output/`。不会改 feed、索引、生产转录、配置或现有 workflow。
- 同一输入已有完整且 SHA-256 一致的成功产物时复用；失败会保存状态，重跑最多单次尝试，没有隐藏重试。并发相同任务会被锁拒绝。进程被强制断电后残留锁需人工确认没有活跃任务再移除。

## 本地 Linux / 云工作区

需要 Python 3.11+、ffmpeg、ffprobe。依赖单独安装，不加进用户或中央生产依赖：

```sh
python -m venv .venv-pilot
. .venv-pilot/bin/activate
pip install -r requirements-whisper-pilot.txt
python scripts/cloud_whisper_pilot.py --list
python scripts/cloud_whisper_pilot.py \
  --guid '从列表复制的精确 GUID' \
  --model small.en --language en \
  --sample-seconds 300 --start-seconds 60
```

`small.en` 仅配 `en`；多语种使用 `small --language auto` 或 `zh`。先查看样本是否有足够真实人声、术语和人名是否可靠、是否遗漏或幻觉，再决定整集：

```sh
python scripts/cloud_whisper_pilot.py \
  --guid '同一期 GUID' --sample-seconds 0 --start-seconds 0
```

整集也保留相同总时间/磁盘限制。不要把 5 分钟样本线性估算当成整集完成保证。整集必须明确授权；当前维护者已授权同一期整集分支试跑，不自动发布转录。

## GitHub Actions

`.github/workflows/whisper-pilot.yml` 支持 `workflow_dispatch` 以及明确授权的分支试跑，权限为 `contents: read`，不注入 secrets；使用 `ubuntu-latest` 标准 CPU runner，所有结果（含失败）作为独立 artifact 保留 7 天。没有 git push、生产脚本调用、定时触发或付费 fallback。工作流输入通过环境变量引用，避免把标题/GUID插入 shell 程序。

GitHub 的 `workflow_dispatch` 初次注册要求 workflow 已在默认分支，但这不代表分支不能测试。2026-10-07 维护者明确要求先在分支实际试跑后，本 PR 新增了严格限定的 `push` 路径：

- 必须是 `codex/cloud-whisper-pilot` 分支。
- 必须修改 `.github/whisper-pilot-request.txt`。
- HEAD commit message 必须包含 `[run-whisper-full]`，三项同时满足才运行转录。此前 5 分钟样本用 `[run-whisper-pilot]`，该旧 marker 现在不会启动整集。
- 2026-10-07 用户明确要求更长测试后，当前分支触发固定跑同一期 SemiAnalysis Ep.035 完整音频（起点 0、sample_seconds=0），仍使用 `small.en/en`、CPU int8、4 线程。模型和已有 30 分钟 worker 上限不变；不能借输入换节目或模型。
- 正常改代码/文档或 main push 不会触发此试跑；无需合并，也不使用 `pull_request_target`。

需要重试时先确认上一轮已结束、检查失败原因，再明确修改 request 文件和 marker commit。不要无意义反复启动。工作流保留 concurrency 和 30 分钟 worker / 40 分钟 job 上限。

本仓库为公开仓库，标准 GitHub-hosted runner 的免费规则适用；不要改用 larger runner 或增加付费资源。若仓库可见性、平台计费规则发生变化，运行前重新确认。

## 怎么看结果

`result.json` 包含：

- 来源 GUID/标题/URL（去除查询参数）、feed/配置/音频哈希、实际下载 MIME/字节数
- 音频下载、裁剪解码、模型下载+加载、完整 generator 推理、worker 总耗时
- 完整音频长度、实际解码样本长度、VAD 后人声长度
- `real_time_factor = inference_seconds / decoded_sample_seconds`；越小越快，表示完整转录处理（含 faster-whisper 内部解码、VAD、特征处理和模型计算），不含模型下载/加载
- Linux worker 的 `peak_process_rss_mib`、CPU 数量/支持的计算类型、线程数、模型、量化、beam、依赖版本
- complete/failed 状态与失败阶段；样本产物一律标记需要人工质量审阅

没有参考逐字稿时不声称准确率或 WER。峰值 RSS 只覆盖 Python/Whisper worker，不是整个 job 的累计内存。

## 本次验证记录（2026-10-07）

- 云端成功安装 faster-whisper 1.2.1 和其 CPU 运行依赖。
- 最初在云工作区的尝试于 `socket.gaierror` 停止；随后维护者明确要求分支测试，使用 GitHub 标准 runner 跑通同一来源。
- 初次 GitHub 分支任务发现 runner 缺少 ffmpeg；已补 Ubuntu 官方包安装。修复后的 [run 37636421548](https://github.com/Benboerba620/ai-signal/actions/runs/37636421548) 全部成功，实际运行 commit 为 `5985b62c0633581164d4892b028c3451fa03c5a9`。
- 原音频 GET 成功：33,289,194 字节、`audio/mpeg`、实际时长 2,080.549 秒；音频哈希与全部测量见 [原始 stats](benchmarks/whisper-semi035-20261007.json)。
- 真实人声样本：第 60 秒起 300 秒，VAD 后 298.4 秒；small.en、CPU int8、4 线程、beam 5。
- 下载 0.301 秒，裁剪解码 1.121 秒，首次模型下载+加载 11.083 秒，转录处理 63.639 秒，worker 合计 76.744 秒；RTF 0.2121，约 4.7 倍实时速度。整个 GitHub job（含环境安装/测试/上传）约 1 分 54 秒。
- Python/Whisper worker 峰值 RSS 868.1 MiB。样本文本 4,946 字符，时间戳与文本 [artifact](https://github.com/Benboerba620/ai-signal/actions/runs/37636421548/artifacts/11490270656) 保留至 2026-10-14。
- 仅做文本可读性检查：整体连贯、未见明显长段循环，但专名/术语有疑似误识，例如 `Infin-SEX`、`Plus and Max`、`Avalanche`。未逐句对音、无参考稿，不能报告准确率或 WER。当前样本不能据此认定能直接替换生产 ASR。
- 上述样本运行只跑 5 分钟；整集另见下方独立记录。样本处理速率不能直接当作整集完成时间保证。
- 离线自动测试覆盖实际 ffmpeg 裁剪探测、mock 推理产物、HTML/截断/体积拒绝、重定向、实际连接 DNS 防护、超时/磁盘预算、失败记录、成功哈希复用和生产输出保护。mock 推理与合成音频测试不是 Whisper 的真实人声测试。
- 原生产流程的来源政策、两次尝试额度、Volc 配置与日程保持原样。

## 后续再评估

当前 feed 是滚动快照；节目退出窗口后，本入口不会扩大历史发现范围。这次只验证后端可行性，不引入持久生产队列。若样本及整集的速度/质量通过，再另行设计以 GUID 为键的持久队列、失败次数/下次重试/来源身份、转录保存及发布审核，解决“等转录时已退出 feed 窗口”的问题。

参考：[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[CPU 量化](https://opennmt.net/CTranslate2/quantization.html)、[GitHub 事件触发](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push)、[GitHub 手动运行](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[Actions 计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。


### 整集验证（已完成）

2026-10-07 用户明确要求更长测试后，在相同分支、相同来源、相同 small.en CPU int8 / 4 线程 / beam5 设置下完成 [整集 run 37639973674](https://github.com/Benboerba620/ai-signal/actions/runs/37639973674)。实际执行 commit：`016a39cf509330f77306179ae4ff8ce8c1597a01`；main 与生产 feed 均未修改。此前 5 分钟结果保留，不覆盖。

- 请求 sample_seconds=0、start_seconds=0。原音频 2,080.549 秒，实际解码 **2,080.508 秒（34分40.508秒）**，差 0.041 秒；来源音频 SHA-256 与样本测试完全相同。
- 转录处理 **432.854 秒（7分12.854秒）**；下载 0.403 秒，裁剪解码 5.393 秒，冷模型下载/加载 5.869 秒；总 worker **445.219 秒（7分25.219秒）**。RTF **0.2081**，约 **4.8 倍实时**。这里的转录处理包括 faster-whisper 内部解码、VAD、特征和模型计算，不是单独模型内核耗时。
- VAD 保留 2,060.864 秒。峰值 Python/Whisper RSS **2,524.5 MiB（约2.47GiB）**；5分钟样本为868.1MiB。长音频的内存上涨明显；2GiB临时磁盘预算不是RAM限制，不能据此保证任意2小时节目都能稳定跑。
- 输出 **33,252 字符、6,008 词、671 段**。时间戳单调、无负时间、无超过音频尾部的段。末段结束 **2,072.73 秒（34分32.73秒）**，已包含结束告别语；与音频终点相差7.778秒，可能是尾静音，尚未逐句对音确认。
- 未发现连续相同段或长度≥40字符的精确重复段。存在短段内重复词及疑似专名/术语误识；整体可读并不证明逐字准确。没有人工参考稿/音频对齐，不报WER或准确率，不自动替换生产ASR。
- [整集原始stats与机械检查](benchmarks/whisper-semi035-full-20261007.json) 永久保留在此PR；[字幕、segments与stats artifact](https://github.com/Benboerba620/ai-signal/actions/runs/37639973674/artifacts/11492790936) 保留至2026-10-14。ZIP SHA-256：`98ee5c2098ef771c5e8a176cc688ecdee2d19b44328c536144c511eedee23441`，已取回并校验三份产物哈希。

结论：此35分钟节目已经实际跑通，速度与样本接近；内存和领域词识别仍需在生产设计中处理。没有合并、发布转录到feed或调用付费API。
