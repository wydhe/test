# research-ops：用 Claude Code 在 4×A800 上管理长周期科研项目

目标有三个：**Claude 不失忆**、**token 不随项目时长线性膨胀**、**4 张卡不闲置**。
做法：把「记忆」从对话搬到文件，把「等待和调度」交给常驻的队列 gpuq，Claude 只在需要判断的时候出场。

> 本目录是通用工具，不含任何具体课题内容（仓库是公开的）。项目专属的 PLAYBOOK、实验登记表和 GPU 分配方案单独私下提供，请不要提交到公开仓库。

---

## 1. 为什么 token 越用越多，还会「失忆」

| 原因 | 说明 |
|---|---|
| 会话越长，每一轮越贵 | 每次提问都要把整段历史重新发给模型。第 200 轮的一句「看看结果」比第 1 轮贵几十倍。 |
| 读原始日志 | 一次 `cat train.log` 就可能是上万 token，而且留在上下文里，之后每一轮都要再付一次。 |
| 让 Claude 等训练结束 | sleep、反复查看进度，是最贵的用法。 |
| 自动压缩是有损的 | 上下文满了会自动 compact。实验数字、为什么放弃某个方案这类细节会丢，这就是「失忆」。 |

对策：**短会话 + 文件记忆 + 摘要工具 + 异步通知**。

## 2. 架构

```
                 你（决策）
                    │  /status  /triage  /next  /handoff  /weekly  /review-packet
                    ▼
  ┌──── Claude Code 主会话（短；做完一件事就 /handoff + /clear）────┐
  │ 启动时 hook 自动注入：STATE.md + 队列状态 + 未处理 INBOX         │
  │ 子代理 log-analyst（Haiku）负责读日志，只回 ≤20 行摘要            │
  └───────┬─────────────────────────────────────┬──────────────────┘
          │ 提交任务（jsonl batch）               │ 读 / 写
          ▼                                     ▼
  gpuq daemon（tmux 常驻）               research/*.md  ← 记忆（唯一真相源）
  按显存 + CPU 把任务打包到 4 张卡         STATE / EXPERIMENTS / DECISIONS / PLAYBOOK / INBOX
          │ 任务结束 → on_finish hook ───────────┘ 写 INBOX（可选：手机通知、无人值守 triage）
          ▼
  runs/<实验ID>/{config.json, metrics.jsonl, result.json} → digest_log / results_table 摘要

  外部评审（只在里程碑用）：/review-packet → ARIS + Codex（ChatGPT Plus），或手动贴给 ChatGPT
```

## 3. 文件一览

| 路径 | 作用 |
|---|---|
| `CLAUDE.md` | 项目宪法（≤ 100 行，每轮加载）：记忆规则、gpuq 规则、省 token 硬规则、实验规范 |
| `research/STATE.md` | 当前状态（≤ 60 行），每次会话开始自动注入 |
| `research/EXPERIMENTS.md` | 实验登记表：假设、门槛、配置、gpuq id、结果、判定 |
| `research/DECISIONS.md` | 决策日志：证据 → 决策 → 回滚条件 |
| `research/PLAYBOOK.md` | 失败模式 → 诊断 → 对策（`/triage` 用来匹配不好的结果） |
| `research/INBOX.md` | gpuq 自动写入的任务结束通知 |
| `tools/gpuq.py` | 按显存打包的 GPU 队列：依赖、优先级、独占、防饿死、OOM 自动加显存重试、CPU 槽位 |
| `tools/digest_log.py` | 把任意大小的日志压成约 20 行：进度、各指标首/最佳/末值、NaN、OOM、最后一个异常 |
| `tools/results_table.py` | 把几十上百个 result.json 汇总成「均值 ± 标准差」表 |
| `tools/rlog.py` | 训练脚本里用的日志器，统一输出 config / metrics / result |
| `tools/status.sh` | 一屏状态（约 40 行） |
| `tools/plan_section.sh` | 按节号读计划书，避免整篇读取 |
| `tools/inventory.sh` | 一次性盘点已经在跑的项目（`/bootstrap` 用） |
| `tools/snapshot.sh` | 把 GPU 状态、最近日志摘要、结果文件、Claude 执行过的实验命令、脚本参数压成一个 `research/snapshot.md`（不花 token，`/catchup` 用） |
| `tools/session_context.sh` | SessionStart hook：自动注入记忆 |
| `tools/statusline.sh` | Claude Code 状态栏：GPU / 队列 / INBOX，不花 token |
| `tools/on_job_finish.sh` | 任务结束 hook：写 INBOX，可选通知或无人值守 triage |
| `.claude/commands/*.md` | `/bootstrap` `/catchup`（低额度一次性：看结果 + 排队）`/status` `/triage` `/next` `/handoff` `/weekly` `/review-packet` |
| `.claude/agents/log-analyst.md` | 读日志的廉价子代理（Haiku） |
| `.claude/settings.json` | hook、状态栏、免确认的只读工具权限、Bash 输出上限 |

