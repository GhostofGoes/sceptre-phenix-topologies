(*
  plc-cellar: CherryMountain Brewery cellar PLC (ci variant, DESIGN.md 12.1).

  Per fermenter FV-101 .. FV-104 this program runs:
    - on/off glycol valve control with hysteresis on the side and cone zones
    - a spunding valve pressure setpoint, written to the RIO every scan
    - ISA-18.2 alarm evaluation with on-delay timers (limits and delays below
      come from plant/generated/ci/alarms.json; assemble.py checks they match)

  Units: every analog value is a raw unsigned register, engineering value x 100
  (degF for temperatures, psia for pressures). Setpoints in %MW are written by
  the operator (FUXA, Modbus holding registers from qw_count, see
  modbus_slave.json); the PLC loads defaults once at start-up and clamps them
  to the plant range afterwards.

  The located variables (the %IW/%QX/%QW/%MW map) are not written here. They
  come from plant/generated/ci/openplc/plc-cellar/located_vars.st, and
  assemble.py splices that VAR block in at the marker comment in plc_cellar.
  Do not hand-edit addresses: change plant/plant.yaml and rerun plantgen.
*)

(* One glycol zone: on/off valve with hysteresis plus its temperature alarms. *)
FUNCTION_BLOCK ZoneControl
  VAR_INPUT
    PV : UINT;                 (* temperature, degF x 100 *)
    SP : UINT;                 (* setpoint, degF x 100 *)
    HYST : UINT := 50;         (* valve hysteresis half band, 0.5 degF *)
    DEV_LIMIT : UINT;          (* deviation alarm limit, degF x 100 *)
    DEV_DELAY : TIME;          (* deviation alarm on-delay *)
    HIGH_LIMIT : UINT;         (* high alarm limit, degF x 100; 0 = no high alarm *)
    HIGH_DELAY : TIME;         (* high alarm on-delay *)
  END_VAR
  VAR_OUTPUT
    VALVE : BOOL;              (* glycol valve command, TRUE = open (cooling) *)
    ALM_DEV : BOOL;
    ALM_HIGH : BOOL;
  END_VAR
  VAR
    dev_timer : TON;
    high_timer : TON;
    err : DINT;
    dev_cond : BOOL;
    high_cond : BOOL;
  END_VAR

  (* Cooling only: open above SP + HYST, close below SP - HYST, hold between. *)
  IF PV >= SP + HYST THEN
    VALVE := TRUE;
  ELSIF PV + HYST <= SP THEN
    VALVE := FALSE;
  END_IF;

  err := UINT_TO_DINT(PV) - UINT_TO_DINT(SP);
  dev_cond := ABS(err) > UINT_TO_DINT(DEV_LIMIT);
  dev_timer(IN := dev_cond, PT := DEV_DELAY);
  ALM_DEV := dev_timer.Q;

  high_cond := (HIGH_LIMIT > 0) AND (PV > HIGH_LIMIT);
  high_timer(IN := high_cond, PT := HIGH_DELAY);
  ALM_HIGH := high_timer.Q;
END_FUNCTION_BLOCK

(* Head pressure (absolute) high and low alarms for one fermenter. *)
FUNCTION_BLOCK PressureAlarm
  VAR_INPUT
    PV : UINT;                 (* psia x 100 *)
    HIGH_LIMIT : UINT;
    HIGH_DELAY : TIME;
    LOW_LIMIT : UINT;
    LOW_DELAY : TIME;
  END_VAR
  VAR_OUTPUT
    ALM_HIGH : BOOL;
    ALM_LOW : BOOL;
  END_VAR
  VAR
    high_timer : TON;
    low_timer : TON;
  END_VAR

  high_timer(IN := PV > HIGH_LIMIT, PT := HIGH_DELAY);
  ALM_HIGH := high_timer.Q;
  low_timer(IN := PV < LOW_LIMIT, PT := LOW_DELAY);
  ALM_LOW := low_timer.Q;
END_FUNCTION_BLOCK

