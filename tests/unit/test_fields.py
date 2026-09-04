"""字段规格引擎测试（六原语：序列 / 动态长度 / 类别分发 / 重复 / 计算 / 校验）。

规格是纯 dict（可经 JSON/MCP 下发）；编译期报 FieldSpecError，解析期数据错误
是字段上的 errors 列表（ADR-004 同款哲学，不打断其余字段）。
"""

import pytest

from decodehub.decode.fields import (
    FieldSpecError,
    compile_spec,
    parse_payload,
    register_field_fn,
    register_fields,
    get_fields,
)


# ---------------------------------------------------------------- 序列与字节序 ---

def test_fixed_seq_big_endian_default():
    spec = {"seq": [{"id": "a", "type": "u8"}, {"id": "b", "type": "u16"}]}
    fs = parse_payload(spec, b"\x01\x12\x34")
    assert [f.id for f in fs] == ["a", "b"]
    assert fs[0].value == 1 and fs[0].kind == "uint"
    assert fs[0].offset_bits == 0 and fs[0].width_bits == 8
    assert fs[1].value == 0x1234
    assert fs[1].offset_bits == 8 and fs[1].width_bits == 16


def test_endian_level_and_field_override():
    spec = {"endian": "le", "seq": [
        {"id": "a", "type": "u16"},
        {"id": "b", "type": "u16", "endian": "be"},
    ]}
    fs = parse_payload(spec, b"\x34\x12\x12\x34")
    assert fs[0].value == 0x1234      # 顶层 le
    assert fs[1].value == 0x1234      # 字段级覆盖回 be


def test_signed_and_float():
    spec = {"seq": [{"id": "t", "type": "s8"}, {"id": "v", "type": "f32"}]}
    import struct
    fs = parse_payload(spec, b"\xff" + struct.pack(">f", 1.5))
    assert fs[0].value == -1 and fs[0].kind == "int"
    assert fs[1].value == pytest.approx(1.5) and fs[1].kind == "float"


# ---------------------------------------------------------------- 动态长度 ---

DYN = {"seq": [
    {"id": "n", "type": "u8"},
    {"id": "body", "type": "bytes", "size": "n"},
]}


def test_dynamic_size_expr():
    fs = parse_payload(DYN, b"\x03abc")
    assert fs[1].value == b"abc"
    assert fs[1].offset_bits == 8 and fs[1].width_bits == 24


def test_size_expr_arithmetic():
    spec = {"seq": [
        {"id": "n", "type": "u8"},
        {"id": "body", "type": "bytes", "size": "n * 2 + 1"},
    ]}
    fs = parse_payload(spec, b"\x01" + b"\x00" * 3)
    assert fs[1].value == b"\x00\x00\x00"


def test_truncated_marks_error_and_stops():
    fs = parse_payload(DYN, b"\x05ab")
    body = fs[1]
    assert body.value is None
    assert "truncated" in body.errors
    assert len(fs) == 2  # body 之后的字段不存在（本规格恰好只有两个）


def test_size_eos_reads_to_end():
    spec = {"seq": [{"id": "head", "type": "u8"},
                    {"id": "rest", "type": "bytes", "size_eos": True}]}
    fs = parse_payload(spec, b"\x01\x02\x03\x04")
    assert fs[1].value == b"\x02\x03\x04"


def test_terminator_excluded_cursor_pasted():
    spec = {"seq": [{"id": "s", "type": "bytes", "terminator": 0x00},
                    {"id": "next", "type": "u8"}]}
    fs = parse_payload(spec, b"hi\x00\xff")
    assert fs[0].value == b"hi"
    assert fs[1].value == 0xFF and fs[1].offset_bits == 24


# ---------------------------------------------------------------- 类别分发 ---

SWITCH = {
    "seq": [
        {"id": "mode", "type": "u8"},
        {"id": "body", "switch_on": "mode",
         "cases": {"1": "idle", "2": "run", "*": "generic"}},
    ],
    "types": {
        "idle": {"seq": [{"id": "idle_t", "type": "u8"}]},
        "run": {"seq": [{"id": "rpm", "type": "u16"}]},
        "generic": {"seq": [{"id": "raw", "type": "bytes", "size_eos": True}]},
    },
}


