(*
  plc-brewhouse: CherryMountain Brewery brewhouse PLC, full and lite variants
  (DESIGN.md 5.1, 6.3, 12.1). Built by plc/common/build_zip.sh; FUXA reads it
  over OPC UA.

  ISA-88 phases, commanded by the FUXA batch engine through BH.BATCH.XC01:
    PHASE  1 MILL, 2 HLT_HEAT, 3 MASH_IN, 4 MASH_STEP, 5 VORLAUF, 6 LAUTER,
           7 BOIL, 8 WHIRLPOOL, 9 KNOCKOUT, 10 CIP_READY
    CMD    1 Start, 2 Hold, 3 Restart, 4 Abort, 5 Reset (cleared once applied)
    STATE  0 Idle, 1 Running, 2 Holding, 3 Held, 4 Restarting, 5 Complete,
           6 Aborted
  Start is accepted in Idle, Complete, and Aborted; Hold and Restart pass
  through Holding and Restarting for one scan; Reset returns Complete or
  Aborted to Idle. The phase in force is latched at Start. A timed step (mill,
  mash rest, vorlauf, whirlpool and its hop dose) counts only the scans it
  runs, so a hold keeps the time already served and a restart finishes the
  rest: nothing is milled, rested, or dosed twice.

  The batch engine also writes the phase parameter BH.BATCH.XC03.PARAM, latched
  at Start like the phase (ISA-88 formal parameters are fixed for a phase run):
  MILL the grain bill in kg (2 s per kg at 0.5 kg/s), MASH_IN the strike liquor
  in liters, MASH_STEP the rest in minutes, BOIL the boil in minutes, WHIRLPOOL
  the stand in minutes. A parameter of 0 keeps the default below.

  MASH_IN fills the tun to the level that the strike liquor and the grain last
  milled come to (the grist takes 0.7 L/kg in the mash), and the HLT takes no
  cold liquor while it runs: the mash lands on its temperature only when it gets
  the recipe's liquor, all of it at the strike temperature.

  The batch request handshake with the engine (DESIGN.md 12.2): the engine
  reads the panel's request counter (BH.BATCH.XI02.REQUESTS, a RIO input) over
  OPC UA and writes the count it has accepted to BH.BATCH.XC02.ACCEPTED; this
  program only forwards that count to the panel (BH.BATCH.XI03.ACCEPTED).

  Equipment control that runs whatever the phase:
    - CLT and HLT levels (fill valves with hysteresis), and the HLT temperature
      (steam valve, proportional, only while the jacket is covered: the
      low-level switch BH.HLT.LSL01.PV is made from 15 % up and drops below
      it, so a dead input reads as uncovered and gives no steam).
    - The local wort boiling point from the barometric pressure
      (Clausius-Clapeyron for water, plus 0.5 degF for the extract), boil
      detection within 0.5 degF of it, and the boil timer.
  Confined-space entry (29 CFR 1910.146, DESIGN.md 6.3): BH.ENTRY.HS01.ENTRY
  (mash/lauter tun) and HS02.ENTRY (kettle) lock out the vessel's steam, water,
  runoff, rake, whirlpool, and hop outputs. A phase that uses a locked vessel
  (MLT: 3-6; kettle: 6-9) does not start or restart, and holds if it is
  running; a refused start, or a locked output found written between scans,
  latches the vessel's critical XAnn alarm until the entry ends. CIP circuit 1
  (brewhouse) is not interlocked: plc-utilities has no peer path to this PLC.
  Alarms are generated from alarms.json by plcbuild.py at the ALARMS marker;
  the mash deviation alarm is enabled only while a mash step holds
  (BH.MLT.XS01.ACTIVE).

  Units: raw registers, engineering value x 10 (degF, %, Hz, gal/min), x 100
  for pressures and O2 flow.
*)

(* Proportional valve: open = (SP - PV) x GAIN, clamped to 0..100 % (x 10). *)
FUNCTION PValve : UINT
  VAR_INPUT
    SP : UINT;
    PV : UINT;
    GAIN : DINT;
  END_VAR
  PValve := DINT_TO_UINT(LIMIT(0, (UINT_TO_DINT(SP) - UINT_TO_DINT(PV)) * GAIN, 1000));
END_FUNCTION

