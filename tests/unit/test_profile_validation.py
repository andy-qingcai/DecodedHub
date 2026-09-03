"""工程档案字段级校验 + tool_version（ADR-014）。"""

from __future__ import annotations

import json

import pytest
from conftest import DATA  # noqa: F401

from decodehub import __version__
from decodehub.app.profile import (
    ConfigError,
    DecodehubError,
    ProfileSpec,
    load_profile,
    save_profile,
    validate_profile_dict,
)


def _valid() -> dict:
    return {
        "name": "gizmo-v3",
        "sources": [{"alias": "la", "format": "kingst_csv"}],
        "locks": [{"source": "la", "protocol": "uart", "params": {"baud": 115200}}],
    }


class TestValidateProfileDict:
    def test_valid_passes(self):
        assert validate_profile_dict(_valid()) == []

    def test_valid_with_whitelists(self):
        problems = validate_profile_dict(
            _valid(), known_formats={"kingst_csv"}, known_protocols={"uart"})
        assert problems == []

    def test_unknown_top_key(self):
        problems = validate_profile_dict({**_valid(), "source": []})
        assert any("未知字段 'source'" in p and "拼写" in p for p in problems)

    def test_missing_name(self):
        d = _valid()
        del d["name"]
        assert any('"name"' in p for p in validate_profile_dict(d))

    def test_source_missing_alias(self):
        d = _valid()
        d["sources"] = [{"format": "kingst_csv"}]
        assert any('sources[0]: 缺少非空 "alias"' in p for p in validate_profile_dict(d))

    def test_duplicate_alias(self):
        d = _valid()
        d["sources"].append({"alias": "la"})
        assert any("重复" in p for p in validate_profile_dict(d))

    def test_lock_reference_unknown_source(self):
        d = _valid()
        d["locks"][0]["source"] = "nope"
        assert any("未在 sources 中定义" in p for p in validate_profile_dict(d))

    def test_params_must_be_object(self):
        d = _valid()
        d["locks"][0]["params"] = [1, 2]
        assert any('"params" 必须是对象' in p for p in validate_profile_dict(d))

    def test_unknown_format_and_protocol(self):
        problems = validate_profile_dict(_valid(),
                                         known_formats={"kingst_csv"},
                                         known_protocols={"spi"})
        assert any("未知协议 'uart'" in p for p in problems)

    def test_non_dict_top(self):
        assert validate_profile_dict([1, 2]) == ["档案顶层必须是 JSON 对象"]


class TestToolVersion:
    def test_save_writes_tool_version(self, tmp_path):
        spec = ProfileSpec(name="tv", sources=[], locks=[])
        path = save_profile(spec, dir=tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tool_version"] == __version__

    def test_roundtrip_keeps_tool_version(self, tmp_path):
        spec = ProfileSpec(name="tv2", tool_version="9.9.9")
        save_profile(spec, dir=tmp_path)
        loaded = load_profile("tv2", dir=tmp_path)
        assert loaded.tool_version == "9.9.9"


class TestLoadProfile:
    def test_happy_roundtrip(self, tmp_path):
        from decodehub.app.profile import LockSpec, SourceSpec
        spec = ProfileSpec(
            name="round",
            sources=[SourceSpec(alias="la", format="kingst_csv")],
            locks=[LockSpec(source="la", protocol="uart", params={"baud": 115200})],
        )
        save_profile(spec, dir=tmp_path)
        loaded = load_profile("round", dir=tmp_path)
        assert loaded.name == "round"
        assert loaded.sources[0].alias == "la"
        assert loaded.locks[0].params == {"baud": 115200}

    def test_missing_file_lists_available(self, tmp_path):
        save_profile(ProfileSpec(name="other"), dir=tmp_path)
        with pytest.raises(ConfigError, match="可用.*other"):
            load_profile("nope", dir=tmp_path)

    def test_corrupt_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="不是合法 JSON"):
            load_profile("bad", dir=tmp_path)

    def test_hand_written_unknown_field(self, tmp_path):
        d = _valid()
        d["source"] = []  # 拼写错误（应为 sources）
        (tmp_path / "typo.json").write_text(json.dumps(d), encoding="utf-8")
        with pytest.raises(ConfigError, match="未知字段 'source'"):
            load_profile("typo", dir=tmp_path)

    def test_forward_version_rejected(self, tmp_path):
        d = {**_valid(), "version": 99}
        (tmp_path / "future.json").write_text(json.dumps(d), encoding="utf-8")
        with pytest.raises(ConfigError, match="高于当前支持"):
            load_profile("future", dir=tmp_path)

    def test_config_error_is_decodehub_error(self):
        """历史调用方按 DecodehubError 捕获不受影响。"""
        assert issubclass(ConfigError, DecodehubError)