def test_switch_selects_case_structure():
    fs = parse_payload(SWITCH, b"\x02\x01\xf4")
    body = fs[1]
    assert body.children[0].id == "rpm" and body.children[0].value == 0x01F4


def test_switch_default_case():
    fs = parse_payload(SWITCH, b"\x09\xde\xad")
    assert fs[1].children[0].id == "raw" and fs[1].children[0].value == b"\xde\xad"


def test_switch_without_match_and_no_default():
    spec = {"seq": [{"id": "m", "type": "u8"},
                    {"id": "b", "switch_on": "m", "cases": {"1": "idle"}}],
            "types": {"idle": {"seq": [{"id": "x", "type": "u8"}]}}}
    fs = parse_payload(spec, b"\x07\x00")
    assert "no-case" in fs[1].errors


# ---------------------------------------------------------------- 重复 ---

def test_repeat_count_expr():
    spec = {"seq": [
        {"id": "n", "type": "u8"},
        {"id": "item", "type": "u16", "repeat": "expr", "repeat_expr": "n"},
    ]}
    fs = parse_payload(spec, b"\x02\x00\x01\x00\x02")
    assert [f.value for f in fs[1].children] == [1, 2]


def test_repeat_eos():
    spec = {"seq": [{"id": "tail", "type": "u8", "repeat": "eos"}]}
    fs = parse_payload(spec, b"\x0a\x0b\x0c")
    assert [f.value for f in fs[0].children] == [0x0A, 0x0B, 0x0C]


def test_repeat_until_inclusive():
    spec = {"seq": [{"id": "b", "type": "u8", "repeat": "until",
                     "until": "b == 0xFF"}]}
    fs = parse_payload(spec, b"\x01\x02\xff\x03")
    assert [f.value for f in fs[0].children] == [1, 2, 0xFF]
    assert fs[0].offset_bits == 0 and fs[0].width_bits == 24  # 消耗恰好 3 字节


# ---------------------------------------------------------------- 位域 ---

def test_bit_fields_share_cursor_then_align():
    spec = {"seq": [
        {"id": "x", "type": "b3"},
        {"id": "y", "type": "b5"},
        {"id": "w", "type": "b2"},
        {"id": "z", "type": "u8"},
    ]}
    fs = parse_payload(spec, b"\xbf\xff\xaa")  # 0xBF = 101 11111
    assert fs[0].value == 0b101 and fs[0].width_bits == 3
    assert fs[1].value == 0b11111 and fs[1].offset_bits == 3
    assert fs[2].value == 0b11 and fs[2].offset_bits == 8   # 位游标跨字节共享
    assert fs[3].value == 0xAA and fs[3].offset_bits == 16  # 10 位消耗后对齐到字节边界


# ---------------------------------------------------------------- 枚举 / 校验 / 计算 ---

def test_enum_label_resolved():
    spec = {"seq": [{"id": "m", "type": "u8",
                     "enum": {"1": "IDLE", "2": "RUN"}}]}
    fs = parse_payload(spec, b"\x02")
    assert fs[0].value == 2 and fs[0].enum_label == "RUN"
    fs2 = parse_payload(spec, b"\x09")
    assert fs2[0].enum_label is None


def test_valid_eq_pass_and_fail():
    spec = {"seq": [{"id": "chk", "type": "u16", "valid": {"eq": "0xA55A"}}]}
    assert parse_payload(spec, b"\xa5\x5a")[0].errors == []
    assert "valid" in parse_payload(spec, b"\x00\x01")[0].errors


def test_contents_mismatch_is_error():
    spec = {"seq": [{"id": "magic", "type": "bytes", "contents": "A55A"}]}
    assert parse_payload(spec, b"\xa5\x5a")[0].errors == []
    assert "valid" in parse_payload(spec, b"\x00\x00")[0].errors


def test_value_field_computes_without_consuming():
    spec = {"seq": [
        {"id": "n", "type": "u8"},
        {"id": "dbl", "value": "n * 2"},
        {"id": "after", "type": "u8"},
    ]}
    fs = parse_payload(spec, b"\x03\x10")
    assert fs[1].value == 6 and fs[1].width_bits == 0
    assert fs[2].value == 0x10  # 计算字段未消耗字节


