#!/usr/bin/env python3
"""Log WireGuard peer events for Wazuh (DESIGN.md 13 and 14).

Installed on dmz-vpn as /usr/local/lib/cm-wireguard/cm_wg_watch.py and run by
cm-wg-watch.service. WireGuard keeps no log of its own, so this polls
``wg show <interface> dump`` and writes a syslog-style line for each change of a
peer, to the log file that the Wazuh agent reads (the dmz agent group) and to
standard output (the journal):

    Nov  9 12:00:00 dmz-vpn cm-wg-watch: event=peer_up interface=wg0 peer=7bqjO1NU endpoint=198.51.100.10:51820

Events (the cm-wg-watch decoder and rules 100200 to 100205 of the Wazuh rule set):

* ``peer_up``: a handshake that is not older than ``--down-after`` seconds, for a peer
  that was down, or a handshake less than ``REKEY_MIN`` seconds after the last one
  (WireGuard rekeys a live session after 120 s, so an earlier handshake is a client
  that started a new session). This is the vendor's tunnel coming up.
* ``peer_roam``: the peer's endpoint changed while it was up.
* ``peer_down``: no handshake for ``--down-after`` seconds (WireGuard drops a session
  after 180 s without one), or the interface disappeared.

Needs CAP_NET_ADMIN to read the interface, and the pydantic package.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

#: Seconds without a handshake after which a peer counts as down.
DOWN_AFTER: int = 200
#: A handshake sooner than this after the last one starts a new session; a rekey of a live
#: session happens after 120 s.
REKEY_MIN: int = 100
#: How often the interface is polled, in seconds.
POLL_INTERVAL: float = 5.0
#: Characters of the peer's public key that identify it in a log line.
PEER_ID_LENGTH: int = 8


class Peer(BaseModel):
    """One line of ``wg show <interface> dump`` after the interface line."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    public_key: str
    endpoint: str | None
    latest_handshake: int

    @property
    def ident(self) -> str:
        return self.public_key[:PEER_ID_LENGTH]


class Event(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    interface: str
    peer: str
    endpoint: str

    def line(self, hostname: str, when: float) -> str:
        stamp: str = time.strftime("%b %e %H:%M:%S", time.localtime(when))
        return f"{stamp} {hostname} cm-wg-watch: event={self.name} interface={self.interface} peer={self.peer} endpoint={self.endpoint}"


def parse_dump(text: str) -> list[Peer]:
    """The peers of a ``wg show <interface> dump`` output.

    The first line describes the interface (private key, public key, listen port,
    fwmark); each later line has the peer's public key, preshared key, endpoint,
    allowed IPs, latest handshake (Unix time, 0 for never), received and sent bytes,
    and the persistent keepalive, separated by tabs.
    """
    peers: list[Peer] = []
    for line in text.splitlines()[1:]:
        fields: list[str] = line.split("\t")
        if len(fields) < 5:
            continue
        endpoint: str | None = None if fields[2] in ("(none)", "") else fields[2]
        peers.append(Peer(public_key=fields[0], endpoint=endpoint, latest_handshake=int(fields[4])))
    return peers


class PeerState(BaseModel):
    up: bool = False
    endpoint: str = "none"
    handshake: int = 0
    ident: str = ""


class Watcher:
    """Turns successive snapshots of the peers into events."""

    def __init__(self, interface: str, down_after: int = DOWN_AFTER) -> None:
        self.interface: str = interface
        self.down_after: int = down_after
        self.state: dict[str, PeerState] = {}

    def step(self, peers: list[Peer], now: float) -> list[Event]:
        events: list[Event] = []
        for peer in peers:
            state: PeerState = self.state.setdefault(peer.public_key, PeerState())
            endpoint: str = peer.endpoint or "none"
            fresh: bool = peer.latest_handshake > 0 and now - peer.latest_handshake <= self.down_after
            restarted: bool = (
                state.up
                and peer.latest_handshake > state.handshake
                and peer.latest_handshake - state.handshake < REKEY_MIN
            )
            if fresh and (not state.up or restarted):
                state.up = True
                events.append(Event(name="peer_up", interface=self.interface, peer=peer.ident, endpoint=endpoint))
            elif fresh and endpoint != state.endpoint:
                events.append(Event(name="peer_roam", interface=self.interface, peer=peer.ident, endpoint=endpoint))
            elif not fresh and state.up:
                state.up = False
                events.append(Event(name="peer_down", interface=self.interface, peer=peer.ident, endpoint=endpoint))
            state.endpoint = endpoint
            state.handshake = max(state.handshake, peer.latest_handshake)
            state.ident = peer.ident
        return events

    def interface_gone(self) -> list[Event]:
        """The interface vanished (wg-quick down): every peer that was up is down."""
        events: list[Event] = []
        for state in self.state.values():
            if state.up:
                state.up = False
                events.append(Event(name="peer_down", interface=self.interface, peer=state.ident, endpoint=state.endpoint))
        return events


def read_dump(interface: str) -> str:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["wg", "show", interface, "dump"], capture_output=True, text=True, check=True, timeout=10
    )
    return result.stdout


def emit(log: Path, hostname: str, event: Event, when: float) -> None:
    line: str = event.line(hostname, when)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interface", default="wg0")
    parser.add_argument("--log", type=Path, default=Path("/var/log/cm-wireguard/events.log"))
    parser.add_argument("--down-after", type=int, default=DOWN_AFTER)
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL)
    args: argparse.Namespace = parser.parse_args(argv)

    hostname: str = socket.gethostname()
    watcher: Watcher = Watcher(args.interface, args.down_after)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            peers: list[Peer] = parse_dump(read_dump(args.interface))
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            # The interface does not exist until wg-quick@wg0 has started.
            print(f"cm-wg-watch: cannot read {args.interface}: {type(error).__name__}", file=sys.stderr, flush=True)
            peers = []
            lost: list[Event] = watcher.interface_gone()
            for event in lost:
                emit(args.log, hostname, event, time.time())
            time.sleep(args.interval)
            continue
        now: float = time.time()
        for event in watcher.step(peers, now):
            emit(args.log, hostname, event, now)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
