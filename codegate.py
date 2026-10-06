#!/usr/bin/env python3
"""Human-input coordinator library used by lane_greenhouse.py.

Usage:
  coordinate(CliNotifier(), 300, on_code, on_timeout)
  on_timeout = functools.partial(requeue_on_timeout, queue_path, claims_path, url)

code_gate.py is the standalone browser CLI for a parked gate. This module
owns the bounded human wait and callbacks, not browser mechanics.

This module never reads email or the inbox and never fetches codes.
The human supplies the code. Browser callbacks reuse code_gate.py's gate
mechanics; this coordinator does not replace them or bypass consent.
"""
import os
import re
import select
import sys
import time


NO_INPUT = object()
MAX_ATTEMPTS = 3


class Notifier:
    """Transport: code, None on timeout, or NO_INPUT if human input is unavailable."""

    def notify(self, message):
        raise NotImplementedError

    def wait_for_code(self, timeout_s):
        raise NotImplementedError


class CliNotifier(Notifier):
    def notify(self, message):
        print(message, flush=True)

    def wait_for_code(self, timeout_s):
        # POSIX readiness plus raw reads keep partial pipe input from
        # blocking in readline() beyond the human wait deadline. Unsupported
        # streams fail closed rather than ignoring the timeout.
        deadline = time.monotonic() + max(0, timeout_s)
        data = b""
        try:
            fd = sys.stdin.fileno()
            while True:
                remaining = max(0, deadline - time.monotonic())
                ready, _, _ = select.select([sys.stdin], [], [], remaining)
                if not ready:
                    return None
                chunk = os.read(fd, 4096)
                data += chunk
                if b"\n" in data or not chunk:
                    if not data:
                        return NO_INPUT
                    return data.split(b"\n", 1)[0].decode("utf-8")
                if len(data) >= 4096:
                    return ""  # Oversized input is invalid, never retried.
                if time.monotonic() >= deadline:
                    return None
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            return NO_INPUT


class TelegramNotifier(Notifier):
    # Future extension point only. No network calls in this stub.
    def notify(self, message):
        raise NotImplementedError("Telegram notifier is a future extension")

    def wait_for_code(self, timeout_s):
        raise NotImplementedError("Telegram notifier is a future extension")


def coordinate(notifier, timeout_s, on_code, on_timeout, *, attempts=1,
               max_attempts=MAX_ATTEMPTS):
    """Wait once. A missing human never retries; callback failure is explicit.

    Callers persist attempts before entering this function. None means a
    genuine wait timeout; NO_INPUT means stdin is unavailable or undecodable.
    """
    if attempts > max_attempts:
        return {"status": "blocked", "reason": "code-gate attempt cap reached"}
    notifier.notify("Enter the 8-character verification code from your email:")
    code = notifier.wait_for_code(timeout_s)
    if code is NO_INPUT:
        return {"status": "no_input"}
    if code is None:
        if attempts >= max_attempts:
            return {"status": "blocked", "reason": "code-gate attempt cap reached"}
        retry = on_timeout()
        if retry is False:
            return {"status": "blocked", "requeued": False}
        if retry is None:  # Compatibility for notification-only callbacks.
            return {"status": "timeout"}
        return {"status": "timeout", "requeued": bool(retry)}
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{8}", code.strip()):
        return {"status": "invalid-code"}
    outcome = on_code(code.strip())
    if isinstance(outcome, dict):
        return {"status": "confirmed" if outcome.get("confirmed") else "failed",
                "result": outcome}
    return {"status": "code-entered"}


def requeue_on_timeout(queue_path, claims_path, url):
    """Default timeout callback: return a blocked code-gate job to pending."""
    import job_queue
    return job_queue.requeue(queue_path, claims_path, url,
                             reason="code-gate timeout")