def test_conditional_field():
    spec = {"seq": [
        {"id": "mode", "type": "u8"},
        {"id": "ext", "type": "u8", "if": "mode & 0x01"},
    ]}
    assert [f.id for f in parse_payload(spec, b"\x01\x99")] == ["mode", "ext"]
    assert [f.id for f in parse_payload(spec, b"\x02\x99")] == ["mode"]


def test_root_and_parent_refs():
    spec = {
        "seq": [
            {"id": "n", "type": "u8"},
            {"id": "blk", "type": "block", "repeat": "expr", "repeat_expr": "n"},
        ],
        "types": {
            "block": {"seq": [
                {"id": "ln", "type": "u8"},
                {"id": "dat", "type": "bytes", "size": "ln"},
                {"id": "sum", "value": "len(dat) + root.n"},  # 演示跨层引用
            ]},
        },
    }
    fs = parse_payload(spec, b"\x01\x02\xab\xcd")
    blk = fs[1].children[0]
    assert blk.children[1].value == b"\xab\xcd"
    assert blk.children[2].value == 3  # len(dat)=2 + root.n=1


# ---------------------------------------------------------------- 编译期错误 ---

def test_unknown_type_rejected_at_compile():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "x", "type": "u12"}]})


def test_duplicate_id_rejected():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "a", "type": "u8"},
                              {"id": "a", "type": "u8"}]})


def test_unknown_name_in_expr_rejected_at_compile():
    # 编译期可静态检测的：size 表达式引用了序列里不存在的 id
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "b", "type": "bytes", "size": "nope"}]})


def test_non_whitelisted_expr_rejected():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "b", "type": "bytes", "size": "f(1)"}]})
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "b", "type": "bytes", "size": "__import__('os')"}]})


def test_switch_needs_cases():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "b", "switch_on": "m"}]})


def test_bytes_needs_size():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "b", "type": "bytes"}]})


# ---------------------------------------------------------------- 命名规格注册表 ---

def test_register_and_get_named_spec():
    spec = {"seq": [{"id": "a", "type": "u8"}]}
    register_fields("test.only", spec)
    compiled = get_fields("test.only")
    assert parse_payload(compiled, b"\x07")[0].value == 7
    with pytest.raises(FieldSpecError):
        register_fields("test.only", spec)
    with pytest.raises(FieldSpecError):
        get_fields("test.nope")


def test_true_division_for_scaling():
    """物理量缩放（/10.0）需要真除法——FloorDiv 会吞掉小数。"""
    spec = {"seq": [{"id": "t", "type": "s16"},
                    {"id": "c", "value": "t / 10.0"}]}
    fs = parse_payload(spec, b"\x00\xeb")
    assert fs[1].value == 23.5


def test_valid_on_bit_field():
    """位域也是标量，valid 断言应同样生效。"""
    spec = {"seq": [{"id": "ver", "type": "b4", "valid": {"eq": "2"}},
                    {"id": "rsv", "type": "b4"}]}
    assert parse_payload(spec, b"\x20")[0].errors == []
    assert "valid" in parse_payload(spec, b"\x30")[0].errors


# ---------------------------------------------- CRC 校验目录与具名钩子 ---

def test_crc16_modbus_preset_and_mismatch():
    """标准校验值：CRC-16/MODBUS('123456789') = 0x4B37，小端存放。"""
    import struct
    spec = {"seq": [
        {"id": "data", "type": "bytes", "size": 9},
        {"id": "crc", "type": "u16", "endian": "le", "crc": {"algo": "crc16_modbus"}},
    ]}
    fs = parse_payload(spec, b"123456789" + struct.pack("<H", 0x4B37))
    assert fs[0].value == b"123456789"
    assert fs[1].errors == [] and fs[1].value == 0x4B37
    # 数据任一比特被篡改 → crc 错误（over=prefix 不含 crc 字段自身）
    bad = parse_payload(spec, b"223456789" + struct.pack("<H", 0x4B37))
    assert "crc" in bad[1].errors


