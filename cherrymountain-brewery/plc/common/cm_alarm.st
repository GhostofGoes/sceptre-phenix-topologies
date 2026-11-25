(*
  CmAlarm: one ISA-18.2 alarm with deadband, on-delay, off-delay, and
  state-based suppression (DESIGN.md 6.5). plcbuild.py prepends this block to
  every generated program and calls one instance per alarm in alarms.json.

  COND_ON    the value is past the limit (for example PV > LIMIT)
  COND_CLEAR the value is back inside the limit by the deadband
             (for example PV <= LIMIT - DEADBAND)
  SUPPRESS   TRUE while the alarm's suppression bit is 0 (alarms.json
             ``suppress``): the alarm is held off and its timers reset
  ON_DELAY   the condition must hold this long before the alarm raises
  OFF_DELAY  the alarm stays raised this long after the condition clears
*)
FUNCTION_BLOCK CmAlarm
  VAR_INPUT
    COND_ON : BOOL;
    COND_CLEAR : BOOL;
    SUPPRESS : BOOL;
    ON_DELAY : TIME;
    OFF_DELAY : TIME;
  END_VAR
  VAR_OUTPUT
    ALM : BOOL;
  END_VAR
  VAR
    beyond : BOOL;
    on_timer : TON;
    off_timer : TOF;
  END_VAR

  (* Deadband: past the limit sets, back inside by the deadband clears. *)
  IF COND_ON THEN
    beyond := TRUE;
  ELSIF COND_CLEAR THEN
    beyond := FALSE;
  END_IF;
  IF SUPPRESS THEN
    beyond := FALSE;
  END_IF;
  on_timer(IN := beyond, PT := ON_DELAY);
  off_timer(IN := on_timer.Q, PT := OFF_DELAY);
  ALM := off_timer.Q AND NOT SUPPRESS;
END_FUNCTION_BLOCK
