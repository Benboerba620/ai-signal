# CPU Whisper 云端手动试跑

这是独立实验入口，不替换日常生产任务。只使用标准 Linux CPU、`faster-whisper` 的 `small.en` / `small`、int8 和最多 4 个线程；没有付费 ASR、GPU、服务器购买或自动回退。

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

整集也保留相同总时间/磁盘限制。不要把 5 分钟样本线性估算当成整集完成保证。此变更不自动运行整集、不自动发布转录。

## GitHub Actions

`.github/workflows/whisper-pilot.yml` 支持 `workflow_dispatch` 以及明确授权的分支试跑，权限为 `contents: read`，不注入 secrets；使用 `ubuntu-latest` 标准 CPU runner，所有结果（含失败）作为独立 artifact 保留 7 天。没有 git push、生产脚本调用、定时触发或付费 fallback。工作流输入通过环境变量引用，避免把标题/GUID插入 shell 程序。

GitHub 的 `workflow_dispatch` 初次注册要求 workflow 已在默认分支，但这不代表分支不能测试。2026-10-07 维护者明确要求先在分支实际试跑后，本 PR 新增了严格限定的 `push` 路径：

- 必须是 `codex/cloud-whisper-pilot` 分支。
- 必须修改 `.github/whisper-pilot-request.txt`。
- HEAD commit message 必须包含 `[run-whisper-pilot]`，三项同时满足才运行转录。
- 分支触发固定只跑 SemiAnalysis Ep.035、`small.en/en`、第 60 秒开始的 300 秒，不能借输入扩大到整集。
- 正常改代码/文档或 main push 不会触发此试跑；无需合并，也不使用 `pull_request_target`。

需要重试时先确认上一轮已结束、检查失败原因，再明确修改 request 文件和 marker commit。不要无意义反复启动。工作流保留 concurrency 和 30 分钟 worker / 40 分钟 job 上限。

本仓库为公开仓库，标准 GitHub-hosted runner 的免费规则适用；不要改用 larger runner 或增加付费资源。若仓库可见性、平台计费规则发生变化，运行前重新确认。

## 怎么看结果

`result.json` 包含：

- 来源 GUID/标题/URL（去除查询参数）、feed/配置/音频哈希、实际下载 MIME/字节数
- 音频下载、裁剪解码、模型下载+加载、完整 generator 推理、worker 总耗时
- 完整音频长度、实际解码样本长度、VAD 后人声长度
- `real_time_factor = inference_seconds / decoded_sample_seconds`；越小越快，只表示推理，不含冷启动
- Linux worker 的 `peak_process_rss_mib`、CPU 数量/支持的计算类型、线程数、模型、量化、beam、依赖版本
- complete/failed 状态与失败阶段；样本产物一律标记需要人工质量审阅

没有参考逐字稿时不声称准确率或 WER。峰值 RSS 只覆盖 Python/Whisper worker，不是整个 job 的累计内存。

## 本次验证记录（2026-10-07）

- 云端成功安装 faster-whisper 1.2.1 和其 CPU 运行依赖。
- 独立 SemiAnalysis Ep.035 的 5 分钟样本尝试，在音频域名解析阶段 `socket.gaierror` 失败，尚未下载实际音频、加载模型或执行推理。
- 因此没有真实音频速度、准确率、完整单集或 GitHub runner 下载成功的结论；代码测试与真实转录必须分开看。
- 离线自动测试覆盖实际 ffmpeg 裁剪探测、mock 推理产物、HTML/截断/体积拒绝、重定向、实际连接 DNS 防护、超时/磁盘预算、失败记录、成功哈希复用和生产输出保护。mock 推理与合成音频测试不是 Whisper 的真实人声测试。
- 原生产流程的来源政策、两次尝试额度、Volc 配置与日程保持原样。

## 后续再评估

当前 feed 是滚动快照；节目退出窗口后，本入口不会扩大历史发现范围。这次只验证后端可行性，不引入持久生产队列。若样本及整集的速度/质量通过，再另行设计以 GUID 为键的持久队列、失败次数/下次重试/来源身份、转录保存及发布审核，解决“等转录时已退出 feed 窗口”的问题。

参考：[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[CPU 量化](https://opennmt.net/CTranslate2/quantization.html)、[GitHub 事件触发](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push)、[GitHub 手动运行](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[Actions 计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