## 4. 安装（约 10 分钟）

```bash
git clone <本仓库> ~/research-ops-kit
bash ~/research-ops-kit/research-ops/install.sh /path/to/your/project   # 不会覆盖任何已有文件
cd /path/to/your/project
cp /path/to/plan.md docs/plan.md
tmux new -d -s gpuq 'python3 tools/gpuq.py daemon >> .gpuq/daemon.log 2>&1'
python3 tools/gpuq.py doctor        # 检查 GPU、显存计量方式、daemon
```

然后编辑 `CLAUDE.md` 第 0 节（项目名、conda 环境、数据路径），启动 `claude` 并运行 `/bootstrap`。

- 如果你已有 `CLAUDE.md` 或 `.claude/settings.json`，安装脚本会保留它们，把新版本存为 `*.research-ops.*`；`/bootstrap` 会帮你合并 CLAUDE.md。
- `doctor` 提示「not visible in /proc」（例如在 docker 里跑）时，把 `.gpuq/config.json` 里的 `accounting` 改成 `"warmup"`。
- `.gpuq/config.json` 的主要参数：`safety_gb`（每卡预留，默认 3G）、`max_jobs_per_gpu`（默认 6）、`cpu_slots`（默认等于 CPU 核数）、`default_cpus`（每个任务默认占的 CPU 槽，默认 4）、`gpus`（只管理哪些卡）。

## 5. 第一次使用：接管已经在跑的项目

运行 `/bootstrap`，它会：
1. 用 `inventory.sh` 盘点：GPU 上的进程（pid、运行时长、工作目录、命令行）、tmux/screen 会话、最近 7 天变动的日志、结果、checkpoint 和代码、git 状态。
2. 只读计划书的结论、实验矩阵、时间线和风险几节。
3. 用 digest（日志多时交给 log-analyst 子代理）弄清每个实验的进度和关键指标。
4. 把已有实验对应到计划编号，填写 EXPERIMENTS.md；写 STATE.md（当前在第几周、哪些门槛已过或未过、风险、下一步）。
5. **不动正在跑的旧进程**。gpuq 通过 nvidia-smi 能看到它们占用的显存，会自动避开。

完成后执行 `/handoff`，再 `/clear`。之后每次开会话，Claude 都会自动「记起」项目状态。

## 6. 日常节奏（每天 2–3 个短会话）

| 时机 | 你做什么 | token 开销 |
|---|---|---|
| 随时看进度 | 看 Claude Code 状态栏，或在 tmux 里 `watch -n 60 bash tools/status.sh` | 0 |
| 有任务结束（状态栏 inbox > 0） | `claude` → `/triage` | 小 |
| 有 GPU 空闲（status 显示 IDLE） | `/next` | 小 |
| 写新方法、改代码、疑难调试 | 正常对话，用 Opus | 大，但值得 |
| 一件事做完 | `/handoff` → `/clear` | 小 |
| 每周一次 | `/weekly` | 小 |
| 里程碑或卡住 | `/review-packet`（外部评审） | 外部额度 |

**遇到不好的结果时，`/triage` 的流程**：digest 取证 → 和登记表里的门槛对比 → 判定 ✅ / ⚠️ / ❌ → 在 PLAYBOOK 里按症状匹配，给出主对策和备选（附 GPU·h）→ 写进 EXPERIMENTS 和 DECISIONS → 便宜且不改主线的修复（< 4 GPU·h）直接提交，大的决定写进 STATE「待我决策」等你拍板。
同一方向连续两次失败时，它会停下来要求复盘和外部评审，不会无限加码。每解决一个新问题，就把它追加到 PLAYBOOK，下次直接命中。

## 7. 把 4 张 A800 用满（通用原则）

1. **任务分三类**：
   - 关键路径的大训练（例如生成器）：`-g 2 -x` 独占两卡、高优先级；
   - 吞吐型小任务（分割、回归、评估，每个 < 20 GB）：共享 GPU，一张卡并发 4–6 个；
   - 可切片的推理（大规模采样、配对生成）：切成多个单卡分片，填别人留下的空隙。
