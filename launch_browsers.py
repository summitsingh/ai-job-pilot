#!/usr/bin/env python3
"""Launch N headful Chrome browsers for job-application lanes.

Interactive:
  python3 launch_browsers.py
  -> "How many Chrome browsers do you want to open for job applications?
      (recommended: 2)"

Non-interactive:
  python3 launch_browsers.py --lanes 2 --base-port 9445
  python3 launch_browsers.py --lanes 3 --purpose greenhouse --purpose other \
      --purpose research --dry-run

Every browser is HEADFUL (never headless, hard requirement), visible on
the machine's main screen, with its own remote-debugging port and its
own profile directory. Lane 1 defaults to Greenhouse, lane 2 to other
boards (Ashby/Lever/Wellfound/direct); lane 3+ ask for a purpose
interactively or take --purpose flags.

Platform recipes:
  macOS   open -n -a "Google Chrome" --args --user-data-dir=... ...
  Windows schtasks /IT trick so Chrome lands on the interactive desktop
          (WMI process creation lands in invisible session 0)
  Linux   google-chrome flags; over SSH, strip DBUS_SESSION_BUS_ADDRESS
          from the environment so the network service does not deadlock

After launch, each instance is polled at 127.0.0.1:<port>/json/version
until it answers; the run fails loudly if any instance does not come up.
"""
import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_PURPOSES = ["greenhouse", "other-boards"]
MAX_SANE_LANES = 8


def validate_count(raw):
    """Parse the lane count. Raises ValueError on bad input."""
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("enter a positive whole number, got %r" % (raw,))
    if n < 1:
        raise ValueError("need at least 1 browser, got %d" % n)
    return n


def warn_if_many(n):
    if n > MAX_SANE_LANES:
        return ("warning: %d browsers is a lot; each needs its own port, "
                "profile, and screen space. Continuing anyway." % n)
    return ""


def port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def allocate_lanes(n, base_port=9445, purposes=(), profile_root="~"):
    """Pure allocation: lane -> port, profile dir, purpose.

    Raises ValueError if a port in the range is already in use.
    """
    if not (1 <= base_port <= 65535) or not (1 <= base_port + n - 1 <= 65535):
        raise ValueError(
            "port range %d-%d is outside 1-65535"
            % (base_port, base_port + n - 1))
    lanes = []
    for i in range(n):
        port = base_port + i
        if not port_free(port):
            raise ValueError(
                "port %d is already in use; pick a free base port" % port)
        if i < len(purposes):
            purpose = purposes[i]
        elif i < len(DEFAULT_PURPOSES):
            purpose = DEFAULT_PURPOSES[i]
        else:
            purpose = "lane-%d" % (i + 1)
        lanes.append({
            "lane": i + 1,
            "port": port,
            "profile_dir": os.path.join(
                os.path.expanduser(profile_root),
                ".chrome-jobpilot-%d" % port),
            "purpose": purpose,
        })
    return lanes


def build_launch(platform_name, lane):
    """Return the launch command for a lane.

    macOS/Linux: argv list for subprocess. Windows: dict with the .cmd
    body plus the schtasks commands (see docs/machine-setup.md).
    """
    port = lane["port"]
    profile = lane["profile_dir"]
    common = [
        "--user-data-dir=%s" % profile,
        "--remote-debugging-port=%d" % port,
        # Loopback only: the CDP endpoint is powerful, so it must not
        # accept a debugging connection from any origin on the network.
        "--remote-allow-origins=http://127.0.0.1:*",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
    ]
    if platform_name == "Darwin":
        return ["open", "-n", "-a", "Google Chrome", "--args"] + common
    if platform_name == "Windows":
        cmd_dir = os.path.dirname(profile) or "."
        cmd_path = os.path.join(cmd_dir, "chrome-%d.cmd" % port)
        quoted = []
        for a in (["C:\\Program Files\\Google\\Chrome\\Application"
                   "\\chrome.exe"] + common):
            quoted.append('"%s"' % a if " " in a else a)
        body = " ".join(quoted) + "\r\n"
        task = "jobpilot-%d" % port
        return {
            "cmd_dir": cmd_dir,
            "cmd_path": cmd_path,
            "cmd_body": body,
            "task": task,
            "schtasks_create": ["schtasks", "/create", "/tn", task,
                                "/tr", cmd_path, "/sc", "once",
                                "/st", "23:59", "/it", "/f"],
            "schtasks_run": ["schtasks", "/run", "/tn", task],
            "schtasks_delete": ["schtasks", "/delete", "/tn", task, "/f"],
        }
    # Linux and friends
    chrome_bin = (os.environ.get("JOBPILOT_CHROME_BIN") or "google-chrome")
    return [chrome_bin] + common


