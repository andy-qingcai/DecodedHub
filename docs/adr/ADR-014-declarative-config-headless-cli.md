# ADR-014 · 声明式项目配置与 headless CLI（decodehub.toml + run/validate/diff）

- 状态：已接受（2026-09-04）
- 背景：平台唯一入口是 MCP stdio server——任何解码都要"起 LLM 会话 → 逐个调工具"。
  项目级/团队场景（配置进固件仓库、CI 回归、批处理、多人共享）评估出五类缺口
  （配置入口、批处理、协作语义、结果回归、扩展点）。本 ADR 处理前两类 +
  为后三类打地基。

## 决策

1. **两层配置模型，各管一段**：
   - **工程档案 Profile（ADR-009，不变）**：固化"源定义 + 协议锁 + 通道角色"，
     是工具生成/可手写的 JSON。ADR-014 补强：`validate_profile_dict` 字段级
     校验（未知字段即报错，防拼写）、`tool_version` 记录保存时的 decodehub 版本、
     `schemas/profile.v1.schema.json` 供 IDE 校验。
   - **项目配置 `decodehub.toml`（新，`app/config.py`）**：自上而下的**手写**
     配置源。档案刻意不固化的东西放这里：采集文件绑定（路径/**glob**）、
     输出管线（导出 formats、渲染 timing/analog + 时间窗）、产物布局
     （`out_dir`，默认 `reports/`）。可引用档案（`profile = "名"`）或内联
     解码定义（`[runs.X.decode]`，同一 ProfileSpec 模型）——团队可只维护
     一个 toml。所有相对路径相对 toml 所在目录；字段严格校验。
2. **headless 运行器（`app/runner.py`）**：`run_config()` 把同一批应用层用例
   （ingest / lock_protocol / run_decode / export / render）编排成批处理——
   每采集集一个独立 SessionState，**只调用 services 公共函数，不改它们**，
   MCP 与 CLI 两条入口共享同一应用层语义。产物布局：
   `out_dir/<run>/<采集集label>/`（decoded.json 机器汇总 + events.* 导出 +
   渲染图），运行级 `index.md` + `summary.json`。
3. **CLI（`cli/`，入口 `decodehub`）**：`validate`（配置/档案/采集绑定全检，
   不解码，CI 首道防线）、`run`（`--run`/`--capture 别名=路径` 覆盖/`--out`/
   `--fail-fast`）、`diff`（两份 decoded.json 对比）。退出码 0/1/2。
   标准库 argparse，无新依赖（TOML 用 3.11+ 内置 `tomllib`）。
4. **批量语义（`expand_captures`）**：glob 命中 N>1 的别名是主变量（字典序），
   命中 1 的别名广播；多个别名同时 >1 且数量不等 → 明确报错（宁可不猜，
   不做隐式笛卡尔积）。label = `001_stem` 序号前缀，两次运行顺序确定。
5. **diff 语义（`app/diffing.py`）**：按报告键逐位对齐、**忽略时间戳**——
   回归场景两次采集的绝对时刻必然不同；签名 = 事件全部字段（除 t_start/
   t_end）的规范 JSON。不做 LCS：同档案同配置解码是确定性的，错位本身
   就是要暴露的差异。报告给每类型计数对照 + 首批分歧点两侧上下文。
6. **`decoded.json` 是 diff/CI 的稳定契约**：运行恒产生（不受 export.formats
   影响），含 tool_version/config/run/label/全量事件。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 扩展 MCP 工具集承载批处理/配置 | 仍依赖 LLM 会话，CI/脚本无法用；上下文成本高 |
| yaml/json 项目配置 | toml 有注释（团队配置需要"为什么钉死 D0"）+ stdlib tomllib 零依赖 |
| 配置里内嵌全部档案内容（不引用 JSON） | 档案已由工具生成且可共享；引用 + 内联二选一，迁移成本低 |
| 笛卡尔积展开多维 glob | 隐式组合爆炸；显式拆 run 更安全 |
| LCS/动态对齐 diff | 确定性解码下错位即差异；对齐猜测会掩盖真正的回归 |
| diff 输入用 events.csv | csv 丢协议字段类型；decoded.json 无损且已有 |

## 后果

- 正面：CI/脚本一条命令出报告（无需 LLM）；档案/配置可提交固件仓库进 code
  review；批处理 + index/summary 天然支持回归；`tool_version` 为升级排查提供锚点；
  services.py 零改动，与并行开发冲突面最小。
- 负面：两处配置（toml + 档案 JSON）有学习成本——内联 decode 可消除但团队需
  自行取舍；diff 忽略时间戳意味着"时序行为变化"（间隔抖动）不在对比范围；
  协议插件机制（项目自带协议不 fork 平台）与多会话部署仍开放（后续 ADR）。
