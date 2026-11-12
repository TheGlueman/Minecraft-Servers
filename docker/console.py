#!/usr/bin/env python3
"""Attached server console for docker.sh: the live log plus typed commands, like run.sh.

    console.py CONTAINER_ID {all|tail|none}

The second argument picks how much log to show: everything since the server started
(after "up"), the last lines ("attach"), or none ("console").

Commands typed here go to the server over RCON (a leading "/" is ignored), with
arrow-key history. Exit codes tell docker.sh what happened:

    0   detached (Ctrl+D or the window closed); the server keeps running
    10  shutdown asked for (Ctrl+C or "stop"); docker.sh does the shutdown and backup
    11  the server stopped cleanly or was removed, from outside this console
    12  the server crashed and Docker is not restarting it
"""

import os
import signal
import subprocess
import sys
import threading
import time

try:
    import readline  # noqa: F401  (gives input() line editing and history)
except ImportError:
    pass

DETACHED, SHUTDOWN, STOPPED, CRASHED = 0, 10, 11, 12
POLL_SECONDS = 2


class ServerGone(Exception):
    """Raised in the main thread when the monitor sees the server has stopped."""


class Detach(Exception):
    """Raised in the main thread when the terminal goes away (SIGHUP)."""


def say(text: str) -> None:
    try:
        print(f"\n*** {text}", flush=True)
    except OSError:
        pass  # the terminal is gone


def inspect(cid: str) -> tuple[str, int, int, str] | None:
    """Container (status, exit code, restart count, started-at), or None if it is gone."""
    result = subprocess.run(
        ["docker", "inspect", "-f",
         "{{.State.Status}} {{.State.ExitCode}} {{.RestartCount}} {{.State.StartedAt}}", cid],
        capture_output=True, text=True)
    if result.returncode != 0:
        return None
    status, code, restarts, started = result.stdout.split()
    return status, int(code), int(restarts), started


# Log lines every RCON command adds (each command typed here, and the backups' save-off/on).
NOISE = ("Thread RCON Client",)


def show_log(stream) -> None:
    for line in stream:
        if not any(noise in line for noise in NOISE):
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except OSError:
                return  # the terminal is gone


def follow_log(cid: str, since: str | None, tail: int | None) -> subprocess.Popen:
    args = ["docker", "logs", "-f"]
    if since:
        args += ["--since", since]
    if tail is not None:
        args += ["--tail", str(tail)]
    # Its own session, so Ctrl+C in this terminal doesn't stop the log.
    process = subprocess.Popen(args + [cid], start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace")
    threading.Thread(target=show_log, args=(process.stdout,), daemon=True).start()
    return process


class Monitor(threading.Thread):
    """Shows the log (restarting it across server restarts) and watches for the server stopping."""

    def __init__(self, cid: str, logs: str) -> None:
        super().__init__(daemon=True)
        self.cid = cid
        self.logs = logs
        self.result: int | None = None
        self.stop_event = threading.Event()
        self.follower: subprocess.Popen | None = None

    def stop_log(self) -> None:
        if self.follower and self.follower.poll() is None:
            self.follower.terminate()

    def run(self) -> None:
        last_started = last_restarts = None
        stopped_seen = 0
        announced_restart = False
        while not self.stop_event.is_set():
            info = inspect(self.cid)
            # Removed = taken down from elsewhere (docker.sh down), not a crash.
            status, code, restarts, started = info or ("removed", 0, 0, "")
            if status == "running":
                stopped_seen = 0
                announced_restart = False
                if last_restarts is not None and restarts > last_restarts:
                    say(f"The server crashed and Docker restarted it (restart {restarts}).")
                new_start = started != last_started
                if self.logs != "none" and (new_start or self.follower is None
                                            or self.follower.poll() is not None):
                    self.stop_log()
                    if last_started is None and self.logs == "tail":
                        self.follower = follow_log(self.cid, None, 30)
                    else:
                        # From this start on (a restart, or "up" showing the whole startup).
                        self.follower = follow_log(self.cid, started, None)
                last_started, last_restarts = started, restarts
            elif status == "restarting":
                if not announced_restart:
                    say("The server crashed; Docker is restarting it...")
                    announced_restart = True
            else:
                # Seen twice in a row, so the moment between a crash and Docker's restart
                # isn't mistaken for the server staying down.
                stopped_seen += 1
                if stopped_seen >= 2:
                    self.result = STOPPED if code == 0 else CRASHED
                    self.stop_log()
                    os.kill(os.getpid(), signal.SIGUSR1)
                    return
            self.stop_event.wait(POLL_SECONDS)
        self.stop_log()


def raise_gone(*_args: object) -> None:
    raise ServerGone


def raise_detach(*_args: object) -> None:
    raise Detach


def run_command(cid: str, command: str) -> None:
    result = subprocess.run(["docker", "exec", cid, "rcon-cli", command],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    reply = (result.stdout + result.stderr).strip()
    if reply:
        print(reply, flush=True)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in ("all", "tail", "none"):
        print(__doc__, file=sys.stderr)
        return 2
    cid, logs = sys.argv[1], sys.argv[2]

    signal.signal(signal.SIGUSR1, raise_gone)
    signal.signal(signal.SIGHUP, raise_detach)
    monitor = Monitor(cid, logs)
    monitor.start()
    print("Type server commands here (no /). Ctrl+C or \"stop\": shut down with a backup. "
          "Ctrl+D: detach (the server keeps running).", flush=True)
    try:
        while True:
            try:
                line = input()
            except KeyboardInterrupt:
                return SHUTDOWN
            command = line.strip().lstrip("/").strip()
            if not command:
                continue
            if command.lower() == "stop":
                return SHUTDOWN
            try:
                run_command(cid, command)
            except KeyboardInterrupt:
                return SHUTDOWN
    except EOFError:
        return DETACHED
    except Detach:
        return DETACHED
    except ServerGone:
        return monitor.result if monitor.result is not None else CRASHED
    finally:
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
        monitor.stop_event.set()
        monitor.stop_log()


if __name__ == "__main__":
    sys.exit(main())