2. **实验吞吐比单个实验的延迟更重要**：一个模型用 2 卡 DDP 已经够快时，不要 4 卡跑它；同时跑两个变体或两条轨道收益更大。
3. **先测再报显存**：第一个任务的 `--mem` 估大一点，跑起来后用 `gpuq.py doctor` 看实测峰值，之后的同类任务按实测值加 20% 填写。OOM 会自动按 ×1.3 重试。
4. **CPU 常常是隐藏瓶颈**：nnU-Net 这类数据增强很吃 CPU。`cpu_slots` 默认等于核数，用 `--cpus` 声明每个任务的 worker 数，gpuq 不会让 CPU 超卖。
5. **大任务不会被饿死**：独占或多卡任务排不上时，gpuq 会把选中的卡「排空」，不再往上放新的小任务，等现有任务结束就启动大任务。
6. **离线预计算**：视频生成训练前，把 VAE latent 一次性编码存盘（或放进内存），训练时不再经过编码器。
7. **先冒烟再全量**：长任务先跑 10 分钟的小版本，全量版用 `-a` 依赖它，避免跑了一夜才发现 bug。
8. **看账**：`gpuq.py report --days 7` 按实验组统计 GPU·h 和失败浪费。目标：卡时利用率 ≥ 85%，失败浪费 < 10%。
9. 这些原则已经写进 `/next`：它会按原则排出未来 24–48 小时的队列，你确认后一键提交。

## 8. 省 token 清单（按效果排序）

1. **短会话**：`/handoff` → `/clear`。这是最大的一项。不要等自动 compact，用 `/context` 看占用，超过一半就收尾。
2. **SessionStart 注入 STATE.md**（约 1–2k token），代替「请先熟悉一下项目」（动辄几万）。
3. **摘要工具代替原始日志**：digest 约 20 行，results_table 一张表，status 约 40 行。
4. **log-analyst 子代理（Haiku）**：脏活在子上下文里做，主会话只拿摘要。
5. **计划书按节读**：整篇计划一万多 token，按节读只要几百。
6. **提交后结束回合**：不轮询，结果通过 INBOX 异步到达。
7. **状态栏**：给你看，不进模型上下文。
8. **模型分工**：监控、登记用 Sonnet（`/model sonnet`），方法设计、调试、写作用 Opus。
9. **精简 skill 和 MCP**：每个已安装 skill 的描述、每个 MCP 的工具定义，每一轮都占上下文。ARIS 有 80 多个 skill，只装用得上的几个。
10. **Bash 输出上限**：settings.json 把单次命令输出限制在 15000 字符，防止一条命令灌爆上下文。

## 9. ARIS（Auto-claude-code-research-in-sleep）能不能用？

**结论：可以用，但把它当作「外部评审 + 写作」层，而不是执行层。**

ARIS 是一组 Markdown skill（80 多个），核心思想是「Claude 执行、另一个模型（默认 Codex / GPT）评审」的跨模型审查循环。它还提供 research-wiki、experiment-queue、paper-writing、citation-audit、rebuttal 等技能。

**推荐用的部分：**

| 阶段 | ARIS skill | 用法 |
|---|---|---|
| 里程碑评审（例如 go/no-go、主表初版） | `/research-review`（单次），或 `/auto-review-loop`（difficulty 用 medium，打开 HUMAN_CHECKPOINT） | 材料用 `/review-packet` 生成的压缩包 |
| 结果 → 论文主张 | `/result-to-claim` | 检查每条 claim 是否有实验支撑 |
| 补文献 | `/research-lit` | 写相关工作之前 |
| 写作 | `/paper-writing`、`/figure-spec` | 写作阶段 |
| 投稿前 | `/citation-audit`、`/kill-argument` | 防止幻觉引用；做对抗式找茬 |
| 审稿意见回来 | `/rebuttal` | — |

**不建议用的部分：**
- `/research-pipeline`、`/idea-discovery`：你的想法和计划已经定了，重跑全流程既费 token，也可能把方向带偏。
- `/experiment-queue`：它为「笔记本 + SSH 远程 GPU」设计，每张卡一次只跑一个任务、一波一波推进，没法在一张 80G 卡上并发多个小任务。你是在服务器本机跑 Claude，用 gpuq 更合适。
- `nightmare` 难度：评审方会读整个仓库，既贵，又会把未发表的方法细节交给外部模型。
- 不要用 `/loop` 或定时任务包装 auto-review-loop。ARIS 文档自己也说明，这样会丢失评审线程的记忆。

**安装**（依据 2026-10 的 ARIS README；它更新很快，以你安装时的 README 为准）：

