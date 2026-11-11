(*
  plc-power: CherryMountain Brewery power PLC, full and lite variants
  (DESIGN.md 7.3, 7.4, 12.1). Built by plc/common/build_zip.sh.

  - Outage ride-through: while the utility is lost, or the ATS is still on the
    emergency source after a loss, the large MSB feeders (boiler, packaging,
    RTU-1) are held open (load shed). When the utility is back, they reclose in
    stages, one every FEEDER_STAGE. An ATS transfer with a healthy utility is
    the weekly exercise (or a genset-exercise event), not an outage: no shed.
  - BESS dispatch, discharge positive. Priority 1, the monthly demand cap:
    discharge to hold the import under the cap. Priority 2, on-peak energy
    shift: discharge up to ONPEAK_KW (never more than the site imports) in the
    SV-SGT window (weekdays 17:00-21:00), down to SOC_EMPTY, or down to the
    brew-day reserve (PWR_DMD_EY01_SP) while XS03.BREWDAY says the next 05:00
    starts a brew day. Charge: the PV surplus, and from the grid overnight
    (21:00-05:00, off-peak) at RECHARGE_KW, never past the cap, at full rate
    while a brew day is below its reserve. The utility's own dispatch (rtu-der)
    overrides all this in the inverter.
  - PV site limit: 100 % (the utility's limit is applied in the inverter).
  - Month-to-date peak demand from the meter's 15-minute demand, reset at the
    billing-month rollover. The meter's last interval at the rollover belongs
    to the old month, so the peak ignores it for one demand interval.
  - Weekly genset exercise: Wednesday 10:00 to 16:00 (off-peak), the first
    moment the plant is on the utility with every feeder restored and the set
    at rest, one run per day, EX_RUN under load (30 minutes at 30 % of
    nameplate is the NFPA 110 monthly test; the 8 s warm-up is margin). It
    never starts, and stops, during an outage or load shed.

  The schedule runs from the ATS controller's clock (PWR.GEN.KI01 to KI03: the
  minute of the day, the date as days since 1970-01-01, the month). The PLC
  trusts the clock only while it is in range, moves forward by at most
  CLK_MAX_STEP minutes between readings, and changes at least every CLK_STALE;
  a reading that does not fit is not acted on (one scan of it is a torn read),
  a jump is re-baselined, and CLK_GOOD_TICKS steady minutes restore trust.
  While the clock is untrusted (XS05.CLKOK = 0, alarm after 5 minutes) the PLC
  falls back to the conservative behavior: demand-cap and PV-surplus dispatch
  only, XS01.ONPEAK = 0, no recharge, no demand reset, no exercise start (a
  running exercise finishes on its own timer). The clock is local civil time
  with no daylight-saving shift, as in the simulation; it must not be the
  cellar's compressed fermentation clock (CEL.CLK.KI01).
  Not retained across a PLC restart: the month's peak, the day of the last
  exercise (a restart inside the window can exercise twice), and whether a
  month rollover was seen.
  Alarms are generated from alarms.json by plcbuild.py at the ALARMS marker.

  Units: raw registers, engineering value x 10 (kW, kvar, V, %), INT where the
  flow is signed (import and discharge positive). The clock registers and the
  operator setpoints and the peak in %MW (kW, %) are unscaled.
*)

