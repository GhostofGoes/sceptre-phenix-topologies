(*
  plc-packaging: CherryMountain Brewery packaging PLC, full and lite variants
  (DESIGN.md 6.3, 12.1). Built by plc/common/build_zip.sh.

  The line is a PackML unit over the machines' own PackML state machines (the
  OEM controllers on rio-packaging):
    - The operator's line command (PKG.LINE.PACKML.CMD, PackML CntrlCmd: 1 Reset,
      2 Start, 3 Stop, 4 Hold, 5 Unhold, 6 Suspend, 7 Unsuspend, 8 Abort,
      9 Clear) is taken once and cleared, so a command is an edge, not a
      level. It is held on every machine for CMD_SCANS scans and then zeroed:
      the modbus_master plugin samples the outputs once per poll (100 ms, not
      in step with the scan), so a one-scan pulse could fall between two
      polls and never reach the machines. The mode command and speed setpoint
      go to every machine every scan.
    - The operator panel at the filler (PKG.PANEL.HS01.PV) gives the same
      commands: the code of the button being pressed, 0 while none is. A press
      is one line command, taken when the code changes, so a held button does
      not repeat. A command from the HMI in the same scan goes first. This is
      how the line's operator starts and stops the shift.
    - The line state and mode are the filler's (the line's constraint machine);
      XS01.RUN is TRUE unless the line is Stopped (2) or Aborted (9), and it
      suppresses the jam alarms (ISA-18.2 state-based suppression).
    - The stop reason is the first machine, in line order, that is Held or
      Stopped with a jam.
    - The counts are the shift's: the filler's processed bottles and the rejects
      of every machine since the shift began. The machines' own counters are
      16 bits and never restart (they wrap after four shifts), so the program
      adds up what each has counted since the last scan. A shift begins when the
      line leaves Stopped or Aborted after SHIFT_GAP there (a stop in the
      middle of a shift is minutes, the night is hours).
    - OEE is performance times quality: filler speed over the setpoint, times
      the shift's good bottles over its processed ones.
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
    CMD_SCANS : UINT := 5;          (* a command stays on the machines 0.5 s *)
    KEG_WASH_ON : UINT := 1650;     (* 165.0 degF *)
    KEG_WASH_OFF : UINT := 1750;    (* 175.0 degF *)
    SHIFT_GAP : TIME := T#4h;       (* stopped this long, the next start is a new shift *)
    IO_SETTLE : TIME := T#5s;       (* the remote I/O has been read by now *)
  END_VAR

  VAR
    started : BOOL := FALSE;
    cmd : UINT := 0;                (* the command the machines are being given *)
    cmd_left : UINT := 0;           (* scans the command still stays on them *)
    panel_last : UINT := 0;         (* the panel's code at the last scan *)
    io_ready : TON;
    stopped_long : TON;
    new_shift : BOOL := FALSE;
    delta : DINT;
    last_processed : UINT;          (* the machines' counters at the last scan *)
    last_rinser : UINT;
    last_filler : UINT;
    last_coder : UINT;
    last_labeler : UINT;
    last_packer : UINT;
    shift_processed : UDINT := 0;   (* the shift's counts *)
    shift_defective : UDINT := 0;
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

  (* ---- operator panel: a button going down is one line command ------------------------- *)
  IF PKG_PANEL_HS01_PV <> panel_last THEN
    panel_last := PKG_PANEL_HS01_PV;
    IF panel_last >= 1 AND panel_last <= 9 AND PKG_LINE_PACKML_CMD = 0 THEN
      PKG_LINE_PACKML_CMD := panel_last;
    END_IF;
  END_IF;

  (* ---- line command: CMD_SCANS scans to every machine, then cleared -------------------- *)
  IF PKG_LINE_PACKML_CMD >= 1 AND PKG_LINE_PACKML_CMD <= 9 THEN
    cmd := PKG_LINE_PACKML_CMD;
    cmd_left := CMD_SCANS;
  ELSIF cmd_left > 0 THEN
    cmd_left := cmd_left - 1;
  ELSE
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
  (* The shift's counts: what each machine has counted since the last scan, its
     16-bit counter's wrap allowed for. Nothing is counted until the remote I/O
     has been read: the step from 0 to a machine's count is not production. *)
  io_ready(IN := TRUE, PT := IO_SETTLE);
  stopped_long(IN := NOT PKG_LINE_XS01_RUN, PT := SHIFT_GAP);
  IF stopped_long.Q THEN
    new_shift := TRUE;
  END_IF;
  IF new_shift AND PKG_LINE_XS01_RUN THEN
    shift_processed := 0;
    shift_defective := 0;
    new_shift := FALSE;
  END_IF;
  IF io_ready.Q THEN
    delta := UINT_TO_DINT(PKG_FILLER_PACKML_PROCESSED) - UINT_TO_DINT(last_processed);
    IF delta < 0 THEN delta := delta + 65536; END_IF;
    shift_processed := shift_processed + DINT_TO_UDINT(delta);
    delta := UINT_TO_DINT(PKG_RINSER_PACKML_DEFECTIVE) - UINT_TO_DINT(last_rinser);
    IF delta < 0 THEN delta := delta + 65536; END_IF;
    shift_defective := shift_defective + DINT_TO_UDINT(delta);
    delta := UINT_TO_DINT(PKG_FILLER_PACKML_DEFECTIVE) - UINT_TO_DINT(last_filler);
    IF delta < 0 THEN delta := delta + 65536; END_IF;
    shift_defective := shift_defective + DINT_TO_UDINT(delta);
    delta := UINT_TO_DINT(PKG_CODER_PACKML_DEFECTIVE) - UINT_TO_DINT(last_coder);
    IF delta < 0 THEN delta := delta + 65536; END_IF;
    shift_defective := shift_defective + DINT_TO_UDINT(delta);
    delta := UINT_TO_DINT(PKG_LABELER_PACKML_DEFECTIVE) - UINT_TO_DINT(last_labeler);
    IF delta < 0 THEN delta := delta + 65536; END_IF;
    shift_defective := shift_defective + DINT_TO_UDINT(delta);
    delta := UINT_TO_DINT(PKG_PACKER_PACKML_DEFECTIVE) - UINT_TO_DINT(last_packer);
    IF delta < 0 THEN delta := delta + 65536; END_IF;
    shift_defective := shift_defective + DINT_TO_UDINT(delta);
  END_IF;
  last_processed := PKG_FILLER_PACKML_PROCESSED;
  last_rinser := PKG_RINSER_PACKML_DEFECTIVE;
  last_filler := PKG_FILLER_PACKML_DEFECTIVE;
  last_coder := PKG_CODER_PACKML_DEFECTIVE;
  last_labeler := PKG_LABELER_PACKML_DEFECTIVE;
  last_packer := PKG_PACKER_PACKML_DEFECTIVE;
  PKG_LINE_PACKML_PROCESSED := UDINT_TO_UINT(MIN(shift_processed, 65535));
  PKG_LINE_PACKML_DEFECTIVE := UDINT_TO_UINT(MIN(shift_defective, 65535));
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
  IF shift_processed > 0 AND shift_processed >= shift_defective THEN
    quality := UDINT_TO_REAL(shift_processed - shift_defective) / UDINT_TO_REAL(shift_processed);
  ELSE
    quality := 1.0;   (* nothing filled yet this shift *)
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
