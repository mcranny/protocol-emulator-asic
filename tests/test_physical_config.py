"""Guard nominal RC initialization independently of the final signoff checker."""
import json
from pathlib import Path


def test_pinned_nominal_rc_values_populate_routed_resizer_tables():
    config = json.loads((Path(__file__).parents[1] / "src/config.json").read_text())
    # Pinned CMOS5L tech LEF, corroborated by run 35460188674/tlef_values.rpt.
    # Resistance: kohm/um. Capacitance: pF/um. Via resistance: kohm.
    assert config["LAYERS_RC"]["nom_*"] == {
        "Metal1": {"res": 0.00084375, "cap": 0.000068784},
        "Metal2": {"res": 0.000515, "cap": 0.00009302},
        "Metal3": {"res": 0.000515, "cap": 0.000092},
        "Metal4": {"res": 0.000515, "cap": 0.000091788},
    }
    assert config["VIAS_R"]["*"] == {
        "Cont": {"res": 0.022}, "Via1": {"res": 0.02},
        "Via2": {"res": 0.02}, "Via3": {"res": 0.02},
    }
    assert config["CLOCK_PERIOD"] == 40
    assert config["MAX_SLEW_VIOLATION_CORNERS"] == ["*"]
    assert config["MAX_CAP_VIOLATION_CORNERS"] == ["*"]