PROGRAM plc_brewhouse
  (* LOCATED_VARS: plcbuild.py splices the located variables and alarm instances here. *)

  VAR CONSTANT
    SCAN_MS : UDINT := 100;           (* the task interval plcbuild.py sets (T#100ms) *)
    MILL_MS : UDINT := 1020000;       (* 17 min: the 503 kg grain bill at 0.5 kg/s (50 Hz) *)
    VORLAUF_MS : UDINT := 600000;     (* 10 min *)
    MASH_HOLD_MS : UDINT := 3600000;  (* single infusion, 60 min *)
    WHIRLPOOL_MS : UDINT := 1200000;  (* 20 min *)
    HOP_DOSE_MS : UDINT := 30000;     (* the hop stand dose, 30 s *)
    BOIL_MINUTES_X10 : UINT := 600;   (* 60 min *)
    MILL_MS_PER_KG : UDINT := 2000;   (* 0.5 kg/s *)
    PARAM_MAX_MIN : UINT := 600;      (* a minutes parameter is at most 10 h *)
    GRAIN_BILL_KG : UINT := 503;          (* the recipe's grain bill *)
    RECIPE_STRIKE_L : UINT := 1310;       (* and its strike liquor: 2.6 L/kg *)
    STRIKE_MAX_L : UINT := 2300;      (* the tun does not hold more liquor on a grain bill *)
    MLT_VOLUME_L : UDINT := 3000;     (* 100 % on BH.MLT.LT01 *)
    GRIST_ML_PER_KG : UDINT := 700;   (* what a kilogram of grist takes up in the mash *)
    MASH_RAMP_ALLOW : TIME := T#15m;  (* a mash step may take this long to reach its temperature *)
    KETTLE_FULL : UINT := 850;        (* 85 % *)
    KETTLE_EMPTY : UINT := 50;        (* 5 % *)
    CLT_LOW : UINT := 600;
    CLT_HIGH : UINT := 900;
    HLT_LOW : UINT := 600;
    HLT_HIGH : UINT := 900;
    BED_DP_HIGH : UINT := 120;        (* 1.20 psi: lower the rake *)
  END_VAR

  VAR
    started : BOOL := FALSE;
    active : UINT := 0;               (* the phase latched at Start *)
    cmd : UINT;
    param : UINT := 0;                (* the phase parameter latched at Start *)
    limit_ms : UDINT := 0;            (* how long the latched phase's step runs *)
    boil_x10 : UINT := 600;           (* the latched boil, tenths of a minute *)
    grain_kg : UINT := 503;           (* the grain bill last milled *)
    strike_l : UINT := 1310;          (* the strike liquor of the latched mash-in *)
    mash_in_level : UINT := 554;      (* the tun level they come to, % x 10 *)
    param_min : UINT;
    step_ms : UDINT := 0;             (* time the latched phase's step has run *)
    timing : BOOL;                    (* the step's time counts this scan *)
    boil_tick : TON;
    running : BOOL;
    complete : BOOL;
    clt_fill : BOOL;
    hlt_fill : BOOL;
    p_kpa : REAL;
    t_boil_k : REAL;
    t_boil_f : REAL;
    want : UINT;
    blocked_mlt : BOOL;
    blocked_ktl : BOOL;
    mash_ramp : TON;
    mash_at_temp : BOOL;
  END_VAR

  IF NOT started THEN
    BH_HLT_TT01_SP := 1680;       (* 168.0 degF *)
    BH_MLT_TT01_SP := 1520;       (* 152.0 degF *)
    BH_KO_TT02_SP := 640;         (* 64.0 degF *)
    BH_BATCH_XC01_PHASE := 0;
    BH_BATCH_XC01_CMD := 0;
    BH_BATCH_XS01_STATE := 0;
    started := TRUE;
  END_IF;
  BH_HLT_TT01_SP := LIMIT(320, BH_HLT_TT01_SP, 2050);
  BH_MLT_TT01_SP := LIMIT(320, BH_MLT_TT01_SP, 1800);
  BH_KO_TT02_SP := LIMIT(320, BH_KO_TT02_SP, 1000);

  (* ---- confined-space entry: a locked output on at the top of the scan was
     written since the last one (the program leaves them off). ------------- *)
  BH_ENTRY_XA01_PV := BH_ENTRY_XS01_LOCKED AND (BH_ENTRY_XA01_PV OR BH_MLT_XV01_CMD
    OR BH_MLT_HS01_CMD OR BH_MLT_TV01_OUT > 0 OR BH_MLT_FV01_OUT > 0
    OR BH_MLT_FV02_OUT > 0 OR BH_MLT_SC01_OUT > 0);
  BH_ENTRY_XA02_PV := BH_ENTRY_XS02_LOCKED AND (BH_ENTRY_XA02_PV OR BH_KTL_XV01_CMD
    OR BH_KTL_XV02_CMD OR BH_KTL_TV01_OUT > 0);
  BH_ENTRY_XS01_LOCKED := BH_ENTRY_HS01_ENTRY;
  BH_ENTRY_XS02_LOCKED := BH_ENTRY_HS02_ENTRY;

  (* ---- the batch engine's accepted-request count goes to the panel -------------------- *)
  BH_BATCH_XI03_ACCEPTED := BH_BATCH_XC02_ACCEPTED;

  (* ---- phase commands -------------------------------------------------------------- *)
  cmd := BH_BATCH_XC01_CMD;
  (* Start and Restart of a phase that uses a locked-out vessel are refused. *)
  IF cmd = 1 THEN
    want := BH_BATCH_XC01_PHASE;
  ELSE
    want := active;
  END_IF;
  blocked_mlt := BH_ENTRY_XS01_LOCKED AND want >= 3 AND want <= 6;
  blocked_ktl := BH_ENTRY_XS02_LOCKED AND want >= 6 AND want <= 9;
  IF (cmd = 1 OR cmd = 3) AND (blocked_mlt OR blocked_ktl) THEN
    BH_ENTRY_XA01_PV := BH_ENTRY_XA01_PV OR blocked_mlt;
    BH_ENTRY_XA02_PV := BH_ENTRY_XA02_PV OR blocked_ktl;
    cmd := 0;
  END_IF;
  BH_BATCH_XC01_CMD := 0;
  IF BH_BATCH_XS01_STATE = 2 THEN
    BH_BATCH_XS01_STATE := 3;     (* Holding -> Held *)
  ELSIF BH_BATCH_XS01_STATE = 4 THEN
    BH_BATCH_XS01_STATE := 1;     (* Restarting -> Running *)
  END_IF;
  CASE cmd OF
    1:
      IF (BH_BATCH_XS01_STATE = 0 OR BH_BATCH_XS01_STATE = 5 OR BH_BATCH_XS01_STATE = 6)
          AND BH_BATCH_XC01_PHASE >= 1 AND BH_BATCH_XC01_PHASE <= 10 THEN
        active := BH_BATCH_XC01_PHASE;
        param := BH_BATCH_XC03_PARAM;
        (* The parameter in force: the engine's, or the program's default. *)
        param_min := MIN(param, PARAM_MAX_MIN);
        limit_ms := 0;
        boil_x10 := BOIL_MINUTES_X10;
        CASE active OF
          1:
            limit_ms := MILL_MS;
            grain_kg := GRAIN_BILL_KG;
            IF param > 0 THEN
              limit_ms := UINT_TO_UDINT(param) * MILL_MS_PER_KG;
              grain_kg := param;
            END_IF;
          3:
            strike_l := RECIPE_STRIKE_L;
            IF param > 0 THEN
              strike_l := MIN(param, STRIKE_MAX_L);
            END_IF;
            mash_in_level := UDINT_TO_UINT(MIN(
              (UINT_TO_UDINT(strike_l) * 1000 + UINT_TO_UDINT(grain_kg) * GRIST_ML_PER_KG) / MLT_VOLUME_L,
              900));
          4:
            limit_ms := MASH_HOLD_MS;
            IF param > 0 THEN
              limit_ms := UINT_TO_UDINT(param_min) * 60000;
            END_IF;
          5:
            limit_ms := VORLAUF_MS;
          7:
            IF param > 0 THEN
              boil_x10 := param_min * 10;
            END_IF;
          8:
            limit_ms := WHIRLPOOL_MS;
            IF param > 0 THEN
              limit_ms := UINT_TO_UDINT(param_min) * 60000;
            END_IF;
        END_CASE;
        BH_BATCH_XS01_STATE := 1;
        BH_BATCH_XS01_STEP := 1;
        step_ms := 0;
        IF active = 7 THEN
          BH_KTL_KI01_PV := 0;
        END_IF;
      END_IF;
    2:
      IF BH_BATCH_XS01_STATE = 1 THEN
        BH_BATCH_XS01_STATE := 2;
      END_IF;
    3:
      IF BH_BATCH_XS01_STATE = 3 THEN
        BH_BATCH_XS01_STATE := 4;
      END_IF;
    4:
      IF BH_BATCH_XS01_STATE >= 1 AND BH_BATCH_XS01_STATE <= 4 THEN
        BH_BATCH_XS01_STATE := 6;
      END_IF;
    5:
      IF BH_BATCH_XS01_STATE = 5 OR BH_BATCH_XS01_STATE = 6 THEN
        BH_BATCH_XS01_STATE := 0;
        BH_BATCH_XS01_STEP := 0;
        active := 0;
      END_IF;
  END_CASE;
  running := BH_BATCH_XS01_STATE = 1;

  (* ---- liquor tanks (whatever the phase) -------------------------------------------- *)
  IF BH_CLT_LT01_PV < CLT_LOW THEN
    clt_fill := TRUE;
  ELSIF BH_CLT_LT01_PV >= CLT_HIGH OR BH_CLT_LSH01_PV THEN
    clt_fill := FALSE;
  END_IF;
  BH_CLT_XV01_CMD := clt_fill;
  IF BH_HLT_LT01_PV < HLT_LOW THEN
    hlt_fill := TRUE;
  ELSIF BH_HLT_LT01_PV >= HLT_HIGH THEN
    hlt_fill := FALSE;
  END_IF;
  (* No cold liquor into the HLT while the strike liquor is drawn: it would chill
     the last of it, and the mash with it. HLT_HEAT has seen to the level. *)
  BH_HLT_XV01_CMD := hlt_fill AND BH_CLT_LT01_PV > 100 AND NOT (running AND active = 3);
  IF BH_HLT_LSL01_PV THEN
    BH_HLT_TV01_OUT := PValve(SP := BH_HLT_TT01_SP, PV := BH_HLT_TT01_PV, GAIN := 20);
  ELSE
    BH_HLT_TV01_OUT := 0;  (* low-level switch dropped: jacket uncovered, no steam *)
  END_IF;

  (* ---- boiling point and boil detection --------------------------------------------- *)
  p_kpa := UINT_TO_REAL(BH_KTL_PT01_PV) / 100.0 * 6.89476;
  IF p_kpa > 10.0 THEN
    t_boil_k := 1.0 / (1.0 / 373.15 - 8.314 * LN(p_kpa / 101.325) / 40660.0);
    t_boil_f := (t_boil_k - 273.15) * 1.8 + 32.0 + 0.5;
    BH_KTL_TB01_PV := REAL_TO_UINT(t_boil_f * 10.0);
  END_IF;
  BH_KTL_XS01_BOIL := BH_KTL_TB01_PV > 0 AND BH_KTL_TT01_PV + 5 >= BH_KTL_TB01_PV;
  (* The boil timer: tenths of a minute while boiling in the BOIL phase. *)
  boil_tick(IN := running AND active = 7 AND BH_KTL_XS01_BOIL AND NOT boil_tick.Q, PT := T#6s);
  IF boil_tick.Q THEN
    BH_KTL_KI01_PV := BH_KTL_KI01_PV + 1;
  END_IF;

  (* ---- phase logic: outputs default off, the running phase turns its own on -------- *)
  BH_MILL_HS01_CMD := FALSE;
  BH_MILL_HS02_CMD := FALSE;
  BH_MILL_SC01_SP := 0;
  BH_MLT_XV01_CMD := FALSE;
  BH_MLT_HS01_CMD := FALSE;
  BH_MLT_TV01_OUT := 0;
  BH_MLT_FV01_OUT := 0;
  BH_MLT_FV02_OUT := 0;
  BH_MLT_SC01_OUT := 0;
  BH_MLT_XS01_ACTIVE := FALSE;
  BH_KTL_XV01_CMD := FALSE;
  BH_KTL_XV02_CMD := FALSE;
  BH_KTL_TV01_OUT := 0;
  BH_KO_HS01_CMD := FALSE;
  BH_KO_XV01_CMD := FALSE;
  BH_KO_SC01_OUT := 0;
  BH_KO_TV01_OUT := 0;
  BH_KO_FC01_SP := 0;
  complete := FALSE;
  timing := FALSE;

  mash_ramp(IN := running AND active = 4, PT := MASH_RAMP_ALLOW);
  IF running THEN
    CASE active OF
      1:  (* MILL *)
        BH_MILL_HS01_CMD := TRUE;
        BH_MILL_HS02_CMD := TRUE;
        BH_MILL_SC01_SP := 500;
        timing := TRUE;
        complete := step_ms >= limit_ms;
      2:  (* HLT_HEAT: the HLT loop runs anyway; done at temperature and level *)
        complete := BH_HLT_TT01_PV + 10 >= BH_HLT_TT01_SP AND BH_HLT_LT01_PV >= HLT_LOW;
      3:  (* MASH_IN *)
        BH_MLT_XV01_CMD := TRUE;
        BH_MLT_HS01_CMD := TRUE;
        BH_MLT_SC01_OUT := 300;
        complete := BH_MLT_LT01_PV >= mash_in_level;
      4:  (* MASH_STEP: hold the rest temperature *)
        BH_MLT_HS01_CMD := TRUE;
        BH_MLT_SC01_OUT := 300;
        BH_MLT_TV01_OUT := PValve(SP := BH_MLT_TT01_SP, PV := BH_MLT_TT01_PV, GAIN := 30);
        mash_at_temp := BH_MLT_TT01_PV + 20 >= BH_MLT_TT01_SP;
        (* The deviation alarm is enabled at temperature, and after the ramp
           allowance wherever the mash is: a mash that never gets there (no
           steam) must alarm too, not wait in silence. *)
        BH_MLT_XS01_ACTIVE := mash_at_temp OR mash_ramp.Q;
        timing := mash_at_temp;  (* the rest counts only at temperature *)
        complete := step_ms >= limit_ms;
      5:  (* VORLAUF: recirculate until the runnings clear *)
        BH_MLT_FV01_OUT := 300;
        timing := TRUE;
        complete := step_ms >= limit_ms;
      6:  (* LAUTER: runoff and sparge; lower the rake when the bed packs *)
        BH_MLT_FV01_OUT := 600;
        BH_MLT_FV02_OUT := 500;
        BH_MLT_HS01_CMD := BH_MLT_PDT01_PV > BED_DP_HIGH;
        BH_MLT_SC01_OUT := 200;
        complete := BH_KTL_LT01_PV >= KETTLE_FULL;
      7:  (* BOIL *)
        BH_KTL_TV01_OUT := 800;
        complete := BH_KTL_KI01_PV >= boil_x10;
      8:  (* WHIRLPOOL, with the hop stand dose at the start *)
        BH_KTL_XV01_CMD := TRUE;
        BH_KTL_XV02_CMD := step_ms < HOP_DOSE_MS;
        timing := TRUE;
        complete := step_ms >= limit_ms;
      9:  (* KNOCKOUT: wort through the HX to the FV, O2 ratioed to flow *)
        BH_KO_HS01_CMD := TRUE;
        BH_KO_XV01_CMD := TRUE;
        BH_KO_SC01_OUT := 700;
        (* Stage-2 glycol opens as the wort runs warm of the setpoint. *)
        BH_KO_TV01_OUT := PValve(SP := BH_KO_TT02_PV, PV := BH_KO_TT02_SP, GAIN := 40);
        (* 0.04 L/min of O2 per gal/min of wort (FT01 is x 10, FC01 x 100):
           about 9 ppm dissolved, inside the 8 to 10 ppm target. *)
        BH_KO_FC01_SP := MIN(BH_KO_FT01_PV * 2 / 5, 1000);
        complete := BH_KTL_LT01_PV <= KETTLE_EMPTY;
      10: (* CIP_READY: everything off *)
        complete := TRUE;
    END_CASE;
  END_IF;
  (* ---- entry lockout: the locked vessel's outputs off, its phase held -------- *)
  IF BH_ENTRY_XS01_LOCKED THEN
    BH_MLT_XV01_CMD := FALSE;
    BH_MLT_HS01_CMD := FALSE;
    BH_MLT_TV01_OUT := 0;
    BH_MLT_FV01_OUT := 0;
    BH_MLT_FV02_OUT := 0;
    BH_MLT_SC01_OUT := 0;
  END_IF;
  IF BH_ENTRY_XS02_LOCKED THEN
    BH_KTL_XV01_CMD := FALSE;
    BH_KTL_XV02_CMD := FALSE;
    BH_KTL_TV01_OUT := 0;
  END_IF;
  IF running AND ((BH_ENTRY_XS01_LOCKED AND active >= 3 AND active <= 6)
      OR (BH_ENTRY_XS02_LOCKED AND active >= 6 AND active <= 9)) THEN
    BH_BATCH_XS01_STATE := 2;     (* Holding *)
    complete := FALSE;
    timing := FALSE;
  END_IF;
  IF timing THEN
    step_ms := step_ms + SCAN_MS;
  END_IF;
  IF complete THEN
    BH_BATCH_XS01_STATE := 5;
    BH_BATCH_XS01_STEP := 0;
  END_IF;

  (* ALARMS: plcbuild.py generates the alarm logic here. *)
END_PROGRAM
