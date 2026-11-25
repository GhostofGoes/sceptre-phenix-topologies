#!/bin/bash
# phenix startup hook on the historian and sim-ctl: put the brewery's images into Grafana
# (cm-grafana-brand). Grafana reads these files per request, so it needs no restart.
/usr/local/bin/cm-grafana-brand