PROGRAM plc_cellar
  (* LOCATED_VARS: assemble.py splices located_vars.st here. *)

  VAR CONSTANT
    (* Alarm limits and delays: keep equal to plant/generated/ci/alarms.json. *)
    TEMP_DEV_LIMIT : UINT := 200;       (* 2.0 degF *)
    TEMP_DEV_DELAY_SIDE : TIME := T#900s;
    TEMP_DEV_DELAY_CONE : TIME := T#900s;
    TEMP_HIGH_LIMIT : UINT := 7500;     (* 75.0 degF, side zone only *)
    TEMP_HIGH_DELAY : TIME := T#300s;
    PRESS_HIGH_LIMIT : UINT := 2610;    (* 26.1 psia *)
    PRESS_HIGH_DELAY : TIME := T#30s;
    PRESS_LOW_LIMIT : UINT := 1160;     (* 11.6 psia *)
    PRESS_LOW_DELAY : TIME := T#300s;
    (* Operator setpoint range, from plant.yaml (20 .. 90 degF). *)
    TEMP_SP_MIN : UINT := 2000;
    TEMP_SP_MAX : UINT := 9000;
    (* Site ambient pressure, 12.1 psia (5200 ft). *)
    AMBIENT_PSIA : UINT := 1210;
  END_VAR

  VAR
    started : BOOL := FALSE;
    (* Spunding setpoints, psig x 100. They follow the ci initial state in
       plant.yaml: FV-101/102 early fermentation, FV-103/104 sealed. *)
    spund_fv101_psig : UINT := 100;
    spund_fv102_psig : UINT := 100;
    spund_fv103_psig : UINT := 1200;
    spund_fv104_psig : UINT := 1200;

    fv101_side : ZoneControl;
    fv101_cone : ZoneControl;
    fv101_press : PressureAlarm;
    fv102_side : ZoneControl;
    fv102_cone : ZoneControl;
    fv102_press : PressureAlarm;
    fv103_side : ZoneControl;
    fv103_cone : ZoneControl;
    fv103_press : PressureAlarm;
    fv104_side : ZoneControl;
    fv104_cone : ZoneControl;
    fv104_press : PressureAlarm;
  END_VAR

  (* First scan: load default setpoints (the ci plant starts mid-fermentation). *)
  IF NOT started THEN
    CEL_FV101_TT01_SP := 6600;
    CEL_FV101_TT02_SP := 6600;
    CEL_FV102_TT01_SP := 6600;
    CEL_FV102_TT02_SP := 6600;
    CEL_FV103_TT01_SP := 6800;
    CEL_FV103_TT02_SP := 6800;
    CEL_FV104_TT01_SP := 5000;
    CEL_FV104_TT02_SP := 5000;
    started := TRUE;
  END_IF;

  (* Keep operator-written setpoints inside the plant range. *)
  CEL_FV101_TT01_SP := LIMIT(TEMP_SP_MIN, CEL_FV101_TT01_SP, TEMP_SP_MAX);
  CEL_FV101_TT02_SP := LIMIT(TEMP_SP_MIN, CEL_FV101_TT02_SP, TEMP_SP_MAX);
  CEL_FV102_TT01_SP := LIMIT(TEMP_SP_MIN, CEL_FV102_TT01_SP, TEMP_SP_MAX);
  CEL_FV102_TT02_SP := LIMIT(TEMP_SP_MIN, CEL_FV102_TT02_SP, TEMP_SP_MAX);
  CEL_FV103_TT01_SP := LIMIT(TEMP_SP_MIN, CEL_FV103_TT01_SP, TEMP_SP_MAX);
  CEL_FV103_TT02_SP := LIMIT(TEMP_SP_MIN, CEL_FV103_TT02_SP, TEMP_SP_MAX);
  CEL_FV104_TT01_SP := LIMIT(TEMP_SP_MIN, CEL_FV104_TT01_SP, TEMP_SP_MAX);
  CEL_FV104_TT02_SP := LIMIT(TEMP_SP_MIN, CEL_FV104_TT02_SP, TEMP_SP_MAX);

  (* FV-101 *)
  fv101_side(PV := CEL_FV101_TT01_PV, SP := CEL_FV101_TT01_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_SIDE,
    HIGH_LIMIT := TEMP_HIGH_LIMIT, HIGH_DELAY := TEMP_HIGH_DELAY);
  CEL_FV101_TV01_CMD := fv101_side.VALVE;
  CEL_FV101_TT01_ALM_DEVIATION := fv101_side.ALM_DEV;
  CEL_FV101_TT01_ALM_HIGH := fv101_side.ALM_HIGH;

  fv101_cone(PV := CEL_FV101_TT02_PV, SP := CEL_FV101_TT02_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_CONE,
    HIGH_LIMIT := 0, HIGH_DELAY := T#0s);
  CEL_FV101_TV02_CMD := fv101_cone.VALVE;
  CEL_FV101_TT02_ALM_DEVIATION := fv101_cone.ALM_DEV;

  fv101_press(PV := CEL_FV101_PT01_PV,
    HIGH_LIMIT := PRESS_HIGH_LIMIT, HIGH_DELAY := PRESS_HIGH_DELAY,
    LOW_LIMIT := PRESS_LOW_LIMIT, LOW_DELAY := PRESS_LOW_DELAY);
  CEL_FV101_PT01_ALM_HIGH := fv101_press.ALM_HIGH;
  CEL_FV101_PT01_ALM_LOW := fv101_press.ALM_LOW;

  (* Spunding setpoint to the RIO, absolute psia x 100, every scan. *)
  CEL_FV101_PCV01_SP := spund_fv101_psig + AMBIENT_PSIA;

  (* FV-102 *)
  fv102_side(PV := CEL_FV102_TT01_PV, SP := CEL_FV102_TT01_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_SIDE,
    HIGH_LIMIT := TEMP_HIGH_LIMIT, HIGH_DELAY := TEMP_HIGH_DELAY);
  CEL_FV102_TV01_CMD := fv102_side.VALVE;
  CEL_FV102_TT01_ALM_DEVIATION := fv102_side.ALM_DEV;
  CEL_FV102_TT01_ALM_HIGH := fv102_side.ALM_HIGH;

  fv102_cone(PV := CEL_FV102_TT02_PV, SP := CEL_FV102_TT02_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_CONE,
    HIGH_LIMIT := 0, HIGH_DELAY := T#0s);
  CEL_FV102_TV02_CMD := fv102_cone.VALVE;
  CEL_FV102_TT02_ALM_DEVIATION := fv102_cone.ALM_DEV;

  fv102_press(PV := CEL_FV102_PT01_PV,
    HIGH_LIMIT := PRESS_HIGH_LIMIT, HIGH_DELAY := PRESS_HIGH_DELAY,
    LOW_LIMIT := PRESS_LOW_LIMIT, LOW_DELAY := PRESS_LOW_DELAY);
  CEL_FV102_PT01_ALM_HIGH := fv102_press.ALM_HIGH;
  CEL_FV102_PT01_ALM_LOW := fv102_press.ALM_LOW;

  (* Spunding setpoint to the RIO, absolute psia x 100, every scan. *)
  CEL_FV102_PCV01_SP := spund_fv102_psig + AMBIENT_PSIA;

  (* FV-103 *)
  fv103_side(PV := CEL_FV103_TT01_PV, SP := CEL_FV103_TT01_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_SIDE,
    HIGH_LIMIT := TEMP_HIGH_LIMIT, HIGH_DELAY := TEMP_HIGH_DELAY);
  CEL_FV103_TV01_CMD := fv103_side.VALVE;
  CEL_FV103_TT01_ALM_DEVIATION := fv103_side.ALM_DEV;
  CEL_FV103_TT01_ALM_HIGH := fv103_side.ALM_HIGH;

  fv103_cone(PV := CEL_FV103_TT02_PV, SP := CEL_FV103_TT02_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_CONE,
    HIGH_LIMIT := 0, HIGH_DELAY := T#0s);
  CEL_FV103_TV02_CMD := fv103_cone.VALVE;
  CEL_FV103_TT02_ALM_DEVIATION := fv103_cone.ALM_DEV;

  fv103_press(PV := CEL_FV103_PT01_PV,
    HIGH_LIMIT := PRESS_HIGH_LIMIT, HIGH_DELAY := PRESS_HIGH_DELAY,
    LOW_LIMIT := PRESS_LOW_LIMIT, LOW_DELAY := PRESS_LOW_DELAY);
  CEL_FV103_PT01_ALM_HIGH := fv103_press.ALM_HIGH;
  CEL_FV103_PT01_ALM_LOW := fv103_press.ALM_LOW;

  (* Spunding setpoint to the RIO, absolute psia x 100, every scan. *)
  CEL_FV103_PCV01_SP := spund_fv103_psig + AMBIENT_PSIA;

  (* FV-104 *)
  fv104_side(PV := CEL_FV104_TT01_PV, SP := CEL_FV104_TT01_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_SIDE,
    HIGH_LIMIT := TEMP_HIGH_LIMIT, HIGH_DELAY := TEMP_HIGH_DELAY);
  CEL_FV104_TV01_CMD := fv104_side.VALVE;
  CEL_FV104_TT01_ALM_DEVIATION := fv104_side.ALM_DEV;
  CEL_FV104_TT01_ALM_HIGH := fv104_side.ALM_HIGH;

  fv104_cone(PV := CEL_FV104_TT02_PV, SP := CEL_FV104_TT02_SP,
    DEV_LIMIT := TEMP_DEV_LIMIT, DEV_DELAY := TEMP_DEV_DELAY_CONE,
    HIGH_LIMIT := 0, HIGH_DELAY := T#0s);
  CEL_FV104_TV02_CMD := fv104_cone.VALVE;
  CEL_FV104_TT02_ALM_DEVIATION := fv104_cone.ALM_DEV;

  fv104_press(PV := CEL_FV104_PT01_PV,
    HIGH_LIMIT := PRESS_HIGH_LIMIT, HIGH_DELAY := PRESS_HIGH_DELAY,
    LOW_LIMIT := PRESS_LOW_LIMIT, LOW_DELAY := PRESS_LOW_DELAY);
  CEL_FV104_PT01_ALM_HIGH := fv104_press.ALM_HIGH;
  CEL_FV104_PT01_ALM_LOW := fv104_press.ALM_LOW;

  (* Spunding setpoint to the RIO, absolute psia x 100, every scan. *)
  CEL_FV104_PCV01_SP := spund_fv104_psig + AMBIENT_PSIA;
END_PROGRAM
