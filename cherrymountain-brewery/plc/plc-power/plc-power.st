(*
  plc-power: CherryMountain Brewery power PLC, full and lite variants
  (DESIGN.md 7.3, 12.1). Built by plc/common/build_zip.sh.

  - Outage ride-through: while the ATS is on the emergency source or the
    utility is unavailable, the large MSB feeders (boiler, packaging, RTU-1)
    are held open (load shed). When the utility is back, they reclose in
    stages, one every FEEDER_STAGE.
  - BESS dispatch (priority 1, the monthly demand cap): discharge to hold the
    import under the cap, charge from PV surplus, and otherwise idle. The
    utility's own dispatch (rtu-der) overrides this in the inverter.
  - PV site limit: 100 % (the utility's limit is applied in the inverter).
  - Month-to-date peak demand from the meter's 15-minute demand.

  Open (no time source on FIELD-PWR): the on-peak window (XS01.ONPEAK), the
  BESS's 05:00 minimum state of charge on brew days, the monthly demand reset,
  and the weekly genset exercise need a clock point; until one exists the
  on-peak bit stays FALSE, the peak is never reset, and the genset is
  exercised only by hand (DESIGN.md 19).
  Alarms are generated from alarms.json by plcbuild.py at the ALARMS marker.

  Units: raw registers, engineering value x 10 (kW, kvar, V, %), INT where the
  flow is signed (import and discharge positive).
*)

PROGRAM plc_power
  (* LOCATED_VARS: plcbuild.py splices the located variables and alarm instances here. *)

  VAR CONSTANT
    FEEDER_STAGE : TIME := T#60s;   (* between feeder recloses after an outage *)
    CAP_MARGIN : DINT := 100;       (* 10 kW below the cap *)
    BESS_MAX : DINT := 1000;        (* 100 kW *)
    SOC_EMPTY : UINT := 100;        (* 10 % *)
    SOC_FULL : UINT := 950;         (* 95 % *)
    PV_SITE_LIMIT : UINT := 1000;   (* 100 % *)
  END_VAR

  VAR
    started : BOOL := FALSE;
    shed : BOOL;
    stage : INT := 3;               (* feeders reclosed so far after an outage *)
    stage_timer : TON;
    site_import : DINT;             (* import without the battery, kW x 10 *)
    cap : DINT;
    dispatch : DINT;
    demand_kw : UINT;
  END_VAR

  IF NOT started THEN
    PWR_DMD_JY01_SP := 250;          (* 250 kW monthly demand cap *)
    PWR_DMD_EY01_SP := 50;           (* 50 % before 05:00 on brew days *)
    PWR_DMD_JY02_PV := 0;
    started := TRUE;
  END_IF;
  PWR_DMD_JY01_SP := LIMIT(50, PWR_DMD_JY01_SP, 600);
  PWR_DMD_EY01_SP := MIN(PWR_DMD_EY01_SP, 100);

  (* ---- outage ride-through and staged restoration ------------------------------------ *)
  shed := PWR_GEN_ZS01_PV OR NOT PWR_GEN_ZS02_PV OR NOT PWR_MAIN_ZS01_PV;
  PWR_DMD_XS02_SHED := shed;
  IF shed THEN
    stage := 0;
  END_IF;
  stage_timer(IN := (NOT shed) AND stage < 3, PT := FEEDER_STAGE);
  IF stage_timer.Q THEN
    stage := stage + 1;
    stage_timer(IN := FALSE, PT := FEEDER_STAGE);
  END_IF;
  PWR_MAIN_HS01_CMD := TRUE;                 (* 52-M stays closed; its relay trips it *)
  PWR_MAIN_HS02_CMD := stage >= 1;           (* boiler first, the largest brew load *)
  PWR_MAIN_HS03_CMD := stage >= 2;           (* packaging *)
  PWR_MAIN_HS04_CMD := stage >= 3;           (* RTU-1 *)

  (* ---- PV ------------------------------------------------------------------------------- *)
  PWR_PV_HS01_CMD := TRUE;
  PWR_PV_JC01_SP := PV_SITE_LIMIT;

  (* ---- BESS dispatch, priority 1: the monthly demand cap ------------------------------- *)
  PWR_BESS_HS01_CMD := TRUE;
  site_import := INT_TO_DINT(PWR_MAIN_JT01_PV) + INT_TO_DINT(PWR_BESS_JT01_PV);
  cap := UINT_TO_DINT(PWR_DMD_JY01_SP) * 10 - CAP_MARGIN;
  IF site_import > cap AND PWR_BESS_EY01_PV > SOC_EMPTY THEN
    dispatch := MIN(site_import - cap, BESS_MAX);
  ELSIF site_import < 0 AND PWR_BESS_EY01_PV < SOC_FULL THEN
    dispatch := MAX(site_import, -BESS_MAX);  (* store the PV surplus *)
  ELSE
    dispatch := 0;
  END_IF;
  IF shed THEN
    dispatch := 0;  (* the inverters are disconnected during an outage *)
  END_IF;
  PWR_BESS_JC01_SP := DINT_TO_INT(dispatch);

  (* ---- demand tracking ---------------------------------------------------------------- *)
  demand_kw := PWR_MAIN_JQ01_PV / 10;
  IF demand_kw > PWR_DMD_JY02_PV THEN
    PWR_DMD_JY02_PV := demand_kw;
  END_IF;
  PWR_DMD_XS01_ONPEAK := FALSE;   (* TODO(clock): weekdays 17:00-21:00 *)
  PWR_GEN_HS01_CMD := FALSE;      (* TODO(clock): the weekly exercise *)

  (* ALARMS: plcbuild.py generates the alarm logic here. *)
END_PROGRAM