def test_crc_parametric_inline_model():
    """非标模型直接给参数：width/poly/init/refin/refout/xorout（Rocksoft）。"""
    spec = {"seq": [
        {"id": "d", "type": "bytes", "size": 9},
        {"id": "c", "type": "u8", "crc": {"width": 8, "poly": 0x07, "init": 0, "xorout": 0}},
    ]}
    fs = parse_payload(spec, b"123456789" + b"\xf4")   # CRC-8 标准校验值 0xF4
    assert fs[1].errors == [] and fs[1].value == 0xF4


def test_crc_sum8_simple():
    spec = {"seq": [{"id": "a", "type": "u8"}, {"id": "b", "type": "u8"},
                    {"id": "s", "type": "u8", "crc": {"algo": "sum8"}}]}
    fs = parse_payload(spec, b"\x01\x02\x03")
    assert fs[2].errors == [] and fs[2].value == 3
    assert "crc" in parse_payload(spec, b"\x01\x02\x04")[2].errors


def test_struct_scope_crc_only_covers_own_bytes():
    """over=prefix 的"前缀"= 本层结构起点 → 嵌套结构的 CRC 不含外层帧头。"""
    spec = {"seq": [{"id": "hdr", "type": "u16"}, {"id": "blk", "type": "blk"}],
            "types": {"blk": {"seq": [
                {"id": "n", "type": "u8"},
                {"id": "body", "type": "bytes", "size": "n"},
                {"id": "s", "type": "u8", "crc": {"algo": "sum8"}},
            ]}}}
    payload = b"\xaa\xbb" + b"\x02\xab\xcd" + bytes([0x7A])  # sum(02 AB CD)=0x17A&0xFF
    fs = parse_payload(spec, payload)
    assert fs[1].children[2].errors == []


def test_crc_compile_rejects_bad_models():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "x", "type": "u8", "crc": {"algo": "crc999"}}]})
    with pytest.raises(FieldSpecError):  # 算法宽度与字段位宽不符
        compile_spec({"seq": [{"id": "x", "type": "u16", "crc": {"algo": "crc8"}}]})
    with pytest.raises(FieldSpecError):  # 位域不支持 crc
        compile_spec({"seq": [{"id": "x", "type": "b4", "crc": {"algo": "sum8"}}]})


def test_process_named_hook():
    """逃生舱：变换代码注册在受信侧，规格只携带名字。"""
    register_field_fn("test.reverse", lambda b: b[::-1])
    spec = {"seq": [{"id": "d", "type": "str", "size": 3, "process": "test.reverse"}]}
    fs = parse_payload(spec, b"abc")
    assert fs[0].value == "cba"


def test_process_unknown_name_rejected_at_compile():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "d", "type": "bytes", "size": 1, "process": "nope"}]})


# ---------------------------------------------- 呈现提示（给人看的输出） ---

def test_render_display_hints():
    """display=bcd/dec、scale+unit、枚举优先、可打印 bytes 附 ASCII。"""
    from decodehub.decode.fields import format_field
    spec = {"seq": [
        {"id": "ver", "type": "u16", "display": "bcd"},
        {"id": "n", "type": "u8", "display": "dec"},
        {"id": "p", "type": "u8", "scale": 2, "unit": "mA"},
        {"id": "m", "type": "bytes", "contents": "5453"},
        {"id": "s", "type": "str", "size": 3},
        {"id": "t", "type": "u8", "enum": {"3": "ALARM"}},
        {"id": "raw", "type": "bytes", "size": 2},
    ]}
    payload = bytes.fromhex("0210") + b"\x12\x32" + b"TS" + b"abc" + b"\x03" + b"\x00\xff"
    fs = parse_payload(spec, payload)
    out = [format_field(f) for f in fs]
    assert out == ["ver=2.10", "n=18", "p=100 mA", "m=5453 'TS'",
                   "s=abc", "t=ALARM", "raw=00ff"]
    assert fs[2].scale == 2 and fs[2].unit == "mA"   # 提示随字段树进 JSON


