"""嗅探规则 + 非真实样本适配器单测（mcu_adc / saleae / generic / mho98 头）。"""

import numpy as np
import pytest

from decodehub.acquisition import load_capture
from decodehub.acquisition.sniff import sniff
from decodehub.shared import PlannedFormatError, UnknownFormatError


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestSniff:
    def test_kingst_kvdat(self, data_dir):
        assert sniff(data_dir / "kingst_100k.kvdat") == "kingst_kvdat"

    def test_kingst_csv(self, data_dir):
        assert sniff(data_dir / "kingst_probe.csv") == "kingst_csv"

    def test_mho98_csv(self, data_dir):
        assert sniff(data_dir / "mho98_ch1_norm.csv") == "mho98_csv"

    def test_kingst_bin(self, data_dir):
        assert sniff(data_dir / "kingst_100k.bin") == "mcu_adc_bin"  # 裸 u16 → 规则 6 兜底

    def test_mcu_adc_csv_with_header(self, tmp_path):
        p = _write(tmp_path, "a.csv", "time_ms,adc_raw\n0,10\n1,20\n2,30\n")
        assert sniff(p) == "mcu_adc_csv"

    def test_mcu_adc_csv_bare(self, tmp_path):
        p = _write(tmp_path, "b.csv", "1023\n1024\n1000\n")
        assert sniff(p) == "mcu_adc_csv"

    def test_saleae_csv(self, tmp_path):
        p = _write(tmp_path, "c.csv", "Time [s],Channel 0,Channel 1\n0,1,1\n0.001,0,1\n")
        assert sniff(p) == "saleae_csv"

    def test_generic_csv(self, tmp_path):
        p = _write(tmp_path, "d.csv", "x,CH1\n0.0,0.1\n0.001,0.2\n")
        assert sniff(p) == "generic_csv"

    def test_sal_planned(self, tmp_path):
        import zipfile

        p = tmp_path / "e.sal"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("meta.json", "{}")
        with pytest.raises(PlannedFormatError):
            sniff(p)

    def test_unknown(self, tmp_path):
        p = tmp_path / "f.xyz"
        p.write_bytes(b"\x01\x02\x03\x04\x05\x06\x07")  # 奇数大小、非文本
        with pytest.raises(UnknownFormatError) as ei:
            sniff(p)
        assert "嗅探规则" in str(ei.value)


class TestMcuAdcAdapters:
    def test_csv_two_col_ms(self, tmp_path):
        p = _write(tmp_path, "a.csv", "time_ms,adc_raw\n0,0\n10,2048\n20,4095\n")
        cap = load_capture(p, options={"vref": 3.3, "bits": 12})
        ch = cap.analog[0]
        assert ch.n == 3
        assert ch.dt == pytest.approx(0.01)
        assert ch.units == "V"
        assert ch.samples[-1] == pytest.approx(3.3 * 4095 / 4096, rel=1e-4)
        assert ch.raw_scale == pytest.approx(3.3 / 4096)

    def test_csv_single_col_needs_rate(self, tmp_path):
        p = _write(tmp_path, "b.csv", "value\n100\n200\n")
        with pytest.raises(Exception):
            load_capture(p)
        cap = load_capture(p, options={"sample_rate": 1000})
        assert cap.analog[0].dt == pytest.approx(1e-3)

    def test_bin(self, tmp_path):
        p = tmp_path / "c.bin"
        np.array([100, 200, 300], dtype="<u2").tofile(p)
        cap = load_capture(p, options={"sample_rate": 500, "vref": 1.8, "bits": 10})
        ch = cap.analog[0]
        assert ch.n == 3 and ch.dt == pytest.approx(1 / 500)
        assert ch.samples[1] == pytest.approx(200 * 1.8 / 1024, rel=1e-4)


class TestSaleaeAndGeneric:
    def test_saleae_digital_roundtrip(self, tmp_path):
        p = _write(tmp_path, "s.csv", "Time [s],Channel 0,Channel 1\n0,1,1\n0.01,0,1\n0.02,0,0\n")
        cap = load_capture(p)
        w = cap.digital
        assert w.channels == ("Channel 0", "Channel 1")
        assert w.initial == 0b11
        assert [int(x) for x in w.edges_levels] == [0b10, 0b00]
        assert w.t_end == pytest.approx(0.02)

    def test_generic_two_voltage_columns(self, tmp_path):
        p = _write(tmp_path, "g.csv", "x,CH1,CH2\n0,0.0,1.0\n0.1,1.0,0.5\n")
        cap = load_capture(p)
        assert [c.name for c in cap.analog] == ["CH1", "CH2"]
        assert cap.analog[1].samples[1] == pytest.approx(0.5, abs=1e-6)


class TestMho98Csv:
    def test_preamble_and_data(self, data_dir):
        cap = load_capture(data_dir / "mho98_ch1_norm.csv")
        ch = cap.analog[0]
        assert ch.n == 1000
        assert ch.dt == pytest.approx(1e-5)
        assert ch.t0 < 0  # 触发居中，负时间
        assert cap.meta.sample_rate == pytest.approx(1e5)
        assert -4.0 < float(ch.samples.min()) and float(ch.samples.max()) < 4.0
