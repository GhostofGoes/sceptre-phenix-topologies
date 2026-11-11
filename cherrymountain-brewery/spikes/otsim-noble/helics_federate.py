#!/usr/bin/env python3
"""Smoke test: run a HELICS broker and two Python federates on noble.

Usage: helics_federate.py            (uses the venv that has `helics` installed)
Install: pip install helics
Passes when federate B receives the value federate A published.
"""
import sys
import threading
import time

import helics as h


def broker() -> "h.HelicsBroker":
    return h.helicsCreateBroker("zmq", "spike-broker", "--federates=2 --loglevel=warning")


def make_fed(name: str):
    fi = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fi, "zmq")
    h.helicsFederateInfoSetCoreInitString(fi, "--federates=1")
    h.helicsFederateInfoSetTimeProperty(fi, h.HELICS_PROPERTY_TIME_PERIOD, 1.0)
    return h.helicsCreateValueFederate(name, fi)


def main() -> int:
    print("helics version:", h.helicsGetVersion())
    b = broker()
    got = {}

    def fed_a() -> None:
        f = make_fed("fedA")
        pub = h.helicsFederateRegisterGlobalPublication(f, "spike/temp", h.HELICS_DATA_TYPE_DOUBLE, "")
        h.helicsFederateEnterExecutingMode(f)
        h.helicsPublicationPublishDouble(pub, 42.5)
        h.helicsFederateRequestTime(f, 2.0)
        h.helicsFederateDisconnect(f)

    def fed_b() -> None:
        f = make_fed("fedB")
        sub = h.helicsFederateRegisterSubscription(f, "spike/temp", "")
        h.helicsFederateEnterExecutingMode(f)
        t = h.helicsFederateRequestTime(f, 2.0)
        got["v"] = h.helicsInputGetDouble(sub)
        got["t"] = t
        h.helicsFederateDisconnect(f)

    ts = [threading.Thread(target=fed_a), threading.Thread(target=fed_b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    time.sleep(0.5)
    h.helicsBrokerDisconnect(b)
    print("received:", got)
    ok = got.get("v") == 42.5
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
