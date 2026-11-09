(*
  plc-utilities: CherryMountain Brewery utilities PLC, full and lite variants
  (DESIGN.md 6.3, 12.1). Built by plc/common/build_zip.sh.

  - Glycol: the chiller's supply setpoint passed to the OEM controller (which
    stages the compressors itself), and the glycol pump speed held on supply
    pressure (proportional control).
  - Steam, air, RO: enables and the boiler pressure setpoint (12 psig: the
    boiler is an ASME Section IV unit with its safety valve at the 15 psig
    MAWP, DESIGN.md 18A.3). The boiler's low-water cutoff is a hardwired trip;
    the PLC also drops the enable.
  - CO2 supply shutoff (DESIGN.md 6.7, Denver IFC 5307): UTL.CO2.XS01.CO2_OK is
    the bas-vent shutoff relay, hardwired and de-energize to trip (it drops at
    30,000 ppm in any area and on loss of detection power or the detection
    panel). The valve's solenoid is wired through the same contact, so the
    valve fails closed without the PLC; the PLC also drops its command and
    latches XS02.SHUTOFF, which only the operator's reset (XC01.RESET) clears,
    and only once the relay has been healthy for CO2_RESET_DELAY. The latch
    arms the first time the relay is healthy, so a power-up is not a trip.
  - Wastewater neutralization: caustic below pH 6.5, acid above pH 8.5 (each
    with hysteresis), and the discharge valve only between pH 6.0 and 9.0.
  - CIP: route verification, then pre-rinse, caustic, rinse, acid, rinse,
    sanitize. The cellar circuits also need plc-cellar's CIP permit, read
    peer to peer (CEL.XFER.XS02.CIP_PERMIT into UTL.CIP.XS04.PV, which reads 0
    when plc-cellar is unreachable, so CIP fails safe; DESIGN.md 6.6 #4). A cellar vessel locked out
    for confined-space entry (its CEL.ENTRY.XS1n.CIP_OK, read the same way into
    UTL.CIP.XS05..XS10) is never a permitted CIP circuit.
  Alarms are generated from alarms.json by plcbuild.py at the ALARMS marker.

  Units: raw registers, engineering value x 10^scaling (degF x 10, psig x 10,
  pH x 100, % x 10).
*)

