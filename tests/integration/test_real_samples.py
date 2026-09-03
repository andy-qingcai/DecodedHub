"""真实样本集成测试（本机采集导出；docs/60-testing.md）。

关键一致性：kvdat 解出的位流必须与 bin 逐位一致（两文件同源采集）。
"""

import numpy as np
import pytest

from decodehub.acquisition import load_capture


class TestKingstReal:
    def test_csv_two_channels(self, data_dir):
        cap = load_capture(data_dir / "kingst_probe.csv")
        w = cap.digital
        assert w.channels[:2] == ("通道 0", "通道 1")
        assert w.initial in (0, 1, 2, 3)
        assert w.n_edges > 100
        assert w.t_end > 0

    def test_csv_sixteen_channels(self, data_dir):
        cap = load_capture(data_dir / "kingst_probe_all.csv")
        assert len(cap.digital.channels) == 16
        # 每个通道都能独立取出跳变流
        for name in cap.digital.channels[:4]:
            t, lv = cap.digital.edge_stream(name)
            assert t.size >= 0

    def test_bin_requires_rate(self, data_dir):
        with pytest.raises(Exception, match="sample_rate"):
            load_capture(data_dir / "kingst_100k.bin")

    def test_bin(self, data_dir):
        # 裸 u16 无魔数：嗅探会兜底成 mcu_adc_bin，kingst_bin 需显式指定
        cap = load_capture(data_dir / "kingst_100k.bin",
                           format_key="kingst_bin", options={"sample_rate": 200e6})
        w = cap.digital
        assert w.n_samples == 100_000
        assert w.t_end == pytest.approx(100_000 / 200e6)
        assert len(w.channels) == 16

    def test_kvdat(self, data_dir):
        cap = load_capture(data_dir / "kingst_100k.kvdat")
        w = cap.digital
        assert cap.meta.sample_rate == pytest.approx(200e6)
        assert w.n_samples == 100_000
        assert len(w.channels) == 16

    def test_kvdat_matches_bin_bitstream(self, data_dir):
        """kvdat 与 bin 同源采集：位流必须逐位一致。"""
        kv = load_capture(data_dir / "kingst_100k.kvdat").digital
        bn = load_capture(data_dir / "kingst_100k.bin",
                          format_key="kingst_bin",
                          options={"sample_rate": 200e6}).digital
        fs = 200e6
        # 用跳变流重建每个通道的逐采样位流并比对
        for name in kv.channels:
            a = _materialize(kv, name, fs)
            b = _materialize(bn, name, fs)
            assert a == b, f"通道 {name} 位流不一致"


def _materialize(w, name, fs):
    n = w.n_samples or int(w.t_end * fs)
    arr = np.zeros(n, dtype=np.uint8)
    b = w.bit_index(name)
    cur = (w.initial >> b) & 1
    if cur:
        arr[:] = 1
    for t, snap in zip(w.edges_t, w.edges_levels):
        i = int(round(t * fs))
        if i >= n:
            break
        cur = (int(snap) >> b) & 1
        arr[i:] = cur
    return arr.tobytes()


class TestMho98Real:
    def test_norm_csv_full_pipeline_slicing(self, data_dir):
        """示波器 CSV → 模拟通道 → 阈值切片 → 数字波形（模拟源路径）。"""
        from decodehub.decode.nodes.slicer import SlicerNode

        cap = load_capture(data_dir / "mho98_ch1_norm.csv")
        assert cap.analog and not cap.digital
        sliced = SlicerNode().run(
            {"in": cap.analog},
            {"threshold": None, "hysteresis": None, "names": []},
        )["out"]
        assert sliced.channels == (cap.analog[0].name,)
        assert sliced.n_edges >= 0  # 模拟化信号切片后至少结构合法