def launch_lane(platform_name, lane):
    spec = build_launch(platform_name, lane)
    if isinstance(spec, dict):
        os.makedirs(spec["cmd_dir"], exist_ok=True)
        with open(spec["cmd_path"], "w", newline="") as f:
            f.write(spec["cmd_body"])
        try:
            r = subprocess.run(spec["schtasks_create"], capture_output=True,
                               text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(
                    "schtasks /create failed: %s" % r.stderr[-300:])
            r = subprocess.run(spec["schtasks_run"], capture_output=True,
                               text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(
                    "schtasks /run failed: %s" % r.stderr[-300:])
        finally:
            # Always clean up the one-shot task entry.
            subprocess.run(spec["schtasks_delete"], capture_output=True,
                           timeout=60)
        return
    env = dict(os.environ)
    # Over SSH, a stale D-Bus address deadlocks Chrome's network service.
    if os.environ.get("SSH_CONNECTION"):
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    subprocess.Popen(spec, env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def wait_for_instance(port, timeout=30):
    """Poll /json/version until a real Chrome answers.

    Requires the Browser and webSocketDebuggerUrl fields, so an
    unrelated service squatting the port is not mistaken for Chrome.
    """
    deadline = time.time() + timeout
    url = "http://127.0.0.1:%d/json/version" % port
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                info = json.load(r)
            if isinstance(info, dict) and info.get("Browser") \
                    and info.get("webSocketDebuggerUrl"):
                return info["Browser"]
        except Exception:
            pass
        time.sleep(1)
    return None


def summary_table(lanes):
    rows = [("lane", "purpose", "port", "profile dir")]
    for l in lanes:
        rows.append((str(l["lane"]), l["purpose"], str(l["port"]),
                     l["profile_dir"]))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = []
    for i, r in enumerate(rows):
        lines.append("  ".join(v.ljust(widths[j])
                               for j, v in enumerate(r)))
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lanes", type=int, default=0,
                    help="number of browsers (skips the prompt)")
    ap.add_argument("--base-port", type=int, default=9445)
    ap.add_argument("--purpose", action="append", default=[],
                    help="purpose per lane, in order (repeatable)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without launching")
    a = ap.parse_args()

    if a.lanes:
        n = a.lanes
        if n < 1:
            print("need at least 1 browser", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            raw = input("How many Chrome browsers do you want to open for "
                        "job applications? (recommended: 2) ")
        except (EOFError, KeyboardInterrupt):
            print("\ncancelled", file=sys.stderr)
            sys.exit(1)
        if not raw.strip():
            n = 2
        else:
            try:
                n = validate_count(raw)
            except ValueError as e:
                print("invalid input: %s" % e, file=sys.stderr)
                sys.exit(2)
    warning = warn_if_many(n)
    if warning:
        print(warning)

    # Full-length purpose list: defaults first, then --purpose flags,
    # then interactive answers for the rest.
    purposes = [DEFAULT_PURPOSES[i] if i < len(DEFAULT_PURPOSES) else ""
                for i in range(n)]
    for i, p in enumerate(a.purpose):
        if i < n:
            purposes[i] = p
    if not a.dry_run and sys.stdin.isatty():
        for i in range(n):
            if purposes[i]:
                continue
            try:
                p = input("Purpose for lane %d (e.g. research, "
                          "code-gate): " % (i + 1)).strip()
            except (EOFError, KeyboardInterrupt):
                p = ""
            purposes[i] = p or "lane-%d" % (i + 1)
    purposes = [p or "lane-%d" % (i + 1) for i, p in enumerate(purposes)]

    try:
        lanes = allocate_lanes(n, a.base_port, purposes)
    except ValueError as e:
        print("allocation failed: %s" % e, file=sys.stderr)
        sys.exit(2)

    print("\nPlan:")
    print(summary_table(lanes))
    if a.dry_run:
        return

    plat = platform.system()
    failed = []
    for lane in lanes:
        print("launching lane %d on port %d (%s)..." %
              (lane["lane"], lane["port"], lane["purpose"]), flush=True)
        try:
            launch_lane(plat, lane)
        except Exception as e:
            print("  launch failed: %s" % e)
            failed.append(lane)
            continue
        browser = wait_for_instance(lane["port"])
        if browser:
            print("  up: %s" % browser.split("/")[0])
        else:
            print("  FAILED to come up on port %d" % lane["port"])
            failed.append(lane)

    print("\nResult:")
    print(summary_table(lanes))
    if failed:
        print("\n%d of %d browsers failed to start:" %
              (len(failed), len(lanes)), file=sys.stderr)
        for lane in failed:
            print("  lane %d port %d (%s)" %
                  (lane["lane"], lane["port"], lane["purpose"]),
                  file=sys.stderr)
        sys.exit(1)
    print("\nAll %d browsers are up. Point each lane at its port with "
          "JOBPILOT_CDP_URL=127.0.0.1:<port>." % len(lanes))


if __name__ == "__main__":
    main()
