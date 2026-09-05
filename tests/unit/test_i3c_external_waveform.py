from pathlib import Path

from decodehub.acquisition.adapters.kingst_csv import load
from decodehub.decode.protocols.i3c.decode import I3cDecodeNode


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "external" / "i3c" / "i3c_sdr_smoke.csv"


def test_generated_i3c_waveform_is_ingestable_and_decodes_private_write():
    capture = load(FIXTURE, {"sample_rate": 100_000})
    assert capture.digital is not None
    assert capture.digital.channels == ("SCL", "SDA")

    params = {name: decl.default for name, decl in I3cDecodeNode.PARAMS.items()}
    params.update({"scl": "SCL", "sda": "SDA", "mode": "sdr"})
    events = I3cDecodeNode().run({"in": capture.digital}, params)["out"]
    transfers = [event for event in events if event.kind == "i3c.transfer"]
    assert [(event.address, event.data_bytes, event.errors) for event in transfers] == [
        (0x20, [0x77, 0x01], [])
    ]
