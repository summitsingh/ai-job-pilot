#!/usr/bin/env python3
"""Coordinate a human-supplied Greenhouse verification code.

Usage:
  coordinate(CliNotifier(), 300, on_code, on_timeout)
  on_timeout = functools.partial(requeue_on_timeout, queue_path, claims_path, url)

This module never reads email or the inbox and never fetches codes.
The human supplies the code. Browser callbacks reuse code_gate.py's gate
mechanics; this coordinator does not replace them or bypass consent.
"""
import os
import select
import sys
import time


class Notifier:
    """Transport interface: notify once, then return a code or None on timeout."""

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
                        return None
                    return data.split(b"\n", 1)[0].decode("utf-8")
                if len(data) >= 4096:
                    return ""  # Oversized input is invalid, never retried.
                if time.monotonic() >= deadline:
                    return None
        except (OSError, ValueError, TypeError):
            return None


class TelegramNotifier(Notifier):
    # Future extension point only. No network calls in this stub.
    def notify(self, message):
        raise NotImplementedError("Telegram notifier is a future extension")

    def wait_for_code(self, timeout_s):
        raise NotImplementedError("Telegram notifier is a future extension")


def coordinate(notifier, timeout_s, on_code, on_timeout):
    """Wait once for a human code, validate, and invoke the matching callback."""
    # Never read email or the inbox: only the human provides this code.
    notifier.notify("Enter the 8-character verification code from your email:")
    code = notifier.wait_for_code(timeout_s)
    if code is None:
        on_timeout()
        return {"status": "timeout"}
    if not isinstance(code, str):
        return {"status": "invalid-code"}
    code = code.strip()
    if len(code) != 8 or any(c.isspace() for c in code):
        # Invalid input is terminal for this wait; never retry or guess.
        return {"status": "invalid-code"}
    on_code(code)
    return {"status": "code-entered"}


def requeue_on_timeout(queue_path, claims_path, url):
    """Default timeout callback: return a blocked code-gate job to pending."""
    import job_queue
    return job_queue.requeue(queue_path, claims_path, url,
                             reason="code-gate timeout")