```bash
npm install -g @openai/codex && codex login          # 用 ChatGPT Plus 账号登录
git clone https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep.git ~/aris_repo
bash ~/aris_repo/tools/install_aris.sh /path/to/your/project   # 支持按组或按 skill 选装，先看说明
claude mcp add codex -s user -- python3 ~/aris_repo/mcp-servers/codex-exec/server.py
claude mcp list                                       # 确认 codex 显示 Connected
```

也可以用插件方式（`claude plugin marketplace add wanshuiyin/Auto-claude-code-research-in-sleep`，然后 `claude plugin install aris@aris`）。但这样会装上全部 skill，每轮占用的上下文更多，所以更推荐用 install_aris.sh 选装。
注意：codex-cli 0.154 移除了 `codex mcp-server`，ARIS 随后改成了 `codex-exec` 桥接；旧的安装方式需要按它的 README 重新注册。

## 10. ChatGPT Plus 怎么用最划算

- **额度**：Plus 账户在 Codex 上同时有 5 小时窗口和每周上限（2026-08 起对 Plus 恢复了 5 小时窗口），CLI、IDE、云端共用同一额度。ARIS 默认用最高推理强度做评审，额度消耗很快。所以只在里程碑用，每周 1–3 次深度评审。
- **分工**：
  - Codex（通过 ARIS）：对「结果与主张」做对抗式审查，找漏洞和缺失的对照；
  - ChatGPT 网页：deep research 补文献、找最新的竞争工作；把 review packet 贴进去做「审稿人模拟」；英文润色；
  - Claude Code（服务器本机）：执行、写代码、跑实验、记录。
- **隐私**：把未发表的想法发给任何外部模型之前，先检查 ChatGPT 账号「设置 → 数据控制」里是否允许用你的数据训练模型（Codex 用的是同一个账号）；packet 里不要放患者数据、服务器路径和代码。

## 11. 其他值得用的工具（按性价比）

1. **Claude Code 内置能力**：子代理、slash command、hooks、状态栏、`/context`、`/model`。本套件就是用它们搭的，没有额外成本。
2. **superpowers 插件**（obra/superpowers）：systematic-debugging、brainstorming、writing-plans 等流程类 skill，适合在「结果不好、要找根因」时配合 `/triage` 使用。
3. **Orchestra Research 的 AI-research-SKILLs**：有 ml-paper-writing 和训练配方类 skill。我没有实测过，写作阶段可以挑一两个试用。
4. **实验曲线**：W&B 或 TensorBoard 给你自己看；Claude 那边仍然用 digest，不要让它去读网页面板。
5. **文献管理**：Zotero + Better BibTeX；投稿前再用 ARIS 的 `/citation-audit` 查一遍。
6. **不推荐**：从想法到论文全自动的「AI 科学家」类框架。你的项目已经有明确计划，又涉及严谨的医学评估，这类框架的可控性和可信度都不够。

## 12. 常见问题

- **已经在跑的实验要迁到 gpuq 吗？** 不用。新任务走 gpuq，旧进程跑完为止；gpuq 会把它们占用的显存算作外部占用。
- **多人共用服务器？** 别人的进程同样算外部占用；需要给别人留卡时用 `gpuq.py hold 3`，用完 `release 3`。
- **手机通知**：在 `.gpuq/hook.env` 写 `NOTIFY_CMD='curl -s -d "$MSG" https://ntfy.sh/<你的主题>'`。Server酱、企业微信机器人的 webhook 同理。
- **想完全无人值守（「睡觉时做科研」）？** 在 `.gpuq/hook.env` 写 `GPUQ_AUTO_TRIAGE=1`。某个 tag 的任务全部结束时，会用 Sonnet 无头模式跑一次 `/triage`：只登记和诊断，只允许提交 < 4 GPU·h 的修复，大的决定留给你，日志在 `research/triage_logs/`。建议先手动用一周，确认 triage 的判断靠谱再打开。
- **daemon 挂了怎么办？** 正在跑的任务不受影响，重新启动 daemon 后会自动接管。状态栏和 status 会显示 `gpuq DOWN`。

## 13. 测试

```bash
python3 research-ops/tests/test_tools.py -v    # 用假 GPU 测试，不需要 CUDA
```

覆盖：多卡分散放置、显存打包、外部占用避让、CPU 槽位、hold / only、独占多卡任务的排空防饿死、依赖与依赖失败、OOM 加显存重试、普通失败不重试、取消、手动重试、on_finish hook、status / report 输出、日志摘要与结果汇总。