def test_detail_multiline_tree():
    """Markdown 内容列：换行 + 缩进的树，而不是一行流。"""
    from decodehub.decode.fields import format_detail, FieldSetEvent, FieldView
    ev = FieldSetEvent(kind="fields.split", t_start=0, t_end=1, label="",
                       fields=[FieldView(id="cmd", offset_bits=0, width_bits=8,
                                         kind="uint", value=3, enum_label="ALARM"),
                               FieldView(id="body", offset_bits=8, width_bits=16,
                                         kind="struct",
                                         children=[FieldView(id="n", offset_bits=8,
                                                             width_bits=8, kind="uint",
                                                             value=2, display="dec")])])
    text = format_detail(ev)
    lines = text.split("<br>")
    assert lines[0] == "cmd=ALARM"
    assert lines[1] == "body (1 项)"                      # 顶层 struct 展开为组标题
    assert lines[2].startswith("\u00a0") and "└" in lines[2] and "n=2" in lines[2]


def test_fmt_struct_inline_not_question_mark():
    """struct 的 value 恒为 None——格式化必须走 children，不得显示 ?。"""
    from decodehub.decode.fields import format_field, FieldView
    f = FieldView(id="body", offset_bits=0, width_bits=8, kind="struct",
                  children=[FieldView(id="n", offset_bits=0, width_bits=8,
                                      kind="uint", value=2, display="dec")])
    assert format_field(f) == "body={n=2}"


def test_display_hint_validation():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "x", "type": "u8", "display": "roman"}]})


def test_crc_named_fn_hook():
    """自定义校验算法：代码注册在受信侧，规格只带名字。"""
    from decodehub.decode.fields import register_check_fn
    register_check_fn("test.x5a", lambda b: (sum(b) ^ 0x5A) & 0xFF)
    spec = {"seq": [{"id": "d", "type": "bytes", "size": 2},
                    {"id": "c", "type": "u8", "crc": {"fn": "test.x5a"}}]}
    good = b"\x01\x02" + bytes([0x03 ^ 0x5A])
    assert parse_payload(spec, good)[1].errors == []
    bad = parse_payload(spec, b"\x01\x03" + bytes([0x03 ^ 0x5A]))
    assert "crc" in bad[1].errors


def test_crc_unknown_fn_rejected_at_compile():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "x", "type": "u8", "crc": {"fn": "nope"}}]})


# ------------------------------------- 评审修复回归（多 agent 审核发现） ---

from contextlib import contextmanager
import signal as _signal


@contextmanager
def _no_hang(seconds=5):
    """死循环守卫：解析若挂死，alarm 到点断言失败而非卡死测试套件。"""
    def _handler(signum, frame):
        raise AssertionError("疑似死循环（alarm 超时）")
    old = _signal.signal(_signal.SIGALRM, _handler)
    _signal.alarm(seconds)
    try:
        yield
    finally:
        _signal.alarm(0)
        _signal.signal(_signal.SIGALRM, old)


def test_u24_s24_endian():
    spec = {"seq": [{"id": "a", "type": "u24"},
                    {"id": "b", "type": "s24", "endian": "le"},
                    {"id": "c", "type": "u8"}]}
    fs = parse_payload(spec, bytes.fromhex("0abbcc") + bytes.fromhex("feffff") + b"\x7f")
    assert fs[0].value == 0x0ABBCC
    assert fs[1].value == -2
    assert fs[2].value == 0x7F and fs[2].errors == []


def test_repeat_eos_zero_progress_terminates():
    """eos × switch 无匹配（游标零推进）：必须终止并报错，不许挂死。"""
    spec = {"seq": [{"id": "m", "type": "u8"},
                    {"id": "items", "type": "it", "repeat": "eos"}],
            "types": {"it": {"seq": [
                {"id": "b", "switch_on": "m", "cases": {"1": "x"}}]},
                "x": {"seq": [{"id": "z", "type": "u8"}]}}}
    with _no_hang():
        fs = parse_payload(spec, b"\x09\xff\xff\xff\xff")
    assert "incomplete" in fs[1].errors
    assert any("no-progress" in c.errors for c in fs[1].children)