PROGRAM plc_power
  (* LOCATED_VARS: plcbuild.py splices the located variables and alarm instances here. *)

  VAR CONSTANT
    FEEDER_STAGE : TIME := T#60s;   (* between feeder recloses after an outage *)
    CAP_MARGIN : DINT := 100;       (* 10 kW below the cap *)
    BESS_MAX : DINT := 1000;        (* 100 kW *)
    SOC_EMPTY : DINT := 100;        (* 10 % *)
    SOC_FULL : DINT := 950;         (* 95 % *)
    PV_SITE_LIMIT : UINT := 1000;   (* 100 % *)
    ONPEAK_START : DINT := 1020;    (* 17:00, minutes of the day (SV-SGT, weekdays) *)
    ONPEAK_END : DINT := 1260;      (* 21:00 *)
    BREW_START : DINT := 300;       (* 05:00, a brew day's first boil *)
    ONPEAK_KW : DINT := 500;        (* 50 kW energy shift *)
    RECHARGE_KW : DINT := 300;      (* 30 kW overnight grid recharge *)
    DEMAND_INTERVAL : DINT := 15;   (* minutes, the meter's demand interval *)
    CLK_GOOD_TICKS : INT := 2;      (* steady minutes before the clock is trusted *)
    CLK_MAX_STEP : DINT := 60;      (* most minutes between two readings *)
    CLK_REJECT : TIME := T#10s;     (* readings that do not fit this long: a jump *)
    CLK_STALE : TIME := T#180s;     (* no change this long: the clock stopped *)
    EX_DOW : DINT := 3;             (* Wednesday, 0 = Sunday *)
    EX_START : DINT := 600;         (* 10:00 *)
    EX_END : DINT := 960;           (* 16:00: the last start; EX_RUN ends before the 17:00 window *)
    EX_RUN : TIME := T#32m;         (* the exercise command, under load *)
  END_VAR

  VAR
    started : BOOL := FALSE;
    shed : BOOL;
    outage : BOOL;
    outage_hold : BOOL;             (* ATS still on the genset after a utility loss *)
    stage : INT := 3;               (* feeders reclosed so far after an outage *)
    stage_timer : TON;
    minute : DINT;                  (* clock registers, as read *)
    day : DINT;
    month : DINT;
    dow : DINT;                     (* 0 = Sunday *)
    now_min : DINT;                 (* day * 1440 + minute, monotonic *)
    last_min : DINT;                (* the last reading that fit *)
    have_last : BOOL;
    good_ticks : INT;
    clk_range : BOOL;
    clk_fits : BOOL;
    clk_tick : BOOL;
    clk_miss : BOOL;
    clk_idle : BOOL;
    clk_ok : BOOL;
    clk_reject_timer : TON;
    clk_stale_timer : TON;
    month_known : BOOL;
    last_month : DINT;
    hold_until : DINT;              (* the peak ignores the meter until this minute *)
    onpeak : BOOL;
    overnight : BOOL;
    ex_cmd : BOOL;
    ex_day : DINT;                  (* the day of the last exercise start *)
    ex_timer : TON;
    soc : DINT;                     (* % x 10 *)
    reserve : DINT;                 (* % x 10 *)
    shift_floor : DINT;
    site_import : DINT;             (* import without the battery, kW x 10 *)
    cap : DINT;
    discharge : DINT;
    charge : DINT;
    recharge_rate : DINT;
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
  outage := NOT PWR_GEN_ZS02_PV OR NOT PWR_MAIN_ZS01_PV;
  IF outage THEN
    outage_hold := TRUE;
  ELSIF NOT PWR_GEN_ZS01_PV THEN
    outage_hold := FALSE;
  END_IF;
  shed := outage OR (PWR_GEN_ZS01_PV AND outage_hold);
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

  (* ---- the ATS controller's clock: trusted only while it is steady --------------------- *)
  minute := UINT_TO_DINT(PWR_GEN_KI01_PV);
  day := UINT_TO_DINT(PWR_GEN_KI02_PV);
  month := UINT_TO_DINT(PWR_GEN_KI03_PV);
  now_min := day * 1440 + minute;
  dow := (day + 4) MOD 7;                    (* 1970-01-01 was a Thursday *)
  clk_range := minute < 1440 AND month >= 1 AND month <= 12 AND day > 0;
  clk_fits := clk_range AND have_last AND now_min >= last_min AND now_min - last_min <= CLK_MAX_STEP;
  clk_tick := clk_fits AND now_min > last_min;
  clk_miss := clk_range AND have_last AND NOT clk_fits;
  clk_idle := clk_range AND have_last AND NOT clk_tick;
  IF NOT clk_range THEN
    have_last := FALSE;                      (* never written, or garbage *)
    good_ticks := 0;
  ELSIF NOT have_last THEN
    last_min := now_min;                     (* the baseline: first reading, or after a loss *)
    have_last := TRUE;
    good_ticks := 0;
  ELSIF clk_tick THEN
    last_min := now_min;
    good_ticks := MIN(good_ticks + 1, CLK_GOOD_TICKS);
  END_IF;
  clk_reject_timer(IN := clk_miss, PT := CLK_REJECT);
  clk_stale_timer(IN := clk_idle, PT := CLK_STALE);
  IF clk_reject_timer.Q OR clk_stale_timer.Q THEN
    have_last := FALSE;                      (* a jump or a stopped clock: start over *)
    good_ticks := 0;
  END_IF;
  clk_ok := clk_fits AND good_ticks >= CLK_GOOD_TICKS;
  PWR_DMD_XS05_CLKOK := clk_ok;

  (* ---- the schedule, from the clock ------------------------------------------------------- *)
  onpeak := clk_ok AND dow >= 1 AND dow <= 5 AND minute >= ONPEAK_START AND minute < ONPEAK_END;
  PWR_DMD_XS01_ONPEAK := onpeak;
  overnight := clk_ok AND (minute >= ONPEAK_END OR minute < BREW_START);
  IF clk_ok THEN
    IF NOT month_known THEN
      last_month := month;                   (* the first trusted reading: no rollover *)
      month_known := TRUE;
    ELSIF month <> last_month THEN
      last_month := month;
      PWR_DMD_JY02_PV := 0;                  (* the billing month rolled over *)
      hold_until := now_min + DEMAND_INTERVAL;
    END_IF;
  END_IF;

  (* ---- weekly genset exercise ------------------------------------------------------------ *)
  IF clk_ok AND dow = EX_DOW AND minute >= EX_START AND minute < EX_END AND day <> ex_day AND NOT ex_cmd
    AND NOT shed AND stage >= 3 AND PWR_GEN_ZS02_PV AND PWR_MAIN_ZS01_PV
    AND NOT PWR_GEN_ZS01_PV AND NOT PWR_GEN_YS01_PV THEN
    ex_cmd := TRUE;
    ex_day := day;
  END_IF;
  ex_timer(IN := ex_cmd, PT := EX_RUN);
  IF ex_cmd AND (ex_timer.Q OR shed) THEN
    ex_cmd := FALSE;
    ex_timer(IN := FALSE, PT := EX_RUN);
  END_IF;
  PWR_GEN_HS01_CMD := ex_cmd;
  PWR_DMD_XS04_EXERCISE := ex_cmd OR (PWR_GEN_ZS01_PV AND NOT outage_hold);

  (* ---- PV ------------------------------------------------------------------------------- *)
  PWR_PV_HS01_CMD := TRUE;
  PWR_PV_JC01_SP := PV_SITE_LIMIT;

  (* ---- BESS dispatch: demand cap, on-peak shift, PV surplus, overnight recharge ----------- *)
  PWR_BESS_HS01_CMD := TRUE;
  soc := UINT_TO_DINT(PWR_BESS_EY01_PV);
  reserve := UINT_TO_DINT(PWR_DMD_EY01_SP) * 10;
  shift_floor := SOC_EMPTY;
  IF PWR_DMD_XS03_BREWDAY THEN
    shift_floor := MAX(SOC_EMPTY, reserve);  (* keep the 05:00 reserve for the brew day *)
  END_IF;
  site_import := INT_TO_DINT(PWR_MAIN_JT01_PV) + INT_TO_DINT(PWR_BESS_JT01_PV);
  cap := UINT_TO_DINT(PWR_DMD_JY01_SP) * 10 - CAP_MARGIN;
  discharge := 0;
  charge := 0;
  IF site_import > cap AND soc > SOC_EMPTY THEN
    discharge := MIN(site_import - cap, BESS_MAX);               (* priority 1: the demand cap *)
  END_IF;
  IF onpeak AND soc > shift_floor THEN
    discharge := MAX(discharge, MIN(ONPEAK_KW, MAX(site_import, 0)));  (* priority 2: no export *)
  END_IF;
  IF site_import < 0 AND soc < SOC_FULL THEN
    charge := MIN(-site_import, BESS_MAX);                       (* store the PV surplus *)
  END_IF;
  IF overnight AND soc < SOC_FULL THEN
    recharge_rate := RECHARGE_KW;
    IF PWR_DMD_XS03_BREWDAY AND soc < reserve THEN
      recharge_rate := BESS_MAX;                                 (* hold the 05:00 reserve *)
    END_IF;
    charge := MAX(charge, MIN(recharge_rate, MAX(cap - site_import, 0)));
  END_IF;
  dispatch := discharge - charge;
  IF shed THEN
    dispatch := 0;  (* the inverters are disconnected during an outage *)
  END_IF;
  PWR_BESS_JC01_SP := DINT_TO_INT(dispatch);

  (* ---- demand tracking ---------------------------------------------------------------- *)
  demand_kw := PWR_MAIN_JQ01_PV / 10;
  IF demand_kw > PWR_DMD_JY02_PV AND NOT (clk_ok AND now_min < hold_until) THEN
    PWR_DMD_JY02_PV := demand_kw;
  END_IF;

  (* ALARMS: plcbuild.py generates the alarm logic here. *)
END_PROGRAM
