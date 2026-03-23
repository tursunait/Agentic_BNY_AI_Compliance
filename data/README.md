# Data folder

This folder holds synthetic and reference data for testing the narrative generator and related workflows.

## sanctions/

Synthetically generated **OFAC sanctions rejected transaction** report inputs for testing the OFAC_REJECT narrative flow. Each JSON file has the required keys (`case_id`, `transaction`, `case_facts`) and optional fields (`institution`, `preparer`, `report_type_code`, etc.).

| File | Scenario | Program | Transaction type |
|------|----------|---------|-------------------|
| `ofac_reject_01_iran_wire.json` | Sanctioned jurisdiction (Iran) | Iran — ITSR | Outbound wire |
| `ofac_reject_02_syria_ach.json` | Sanctioned jurisdiction (Syria) | Syria — SSR | ACH credit |
| `ofac_reject_03_russia_wire.json` | Sanctioned entity (VTB) | Russia — E.O. 14024 | Outbound wire |
| `ofac_reject_04_north_korea_wire.json` | Sanctioned jurisdiction (DPRK) | North Korea — NKSR | Outbound wire |
| `ofac_reject_05_sdn_match.json` | SDN list match (Cyprus) | SDN List | Outbound wire (EUR) |
| `ofac_reject_06_cuba_remittance.json` | Sanctioned jurisdiction (Cuba) | Cuba — CACR | International remittance |
| `ofac_reject_07_venezuela_wire.json` | Sanctioned entity (PdVSA) | Venezuela — E.O. 13850 | Outbound wire |
| `ofac_reject_08_iran_trade.json` | Sanctioned jurisdiction (Iran) | Iran — ITSR | Letter of credit payment |
| `ofac_reject_09_dual_use_iran.json` | Iran / dual-use | Iran — ITSR | Outbound wire |
| `ofac_reject_10_entity_match.json` | SDN / Russia entity | Russia — E.O. 14024 | Outbound wire |
| `ofac_reject_11_belarus_wire.json` | Sanctioned jurisdiction (Belarus) | Belarus — E.O. 13405/14038 | Outbound wire |
| `ofac_reject_12_myanmar_wire.json` | Sanctioned jurisdiction (Myanmar) | Myanmar — E.O. 14014 | Outbound wire |
| `ofac_reject_13_crimea_wire.json` | Sanctioned jurisdiction (Crimea) | Ukraine/Russia — Crimea (E.O. 13685) | Outbound wire (EUR) |
| `ofac_reject_14_hezbollah_match.json` | SDN / terrorism | SDN — Global Terrorism | Outbound wire |
| `ofac_reject_15_iran_ach.json` | Sanctioned jurisdiction (Iran) | Iran — ITSR | ACH debit (international) |
| `ofac_reject_16_syria_wire.json` | Sanctioned entity (Syria oil) | Syria — SSR | Outbound wire |
| `ofac_reject_17_north_korea_remittance.json` | Sanctioned jurisdiction (DPRK) | North Korea — NKSR | International remittance |
| `ofac_reject_18_russia_ach.json` | Sanctioned entity (Sberbank) | Russia — E.O. 14024 | ACH credit |
| `ofac_reject_19_iran_check_deposit.json` | Sanctioned jurisdiction (Iran) | Iran — ITSR | Check deposit / incoming |
| `ofac_reject_20_cuba_wire.json` | Sanctioned jurisdiction (Cuba) | Cuba — CACR | Outbound wire |
| `ofac_reject_21_venezuela_ach.json` | Sanctioned entity (Venezuela) | Venezuela — E.O. 13850/13884 | ACH credit |
| `ofac_reject_22_iran_lc_standby.json` | Sanctioned jurisdiction (Iran) | Iran — ITSR | Standby LC draw |
| `ofac_reject_23_sdn_narcotics.json` | SDN / Kingpin | SDN — Narcotics Kingpin | Outbound wire |
| `ofac_reject_24_russia_lc.json` | Sanctioned entity (Russia) | Russia — E.O. 14024 | Documentary LC payment |
| `ofac_reject_25_syria_remittance.json` | Sanctioned jurisdiction (Syria) | Syria — SSR | International remittance |
| `ofac_reject_26_iran_oil.json` | Sanctioned jurisdiction (Iran) | Iran — ITSR / petroleum | Outbound wire (trade) |
| `ofac_reject_27_belarus_entity.json` | Sanctioned entity (Belarus) | Belarus — E.O. 14038 | Outbound wire |
| `ofac_reject_28_myanmar_entity.json` | List match (Myanmar) | Myanmar — E.O. 14014 | Outbound wire |
| `ofac_reject_29_secondary_sanctions.json` | Iran / correspondent | Iran — ITSR (secondary) | Correspondent incoming |
| `ofac_reject_30_mixed_donbas.json` | Sanctioned region (Donbas) | Ukraine/Russia — Donetsk/Luhansk | Outbound wire (EUR) |

All data is **fictional** and for testing only. Names, institutions, addresses, and amounts are not real.

### Using in code

```python
import json
from pathlib import Path

data_dir = Path("data/sanctions")  # or project_root / "data" / "sanctions"
for path in sorted(data_dir.glob("ofac_reject_*.json")):
    with open(path) as f:
        case = json.load(f)
    # run generate_narrative(case), validate_narrative(...), etc.
```

### Using in the notebook

Point to `data/sanctions/` and loop over the JSON files, or load a single file:

```python
project_root = Path("..").resolve()
sanctions_path = project_root / "data" / "sanctions" / "ofac_reject_01_iran_wire.json"
with open(sanctions_path) as f:
    ofac_input = json.load(f)
output = generate_narrative(ofac_input)
```
