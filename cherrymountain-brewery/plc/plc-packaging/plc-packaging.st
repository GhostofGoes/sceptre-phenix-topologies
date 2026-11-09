(*
  plc-packaging: CherryMountain Brewery packaging PLC, full and lite variants
  (DESIGN.md 6.3, 12.1). Built by plc/common/build_zip.sh.

  The line is a PackML unit over the machines' own PackML state machines (the
  OEM controllers on rio-packaging):
    - The operator's line command (PKG.LINE.PACKML.CMD, PackML CntrlCmd: 1 Reset,
      2 Start, 3 Stop, 4 Hold, 5 Unhold, 6 Suspend, 7 Unsuspend, 8 Abort,
      9 Clear) goes to every machine for one scan and is then cleared, so a
      command is an edge, not a level. The mode command and speed setpoint go
      to every machine every scan.
    - The line state and mode are the filler's (the line's constraint machine);
      XS01.RUN is TRUE unless the line is Stopped (2) or Aborted (9), and it
      suppresses the jam alarms (ISA-18.2 state-based suppression).
    - The stop reason is the first machine, in line order, that is Held or
      Stopped with a jam; the counts are the filler's processed bottles and the
      rejects of every machine.
    - OEE is performance times quality: filler speed over the setpoint, times
      good bottles over processed.
    - Conveyors run while the machine they feed executes and their jam eye is
      clear; the filler's product valve is open while it executes.
    - The keg washer follows the line commands, and its wash-water heater
      holds 170 degF.
  Alarms are generated from alarms.json by plcbuild.py at the ALARMS marker.

  Units: raw registers, PackML codes as-is, speeds in bottles/min x 10.
*)