def test_repeat_until_zero_progress_terminates():
    spec = {"seq": [{"id": "m", "type": "u8"},
                    {"id": "items", "type": "it", "repeat": "until",
                     "until": "m == 255"}],
            "types": {"it": {"seq": [
                {"id": "b", "switch_on": "m", "cases": {"1": "x"}}]},
                "x": {"seq": [{"id": "z", "type": "u8"}]}}}
    with _no_hang():
        fs = parse_payload(spec, b"\x09\x01\x02")
    assert any("no-progress" in c.errors for c in fs[1].children)


def test_repeat_expr_huge_count_zero_progress_terminates():
    spec = {"seq": [{"id": "many", "type": "it",
                     "repeat": "expr", "repeat_expr": "1000000"}],
            "types": {"it": {"seq": [{"id": "v", "value": "2"}]}}}
    with _no_hang():
        fs = parse_payload(spec, b"")
    assert fs[0].value is not None or fs[0].errors  # 终止即可


def test_repeat_eos_truncated_marks_incomplete():
    """eos 分支的截断必须显式标记（此前被静默吞掉，后续字段错位解析）。"""
    spec = {"seq": [{"id": "n", "type": "u8"},
                    {"id": "b", "type": "bytes", "size": "n", "repeat": "eos"},
                    {"id": "tail", "type": "u8"}]}
    fs = parse_payload(spec, b"\x05abcdXYZ")
    assert "incomplete" in fs[1].errors
    assert any("truncated" in c.errors for c in fs[1].children)
    assert len(fs) == 2  # incomplete 之后 tail 不再解析


def test_expr_type_errors_are_data_not_crash():
    spec = {"seq": [{"id": "d", "type": "bytes", "size": 1},
                    {"id": "bad", "value": "d + 1"},
                    {"id": "m", "type": "s8"},
                    {"id": "sh", "value": "255 << m"},
                    {"id": "neg", "value": "255 >> m"}]}
    fs = parse_payload(spec, b"\x41\x80")
    assert fs[1].errors and fs[1].errors[0].startswith("expr:")
    assert fs[3].errors and fs[3].errors[0].startswith("expr:")
    assert fs[4].errors and fs[4].errors[0].startswith("expr:")


def test_compare_type_mismatch_is_expr_error():
    # 字符串常量在编译期就被白名单拒绝；求值期类型错用 bytes 字段触发
    spec = {"seq": [{"id": "d", "type": "bytes", "size": 1},
                    {"id": "c", "value": "d < 3"}]}
    fs = parse_payload(spec, b"\x41")
    assert fs[1].errors and fs[1].errors[0].startswith("expr:")


def test_types_cycle_rejected_at_compile():
    spec = {"seq": [{"id": "a", "type": "ta"}],
            "types": {"ta": {"seq": [{"id": "n", "type": "tb"}]},
                      "tb": {"seq": [{"id": "b", "type": "ta"}]}}}
    with pytest.raises(FieldSpecError, match="循环"):
        compile_spec(spec)


def test_boolop_short_circuit():
    """and 左支为假时不得求值右支（除零不该发生）。"""
    spec = {"seq": [{"id": "flag", "type": "u8"},
                    {"id": "n", "type": "u8"},
                    {"id": "ok", "value": "flag == 1 and 100 / n > 1"}]}
    fs = parse_payload(spec, b"\x00\x00")  # flag=0 → and 左支假，右支不求值
    assert fs[2].value is False and fs[2].errors == []


def test_bytes_valid_rejected_at_compile():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "d", "type": "bytes", "size": 2,
                               "valid": {"eq": "1"}}]})


def test_enum_key_leading_zero_rejected():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "x", "type": "u8", "enum": {"01": "TEN"}}]})


def test_terminator_range_validated():
    with pytest.raises(FieldSpecError):
        compile_spec({"seq": [{"id": "s", "type": "bytes", "terminator": 300}]})


def test_huge_shift_capped():
    spec = {"seq": [{"id": "m", "type": "u32"}, {"id": "v", "value": "1 << m"}]}
    fs = parse_payload(spec, bytes.fromhex("000186a0"))  # 100000 > 4096
    assert fs[1].errors and fs[1].errors[0].startswith("expr:")
