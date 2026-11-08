PROGRAM main
  VAR
    cnt AT %QW0 : INT;
    inp AT %IW0 : INT;
    run AT %QX0.0 : BOOL;
  END_VAR
  cnt := cnt + 1;
  run := inp > 0;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : main;
  END_RESOURCE
END_CONFIGURATION
