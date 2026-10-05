#!/usr/bin/env python3
"""Shared transport for the jobpilot ATS fill harness.

Browser ops run on a remote host that serves the debug-Chrome pool,
reached over SSH. Configure with environment variables:

  JOBPILOT_SSH_HOST   ssh destination for the browser host, e.g. "user@host"
                      (required for the remote lane)
  JOBPILOT_SSH_CMD    ssh command to use (default: "ssh")
  JOBPILOT_SCP_CMD    scp command to use (default: "scp")
  JOBPILOT_CDP_URL    "host:port" of a debug Chrome reachable directly
                      (default: unset). When set, the generic driver runs
                      LOCALLY against that address instead of over SSH, and
                      JOBPILOT_HOST_SSH (a ssh-helper path) is used for
                      browser-host file checks.

The CDP driver (cdp_driver.py) is stdlib-only, so no venv is needed on the
browser host.
"""
import base64
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SSH_CMD = os.environ.get("JOBPILOT_SSH_CMD", "ssh")
SCP_CMD = os.environ.get("JOBPILOT_SCP_CMD", "scp")
SSH_HOST = os.environ.get("JOBPILOT_SSH_HOST", "")
DRIVER = "/tmp/jobpilot/cdp_driver.py"
DIRECT_CDP = os.environ.get("JOBPILOT_CDP_URL",
                            os.environ.get("ATS_CDP_URL"))
HOST_SSH = os.environ.get("JOBPILOT_HOST_SSH",
                          os.environ.get("ATS_HOST_SSH"))
DIRECT_DRIVER = os.path.join(HERE, "cdp_direct.py")


def is_direct():
    return bool(DIRECT_CDP)


def b64(s):
    return base64.b64encode(s.encode("utf-8")).decode()


def _ssh_base():
    if not SSH_HOST:
        raise RuntimeError(
            "JOBPILOT_SSH_HOST is not set (ssh destination for the "
            "browser host, e.g. user@host)")
    return [SSH_CMD, SSH_HOST]


def host_ssh(*args, timeout=240, retries=3):
    """Run a command on the browser host over SSH, retrying transient
    connection drops (rc=255). A non-zero exit from the remote command
    itself is not retried beyond the rc=255 case."""
    base = _ssh_base()
    last = None
    for attempt in range(retries):
        r = subprocess.run(base + list(args),
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return r.stdout.strip()
        last = r
        if r.returncode == 255 and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        break
    raise RuntimeError(
        f"ssh failed rc={last.returncode}: {last.stderr.strip()[:300]}")


def ensure_driver():
    if DIRECT_CDP:
        # direct lane: driver runs locally, nothing to copy
        return
    if not SSH_HOST:
        raise RuntimeError("JOBPILOT_SSH_HOST is not set")
    host_ssh("mkdir", "-p", "/tmp/jobpilot")
    r = subprocess.run(
        [SCP_CMD, os.path.join(HERE, "cdp_driver.py"),
         f"{SSH_HOST}:{DRIVER}"],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("driver scp copy failed: " + r.stderr.strip()[:300])


def cdp(port, cmd, *args, timeout=240):
    """Run one driver command. Returns parsed JSON (raises on transport error).

    With JOBPILOT_CDP_URL set, the generic driver runs locally against that
    host:port (port arg is ignored there; the URL carries it).
    """
    if DIRECT_CDP:
        r = subprocess.run([sys.executable, DIRECT_DRIVER, DIRECT_CDP, cmd,
                            *args], capture_output=True, text=True,
                           timeout=timeout)
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            raise RuntimeError(
                f"direct cdp {cmd} failed rc={r.returncode}: "
                f"{(r.stderr or '')[:300]}")
        try:
            return json.loads(out)
        except Exception:
            raise RuntimeError(f"bad driver JSON for {cmd}: {out[:300]}")
    out = host_ssh("python3", DRIVER, str(port), cmd, *args, timeout=timeout)
    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError(f"bad driver JSON for {cmd}: {out[:300]}")


def host_test_file(path):
    """Check the file exists on the BROWSER host (where setFileInputFiles
    reads it): via JOBPILOT_HOST_SSH in the direct lane, else the SSH host."""
    if HOST_SSH:
        r = subprocess.run([os.path.expanduser(HOST_SSH), "test", "-f", path,
                            "&&", "echo", "OK"], capture_output=True,
                           text=True, timeout=60)
        return r.returncode == 0 and r.stdout.strip() == "OK"
    try:
        host_ssh("test", "-f", path, "&&", "echo", "OK")
        return True
    except Exception:
        return False


def cdp_ok(port, cmd, *args, timeout=240):
    r = cdp(port, cmd, *args, timeout=timeout)
    if not r.get("ok"):
        raise RuntimeError(f"cdp {cmd} failed: {r.get('error')}")
    return r


def container_click_js(key):
    """JS returning the viewport center of a react-select-style control.

    The visible combobox input is often a 2-4px wide element; clicking its
    center is unreliable. Click the control container's center instead.
    Uses instant scrolling with a verify-and-retry so the point is real.
    """
    return ("(() => { const input = document.querySelector(" +
            json.dumps(key) + "); if (!input) return {error:'not-found'}; " +
            "const ctl = input.closest('.select__control') || " +
            "input.closest('[class*=control]') || input.parentElement; " +
            "const center = () => { " +
            "ctl.scrollIntoView({block:'center', behavior:'instant'}); " +
            "void ctl.offsetHeight; " +
            "return ctl.getBoundingClientRect(); }; " +
            "let r = center(); " +
            "if (r.y < 0 || r.y > window.innerHeight) { " +
            "window.scrollTo(0, Math.max(0, r.y + window.scrollY - window.innerHeight/2)); " +
            "void ctl.offsetHeight; r = ctl.getBoundingClientRect(); } " +
            "const inView = r.y >= 0 && r.y <= window.innerHeight; " +
            "return {x: r.x + r.width/2, y: r.y + r.height/2, inView: inView}; })()")
