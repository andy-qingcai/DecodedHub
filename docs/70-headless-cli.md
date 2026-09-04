# Headless CLI：项目配置驱动的自动化解码（ADR-015）

MCP 入口面向 LLM 交互；本篇是**无 LLM** 的一条命令解码——配置进固件仓库、
CI 可跑、多人共享同一份配置。

> 手边没有仪器/采集文件？先看 [README 的「无仪器冷启动」](../README.md#无仪器冷启动)——
> 用内置合成器造一份采集，5 分钟跑通 配置 → 校验 → 解码 全流程。

## 项目布局约定

```
固件仓库/
├── decodehub.toml        # 项目配置（手写、可评审）—— 所有相对路径相对本文件
├── profiles/             # 工程档案（可工具生成，也可手写；JSON Schema 见 schemas/）
├── captures/             # 采集文件（约定俗成，配置里用 glob 绑定）
└── reports/              # 产物（运行生成；建议 gitignore）
```

安装：`pip install -e <decodehub 仓库>` 后获得 `decodehub` 命令。

## decodehub.toml 参考

```toml
version = 1                        # 当前仅支持 1

[project]                          # 可选段
name = "gizmo-v3"
description = "v3 主板 UART/I2C"
profiles_dir = "profiles"          # 默认 "profiles"
out_dir = "reports"                # 默认 "reports"

# ---- 至少一个 [runs.<名字>] ----
[runs.main]
profile = "gizmo-v3"               # 方式一：引用 profiles/ 下的档案
# 方式二：内联解码定义（与档案同一模型，二选一）
# [runs.main.decode.sources.la]
# format = "kingst_kvdat"          # 省略 = 自动嗅探
# [runs.main.decode.sources.la.options]
# sample_rate = 200_000_000
# [runs.main.decode.locks.la]      # 表形式：每源一把锁（缺省名 = 协议名）
# protocol = "i2c"
# params = { scl = "D0", sda = "D1" }
# 数组形式（ADR-023）：同源多路同协议并存，用 name 区分
# [[runs.main.decode.locks]]
# source = "la"
# protocol = "uart"
# name = "uart1"                    # 报告键 = la|uart1；params 钉死各自通道
# params = { rx = "D0", baud = 115200 }
# [[runs.main.decode.locks]]
# source = "la"
# protocol = "uart"
# name = "uart2"
# params = { rx = "D2", baud = 9600 }

[runs.main.captures]               # 源别名 → 路径或 glob（必需段）
la = "captures/*.kvdat"
scope = "captures/ref.csv"         # 单文件在批量时自动广播

[runs.main.export]                 # 可选：导出（csv/json/md）
formats = ["csv", "md"]
# source = "la"                    # 可选：只导某源/某协议
# protocol = "i2c"

[runs.main.render]                 # 可选：渲染
timing = true                      # 数字时序图（帧 span 编号）
analog = false                     # 模拟波形图（模拟源）
t_min = 0.0
t_max = 0.002
max_frames = 60
dpi = 150

# ---- 可选：管线（ADR-020）——tap 上游协议锁的输出，独立报告/导出/渲染 ----
[runs.main.pipelines.frames]       # 报告键 = 源|管线名
tap = "uart"                       # 上游锁（协议名 / 源|协议；唯一锁可省）
chain = [                          # 节点链：{ type, 参数直接平铺 }（推荐写法）
    { type = "event_filter", kinds = ["uart.frame"] },
    # { type = "field_split", spec_name = "my-app" },   # 可继续串节点
]
# 等价的嵌套写法也接受：{ type = "event_filter", params = { kinds = ["uart.frame"] } }
```

# ---- 可选：产物命名与路径模板（ADR-024）——缺省值 = 原行为 ----
[runs.explore.naming]
decoded = "{run}-{label}-result.json"   # 机器汇总（diff 输入；占位符 label/run）
events = "exp-{source}-{protocol}.{ext}" # 事件导出（source/protocol/ext/label/run）
timing = "pic_{source}_{protocol}.png"   # 时序图（source/protocol/n/label/run）
analog = "wave_{channel}.png"            # 模拟图（source/channel/n/label/run）
index = "INDEX.md"                       # 运行索引
summary = "sum.json"                     # 运行汇总（CI）
# 运行级路径：out_dir 覆盖 [project] 的产物根；set_dir 是采集集子目录模板
# set_dir = "sets/{label}"

规则：

- **严格校验**：任何未知字段（拼写错误）都报错并列出可用字段；
  `decodehub validate` 还会对照格式/协议白名单复核档案。
- **每个解码的全部参数皆可配（ADR-021）**：`params` 表里写协议的任意节点
  参数（模拟源上的数字协议还包括切片器 threshold/hysteresis），可配集合
  派生自节点 PARAMS、与校验同源；未知参数报错并列出可配项。查看参数：
  `decodehub params`（全部）或 `decodehub params uart`（单个）。管线链节点
  参数同理——节点 PARAMS 声明的键皆可平铺进 `{ type = …, … }`。
- **批量语义**：glob 命中 N>1 的别名是主变量（按文件名字典序）；
  命中 1 个的别名广播；多个主变量数量不一致 → 明确报错。
- 每个采集集产物：`out_dir/<run>/<label>/` 下 `decoded.json`（机器可读全量
  事件，恒产生）+ `events.*` 导出 + 渲染图；批量 label 为 `001_文件名`。

## 命令

```bash
decodehub params [协议名]           # 列出解码协议的全部可配参数（与校验同源）
decodehub validate [配置路径]       # 校验配置/档案/采集绑定，不解码（CI 首道防线）
decodehub run [配置路径]            # 解码全部采集集 → index.md + summary.json
decodehub run --run main --capture la=path/x.kvdat --out other_reports --fail-fast
decodehub run --incremental        # 增量：只重算输入/配置有变化的锁与管线（ADR-025）
decodehub diff A/decoded.json B/decoded.json --out diff.md
```

## 增量运行（ADR-025）

`--incremental` 时每个 sink（锁/管线报告）按**依赖闭包指纹**判定新鲜度：
源文件内容哈希（锚依赖自动传递——只更新上行数据，下行也会重算）+ 锁参数/
管线链定义 + 命名与导出渲染配置 + decodehub 版本。未变的 sink 跳过、旧产物
原地沿用；指纹与产物清单记录在采集集目录的 `manifest.json`（每次 run 都写，
全量 run 也留档）。部分数据更新时：重算 N / 跳过 M 会打印在终端、写入
index 的"增量"列与 summary.json。注意：命名模板或配置一变即全部失效
（产物形态变了）。

- 配置路径缺省 `./decodehub.toml`；退出码 `0` 成功 / `1` 语义失败（校验不通过、
  有采集集失败、diff 有差异）/ `2` 用法错误。
- 采集集级失败**不中断**批处理（`--fail-fast` 除外），逐条记录进 index/summary。

## CI 示例（GitHub Actions）

```yaml
- name: 解码回归
  run: |
    decodehub validate
    decodehub run
    decodehub diff reports/main/001_baseline/decoded.json reports/main/001_latest/decoded.json
```

## diff 语义

两次运行的事件流**按报告键逐位对齐、忽略时间戳**（绝对时刻在两次采集中必然
不同；签名 = 除 t_start/t_end 外的全部事件字段）。报告含每类型计数对照表与
首批分歧点的两侧上下文。错位即差异——确定性解码下不做对齐猜测。

## 与 MCP 入口的关系

两者共享同一应用层（`app/services.py`）：`decodehub run` 的每一步等价于
`open_project → run_decode → export_events / render_timing` 工具调用。
交互探索用 MCP（LLM 帮你配参数），定型后写进 toml 用 CLI 批量复跑——
先 `save_profile` 固化档案，再在 toml 里引用它。管线（`bind_pipeline`
工具与 `[runs.*.pipelines]` 同语义，ADR-020）让"总线解码 → 上层解析 →
各自导出"成为一张图上的多份独立产物。
