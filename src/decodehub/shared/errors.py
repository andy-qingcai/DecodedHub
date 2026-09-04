"""领域错误体系（C1 信号内核的一部分，全平台共享）。

分层约定（见 docs/30-architecture.md 错误模型表）：
- 摄取/格式:  UnknownFormatError / PlannedFormatError / IngestError
- 图:         GraphValidationError / NodeError
- 协议锁定:    ProtocolLockError
- 字段规格:    FieldSpecError
- MCP 门禁:    StageGateError
- 项目配置:    ConfigError（decodehub.toml / 档案 JSON 的解析与校验）
解码数据错误（坏帧等）不是异常——是事件上的 errors 字段（ADR-004）。
"""

from __future__ import annotations


class DecodehubError(Exception):
    """平台领域错误基类。"""


class UnknownFormatError(DecodehubError):
    """嗅探无法识别格式。携带尝试过的规则，供用户诊断。"""

    def __init__(self, path: str, tried: list[str]):
        self.path = path
        self.tried = tried
        super().__init__(
            f"无法识别采集文件格式: {path}\n已尝试的嗅探规则: {', '.join(tried) or '(无)'}\n"
            f"可用 format 键见 list_capabilities；也可显式传 format 参数。"
        )


class PlannedFormatError(DecodehubError):
    """嗅探识别出格式，但该格式在 v1 明确延后（ADR-007）。"""

    def __init__(self, path: str, format_key: str, note: str = ""):
        self.path = path
        self.format_key = format_key
        super().__init__(
            f"文件 {path} 是 {format_key} 格式，但该格式在当前版本明确延后。{note}"
        )


class IngestError(DecodehubError):
    """适配器读取/解析失败。"""


class GraphValidationError(DecodehubError):
    """图构建期校验失败（五规则之一）。"""

    def __init__(self, rule: str, detail: str):
        self.rule = rule
        self.detail = detail
        super().__init__(f"图校验失败 [{rule}]: {detail}")


class NodeError(DecodehubError):
    """节点运行期失败（引擎错误，非数据错误）。"""

    def __init__(self, node_id: str, cause: str, inputs_summary: str = ""):
        self.node_id = node_id
        super().__init__(f"节点 {node_id} 执行失败: {cause}" + (f"（输入: {inputs_summary}）" if inputs_summary else ""))


class ProtocolLockError(DecodehubError):
    """lock_protocol 校验失败（通道缺失、参数非法等）。"""


class FieldSpecError(DecodehubError):
    """payload 字段规格编译/查找失败（规格是声明，写错在编译期报）。"""


class StageGateError(DecodehubError):
    """工具在当前阶段不可用（MCP 门禁）。"""

    def __init__(self, tool: str, needed: str, current: str, hint: str = ""):
        self.tool = tool
        self.needed = needed
        self.current = current
        super().__init__(
            f"工具 {tool} 需要 {needed} 阶段，当前处于 {current} 阶段。{hint}".strip()
        )


class ConfigError(DecodehubError):
    """项目配置（decodehub.toml / 工程档案 JSON）解析或校验失败（ADR-015）。"""
