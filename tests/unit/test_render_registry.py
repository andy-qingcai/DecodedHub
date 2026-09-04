"""渲染侧注册表一致性（ADR-019）：导出格式与渲染路由的单一登记点。

导出格式的四份历史副本（services if/elif、config 元组、runner 硬编码列表、
tools enum）已收敛为 render/format.py 的注册表；渲染路由从 render_timing 的
if/elif 收敛为 render/routes.py 的 graph_kind 查表。这里的守卫保证：
清单漂移、路由缺口（新绑定先行而路由未跟）在单元测试期暴露。
"""

from types import SimpleNamespace

from decodehub.app import config
from decodehub.decode.bindings import all_bindings
from decodehub.render.format import (
    EXPORT_FORMAT_KEYS,
    EXPORT_FORMAT_SPECS,
    export_report,
)
from decodehub.render.routes import ROUTES, render_route
from decodehub.shared import DecodehubError


class TestExportFormatRegistry:
    def test_v1_formats_registered(self):
        assert list(EXPORT_FORMAT_SPECS) == ["csv", "json", "md"]
        for spec in EXPORT_FORMAT_SPECS.values():
            assert spec.ext == spec.key  # v1 约定：键即扩展名
            assert spec.description
            assert callable(spec.exporter)

    def test_config_formats_derived(self):
        """config 的合法导出格式 = 注册表（曾四处手抄，防回退）。"""
        assert config.EXPORT_FORMATS == EXPORT_FORMAT_KEYS == ("csv", "json", "md")

    def test_tool_enum_derived(self):
        from decodehub.mcp_server.tools import TOOLS

        schema = next(t for t in TOOLS if t.name == "export_events").schema
        assert schema["properties"]["format"]["enum"] == list(EXPORT_FORMAT_KEYS)

    def test_unknown_format_lists_available(self):
        report = _empty_report()
        try:
            export_report("yaml", report)
        except DecodehubError as e:
            assert "csv" in str(e) and "yaml" in str(e)
        else:
            raise AssertionError("未知格式应报错")

    def test_each_format_produces_text(self):
        report = _empty_report()
        for key, spec in EXPORT_FORMAT_SPECS.items():
            text = spec.exporter(report)
            assert isinstance(text, str) and text, key


def _empty_report():
    from decodehub.decode.events import DecodeReport

    return DecodeReport(protocol="uart", params={}, events=[])


class TestRenderRoutes:
    def test_bindings_graph_kinds_all_routed(self):
        """路由缺口防线：任何绑定在任何源模态下给出的 graph_kind 必有路由。"""
        digital_cap, analog_cap = SimpleNamespace(digital=object()), SimpleNamespace(digital=None)
        kinds = set()
        for b in all_bindings():
            kinds.add(b.graph_kind_for(digital_cap))
            kinds.add(b.graph_kind_for(analog_cap))
        assert kinds <= set(ROUTES), f"未登记路由的图形状: {kinds - set(ROUTES)}"

    def test_v1_kinds_present(self):
        assert {"digital", "sliced", "analog_direct", "fan_in"} <= set(ROUTES)

    def test_route_semantics(self):
        assert ROUTES["sliced"].needs_slice           # 数字波形须从图求值物化
        assert not ROUTES["digital"].needs_slice
        # 模拟直达与锚点扇入共用"波形 + 事件 span"策略
        assert ROUTES["analog_direct"].plot is ROUTES["fan_in"].plot
        assert ROUTES["digital"].plot is ROUTES["sliced"].plot

    def test_unknown_graph_kind_reports_available(self):
        try:
            render_route("constellation")
        except DecodehubError as e:
            assert "analog_direct" in str(e)
        else:
            raise AssertionError("未知图形状应报错")