PROGRAM plc_packaging
  (* LOCATED_VARS: plcbuild.py splices the located variables and alarm instances here. *)

  VAR CONSTANT
    STATE_STOPPED : UINT := 2;
    STATE_EXECUTE : UINT := 6;
    STATE_ABORTED : UINT := 9;
    STATE_HELD : UINT := 11;
    MODE_PRODUCTION : UINT := 1;
    SPEED_MAX : UINT := 600;        (* 60 bottles/min *)
    KEG_WASH_ON : UINT := 1650;     (* 165.0 degF *)
    KEG_WASH_OFF : UINT := 1750;    (* 175.0 degF *)
  END_VAR

  VAR
    started : BOOL := FALSE;
    cmd : UINT;
    defective : UDINT;
    perf : REAL;
    quality : REAL;
    keg_heat : BOOL;
  END_VAR

  IF NOT started THEN
    PKG_LINE_PACKML_CMD := 0;
    PKG_LINE_PACKML_MODE_CMD := MODE_PRODUCTION;
    PKG_LINE_PACKML_SPEED_SP := 300;     (* 30 bottles/min, about 1,800 per hour *)
    started := TRUE;
  END_IF;
  PKG_LINE_PACKML_SPEED_SP := MIN(PKG_LINE_PACKML_SPEED_SP, SPEED_MAX);
  PKG_LINE_PACKML_MODE_CMD := LIMIT(1, PKG_LINE_PACKML_MODE_CMD, 3);

  (* ---- line command: one scan to every machine, then cleared --------------------------- *)
  cmd := PKG_LINE_PACKML_CMD;
  IF cmd > 9 THEN
    cmd := 0;
  END_IF;
  PKG_LINE_PACKML_CMD := 0;
  PKG_RINSER_PACKML_CMD := cmd;
  PKG_FILLER_PACKML_CMD := cmd;
  PKG_CODER_PACKML_CMD := cmd;
  PKG_LABELER_PACKML_CMD := cmd;
  PKG_PACKER_PACKML_CMD := cmd;
  PKG_KEG_PACKML_CMD := cmd;
  PKG_RINSER_PACKML_MODE_CMD := PKG_LINE_PACKML_MODE_CMD;
  PKG_FILLER_PACKML_MODE_CMD := PKG_LINE_PACKML_MODE_CMD;
  PKG_CODER_PACKML_MODE_CMD := PKG_LINE_PACKML_MODE_CMD;
  PKG_LABELER_PACKML_MODE_CMD := PKG_LINE_PACKML_MODE_CMD;
  PKG_PACKER_PACKML_MODE_CMD := PKG_LINE_PACKML_MODE_CMD;
  PKG_KEG_PACKML_MODE_CMD := PKG_LINE_PACKML_MODE_CMD;
  PKG_RINSER_PACKML_SPEED_SP := PKG_LINE_PACKML_SPEED_SP;
  PKG_FILLER_PACKML_SPEED_SP := PKG_LINE_PACKML_SPEED_SP;
  PKG_CODER_PACKML_SPEED_SP := PKG_LINE_PACKML_SPEED_SP;
  PKG_LABELER_PACKML_SPEED_SP := PKG_LINE_PACKML_SPEED_SP;
  PKG_PACKER_PACKML_SPEED_SP := PKG_LINE_PACKML_SPEED_SP;

  (* ---- line state, counts, stop reason ---------------------------------------------- *)
  PKG_LINE_PACKML_STATE := PKG_FILLER_PACKML_STATE;
  PKG_LINE_PACKML_MODE := PKG_FILLER_PACKML_MODE;
  PKG_LINE_XS01_RUN := (PKG_LINE_PACKML_STATE <> STATE_STOPPED)
    AND (PKG_LINE_PACKML_STATE <> STATE_ABORTED);
  PKG_LINE_PACKML_PROCESSED := PKG_FILLER_PACKML_PROCESSED;
  defective := UINT_TO_UDINT(PKG_RINSER_PACKML_DEFECTIVE) + UINT_TO_UDINT(PKG_FILLER_PACKML_DEFECTIVE)
    + UINT_TO_UDINT(PKG_CODER_PACKML_DEFECTIVE) + UINT_TO_UDINT(PKG_LABELER_PACKML_DEFECTIVE)
    + UINT_TO_UDINT(PKG_PACKER_PACKML_DEFECTIVE);
  PKG_LINE_PACKML_DEFECTIVE := UDINT_TO_UINT(MIN(defective, 65535));
  (* First-out: 1 rinser, 2 filler, 3 coder, 4 labeler, 5 packer, 6 keg; 0 none. *)
  IF PKG_RINSER_YA01_PV OR PKG_RINSER_PACKML_STATE = STATE_HELD THEN
    PKG_LINE_PACKML_STOP_REASON := 1;
  ELSIF PKG_FILLER_YA01_PV OR PKG_FILLER_PACKML_STATE = STATE_HELD THEN
    PKG_LINE_PACKML_STOP_REASON := 2;
  ELSIF PKG_CODER_YA01_PV OR PKG_CODER_PACKML_STATE = STATE_HELD THEN
    PKG_LINE_PACKML_STOP_REASON := 3;
  ELSIF PKG_LABELER_YA01_PV OR PKG_LABELER_PACKML_STATE = STATE_HELD THEN
    PKG_LINE_PACKML_STOP_REASON := 4;
  ELSIF PKG_PACKER_YA01_PV OR PKG_PACKER_PACKML_STATE = STATE_HELD THEN
    PKG_LINE_PACKML_STOP_REASON := 5;
  ELSIF PKG_KEG_YA01_PV OR PKG_KEG_PACKML_STATE = STATE_HELD THEN
    PKG_LINE_PACKML_STOP_REASON := 6;
  ELSE
    PKG_LINE_PACKML_STOP_REASON := 0;
  END_IF;

  (* ---- OEE: performance x quality, % x 10 -------------------------------------------- *)
  IF PKG_LINE_PACKML_SPEED_SP > 0 THEN
    perf := MIN(UINT_TO_REAL(PKG_FILLER_PACKML_SPEED) / UINT_TO_REAL(PKG_LINE_PACKML_SPEED_SP), 1.0);
  ELSE
    perf := 0.0;
  END_IF;
  IF PKG_LINE_PACKML_PROCESSED > 0 AND PKG_LINE_PACKML_PROCESSED >= PKG_LINE_PACKML_DEFECTIVE THEN
    quality := UINT_TO_REAL(PKG_LINE_PACKML_PROCESSED - PKG_LINE_PACKML_DEFECTIVE)
      / UINT_TO_REAL(PKG_LINE_PACKML_PROCESSED);
  ELSE
    quality := 1.0;
  END_IF;
  PKG_LINE_KQ01_OEE := REAL_TO_UINT(perf * quality * 1000.0);

  (* ---- conveyors and the filler's product valve ------------------------------------- *)
  PKG_CONV_HS01_CMD := PKG_RINSER_PACKML_STATE = STATE_EXECUTE AND NOT PKG_CONV_YA01_PV;
  PKG_CONV_HS02_CMD := PKG_CODER_PACKML_STATE = STATE_EXECUTE AND NOT PKG_CONV_YA02_PV;
  PKG_CONV_HS03_CMD := PKG_PACKER_PACKML_STATE = STATE_EXECUTE AND NOT PKG_CONV_YA03_PV;
  PKG_CONV_HS04_CMD := PKG_PACKER_PACKML_STATE = STATE_EXECUTE AND NOT PKG_CONV_YA04_PV;
  PKG_FILLER_XV01_CMD := PKG_FILLER_PACKML_STATE = STATE_EXECUTE;

  (* ---- keg washer heater ------------------------------------------------------------ *)
  IF PKG_KEG_TT01_PV < KEG_WASH_ON THEN
    keg_heat := TRUE;
  ELSIF PKG_KEG_TT01_PV >= KEG_WASH_OFF THEN
    keg_heat := FALSE;
  END_IF;
  PKG_KEG_HS01_CMD := keg_heat;

  (* ALARMS: plcbuild.py generates the alarm logic here. *)
END_PROGRAM