PROGRAM plc_utilities
  (* LOCATED_VARS: plcbuild.py splices the located variables and alarm instances here. *)

  VAR CONSTANT
    GLYCOL_SP_MIN : UINT := 200;      (* 20.0 degF *)
    GLYCOL_SP_MAX : UINT := 400;      (* 40.0 degF *)
    PUMP_SPEED_BASE : DINT := 600;    (* 60 % at the setpoint *)
    PUMP_GAIN : DINT := 4;            (* % x 10 per psig x 10 of error *)
    BOILER_SP : UINT := 120;          (* 12.0 psig, below the 15 psig safety valve *)
    CO2_RESET_DELAY : TIME := T#60s;  (* relay healthy this long before a reset *)
    PH_CAUSTIC_ON : UINT := 650;
    PH_CAUSTIC_OFF : UINT := 700;
    PH_ACID_ON : UINT := 850;
    PH_ACID_OFF : UINT := 800;
    PH_PERMIT_LOW : UINT := 600;
    PH_PERMIT_HIGH : UINT := 900;
    WWN_DISCHARGE_MIN_LEVEL : UINT := 200;   (* 20 % *)
    CIP_STEP_TIME : TIME := T#300s;   (* each wash step, compressed for the range *)
    CIP_HEAT_GAIN : DINT := 20;       (* % x 10 per degF x 10 of error *)
    CIP_CMD_START : UINT := 1;
    CIP_CMD_HOLD : UINT := 2;
    CIP_CMD_ABORT : UINT := 3;
    CIP_STEP_DONE : INT := 7;
  END_VAR

  VAR
    started : BOOL := FALSE;
    err : DINT;
    caustic_dose : BOOL;
    acid_dose : BOOL;
    cip_step : INT := 0;
    cip_last_step : INT := 0;
    cip_hold : BOOL;
    cip_panel_ok : BOOL;
    cip_permit_ok : BOOL;
    cip_timer : TON;
    cip_wash : BOOL;
    cip_vessel_ok : BOOL;
    co2_healthy : TON;
    co2_armed : BOOL := FALSE;
  END_VAR

  IF NOT started THEN
    UTL_CHL_TT01_SP := 280;      (* 28.0 degF *)
    UTL_GLY_PT01_SP := 300;      (* 30.0 psig *)
    UTL_CIP_TT01_SP := 1750;     (* 175.0 degF *)
    UTL_CIP_XC01_CIRCUIT := 0;
    UTL_CIP_XC01_CMD := 0;
    started := TRUE;
  END_IF;
  UTL_CHL_TT01_SP := LIMIT(GLYCOL_SP_MIN, UTL_CHL_TT01_SP, GLYCOL_SP_MAX);
  UTL_GLY_PT01_SP := MIN(UTL_GLY_PT01_SP, 1000);
  UTL_CIP_TT01_SP := LIMIT(320, UTL_CIP_TT01_SP, 2000);
  UTL_CIP_XC01_CIRCUIT := MIN(UTL_CIP_XC01_CIRCUIT, 7);

  (* ---- glycol ------------------------------------------------------------------- *)
  UTL_CHL_HS01_CMD := TRUE;
  UTL_CHL_TIC01_SP := UTL_CHL_TT01_SP;
  UTL_GLY_HS01_CMD := TRUE;
  err := UINT_TO_DINT(UTL_GLY_PT01_SP) - UINT_TO_DINT(UTL_GLY_PT01_PV);
  UTL_GLY_SC01_OUT := DINT_TO_UINT(LIMIT(200, PUMP_SPEED_BASE + PUMP_GAIN * err, 1000));

  (* ---- steam, air, CO2, RO ----------------------------------------------------------- *)
  UTL_STM_HS01_CMD := NOT UTL_STM_LSL01_PV;
  UTL_STM_PC01_SP := BOILER_SP;
  UTL_AIR_HS01_CMD := TRUE;

  (* ---- CO2 supply shutoff (DESIGN.md 6.7) ------------------------------------------ *)
  co2_healthy(IN := UTL_CO2_XS01_CO2_OK, PT := CO2_RESET_DELAY);
  IF co2_healthy.Q THEN
    co2_armed := TRUE;
  END_IF;
  IF co2_armed AND NOT UTL_CO2_XS01_CO2_OK THEN
    UTL_CO2_XS02_SHUTOFF := TRUE;
  END_IF;
  IF UTL_CO2_XC01_RESET THEN
    IF co2_healthy.Q THEN
      UTL_CO2_XS02_SHUTOFF := FALSE;
    END_IF;
    UTL_CO2_XC01_RESET := FALSE;
  END_IF;
  UTL_CO2_XV01_CMD := UTL_CO2_XS01_CO2_OK AND NOT UTL_CO2_XS02_SHUTOFF;
  UTL_RO_HS01_CMD := TRUE;

  (* ---- wastewater neutralization --------------------------------------------------- *)
  IF UTL_WWN_AT01_PV < PH_CAUSTIC_ON THEN
    caustic_dose := TRUE;
  ELSIF UTL_WWN_AT01_PV >= PH_CAUSTIC_OFF THEN
    caustic_dose := FALSE;
  END_IF;
  IF UTL_WWN_AT01_PV > PH_ACID_ON THEN
    acid_dose := TRUE;
  ELSIF UTL_WWN_AT01_PV <= PH_ACID_OFF THEN
    acid_dose := FALSE;
  END_IF;
  UTL_WWN_XV01_CMD := caustic_dose;
  UTL_WWN_XV02_CMD := acid_dose;
  UTL_WWN_XS01_INHIBIT := (UTL_WWN_AT01_PV < PH_PERMIT_LOW) OR (UTL_WWN_AT01_PV > PH_PERMIT_HIGH);
  UTL_WWN_XV03_CMD := (NOT UTL_WWN_XS01_INHIBIT) AND (UTL_WWN_LT01_PV > WWN_DISCHARGE_MIN_LEVEL);

  (* ---- CIP --------------------------------------------------------------------------- *)
  (* Circuits: 1 brewhouse, 2-5 FV-101..104, 6-7 BBT-201/202 (swing panels ZS01..ZS07). *)
  CASE UTL_CIP_XC01_CIRCUIT OF
    1: cip_panel_ok := UTL_CIP_ZS01_PV;
    2: cip_panel_ok := UTL_CIP_ZS02_PV;
    3: cip_panel_ok := UTL_CIP_ZS03_PV;
    4: cip_panel_ok := UTL_CIP_ZS04_PV;
    5: cip_panel_ok := UTL_CIP_ZS05_PV;
    6: cip_panel_ok := UTL_CIP_ZS06_PV;
    7: cip_panel_ok := UTL_CIP_ZS07_PV;
  ELSE
    cip_panel_ok := FALSE;
  END_CASE;
  (* Cellar circuits need plc-cellar's permit (no transfer in progress) and the
     vessel's own: not locked out for confined-space entry. *)
  CASE UTL_CIP_XC01_CIRCUIT OF
    2: cip_vessel_ok := UTL_CIP_XS05_PV;
    3: cip_vessel_ok := UTL_CIP_XS06_PV;
    4: cip_vessel_ok := UTL_CIP_XS07_PV;
    5: cip_vessel_ok := UTL_CIP_XS08_PV;
    6: cip_vessel_ok := UTL_CIP_XS09_PV;
    7: cip_vessel_ok := UTL_CIP_XS10_PV;
  ELSE
    cip_vessel_ok := TRUE;
  END_CASE;
  cip_permit_ok := (UTL_CIP_XC01_CIRCUIT = 1)
    OR (UTL_CIP_XS04_PV AND NOT UTL_CIP_XS03_PV AND cip_vessel_ok);
  UTL_CIP_XS02_ROUTE_OK := cip_panel_ok AND cip_permit_ok;

  IF UTL_CIP_XC01_CMD = CIP_CMD_ABORT THEN
    cip_step := 0;
    UTL_CIP_XC01_CMD := 0;
  ELSIF UTL_CIP_XC01_CMD = CIP_CMD_START AND (cip_step = 0 OR cip_step = CIP_STEP_DONE)
      AND UTL_CIP_XS02_ROUTE_OK THEN
    cip_step := 1;
    UTL_CIP_XC01_CMD := 0;
  END_IF;
  (* Hold on command, or when the route is lost or (once the pumps run) the return
     flow is not proven. *)
  cip_hold := (UTL_CIP_XC01_CMD = CIP_CMD_HOLD) OR (NOT UTL_CIP_XS02_ROUTE_OK)
    OR (UTL_CIP_HS02_CMD AND cip_timer.ET > T#30s AND NOT UTL_CIP_FS01_PV);
  cip_wash := cip_step >= 1 AND cip_step <= 6;
  IF cip_step <> cip_last_step THEN
    cip_timer(IN := FALSE, PT := CIP_STEP_TIME);  (* restart the step timer *)
    cip_last_step := cip_step;
  END_IF;
  cip_timer(IN := cip_wash AND NOT cip_hold, PT := CIP_STEP_TIME);
  IF cip_wash AND cip_timer.Q THEN
    cip_step := cip_step + 1;
  END_IF;
  UTL_CIP_XS01_STEP := INT_TO_UINT(cip_step);
  UTL_CIP_HS01_CMD := cip_wash AND NOT cip_hold;
  UTL_CIP_HS02_CMD := cip_wash AND NOT cip_hold;
  UTL_CIP_XV01_CMD := cip_step = 2 AND NOT cip_hold;                    (* caustic *)
  UTL_CIP_XV02_CMD := (cip_step = 4 OR cip_step = 6) AND NOT cip_hold;  (* acid, sanitizer *)
  IF cip_step = 2 AND NOT cip_hold THEN
    err := UINT_TO_DINT(UTL_CIP_TT01_SP) - UINT_TO_DINT(UTL_CIP_TT01_PV);
    UTL_CIP_TV01_OUT := DINT_TO_UINT(LIMIT(0, CIP_HEAT_GAIN * err, 1000));
  ELSE
    UTL_CIP_TV01_OUT := 0;
  END_IF;

  (* ALARMS: plcbuild.py generates the alarm logic here. *)
END_PROGRAM
