#!/usr/bin/env python3
"""Local model client for the jobpilot harness.

Talks to an OpenAI-compatible endpoint (LM Studio by default) or Ollama's
native API for strict-JSON field mapping. Configure with:

  JOBPILOT_MODEL_URL   base URL of the model server
                       (default: http://127.0.0.1:1234, LM Studio's default)
  JOBPILOT_API_FLAVOR  "openai" (default, LM Studio /v1/chat/completions)
                       or "ollama" (native /api/chat with `format`)
  JOBPILOT_MODEL_NAME  primary model id (default: qwen/qwen3-coder-next)
  JOBPILOT_BACKUP_MODELS  comma-separated fallback model ids
  JOBPILOT_MODEL_MAX_TOKENS     budgeted_chat max_tokens ceiling (default 2000)
  JOBPILOT_MODEL_WALL_TIMEOUT_S budgeted_chat wall-clock budget (default 300)
  JOBPILOT_MODEL_TELEMETRY      budgeted_chat JSONL log
                                (default: model_telemetry.jsonl next to this file)

Strict JSON: uses response_format json_schema with strict:true on the
OpenAI-compatible /v1/chat/completions endpoint. Retry with exponential
backoff; on repeated failure walks the fallback chain.
"""
import datetime
import json
import math
import os
import shlex
import sys
import threading
import time
import urllib.request
import urllib.error

ENDPOINT = os.environ.get("JOBPILOT_MODEL_URL",
                            os.environ.get("ATS_MODEL_URL",
                                           "http://127.0.0.1:1234"))
# API flavor: "openai" (default) uses /v1/chat/completions (LM Studio);
# "ollama" uses native /api/chat with `format` (Ollama backup backend).
API_FLAVOR = os.environ.get("JOBPILOT_API_FLAVOR",
                            os.environ.get("ATS_API_FLAVOR", "openai"))
# Default model chain. Only ONE model needs to be loaded at a time;
# `chat()` probes the server and prefers whichever chain model is
# actually loaded. Override the whole chain with JOBPILOT_MODEL_NAME
# (single model) or JOBPILOT_BACKUP_MODELS (comma-separated, tried after
# JOBPILOT_MODEL_NAME). ATS_MODEL_NAME / ATS_BACKUP_MODELS are honored
# as legacy aliases.
MODEL = os.environ.get("JOBPILOT_MODEL_NAME",
                       os.environ.get("ATS_MODEL_NAME",
                                      "qwen/qwen3-coder-next"))
BACKUP_MODELS = [
    m.strip()
    for m in os.environ.get(
        "JOBPILOT_BACKUP_MODELS",
        os.environ.get(
            "ATS_BACKUP_MODELS",
            "qwen/qwen3-coder-30b,nvidia-nemotron-3.5-lightning-30b-a3b,"
            "gemma-4-26b-a4b-it",
        ),
    ).split(",")
    if m.strip() and m.strip() != MODEL
]


def probe(timeout=10):
    """Returns (ok, model_ids_or_error).

    ok=True if ANY model in the chain ([MODEL] + BACKUP_MODELS) is loaded.
    The single-model hard rule means another lane may have swapped the
    loaded model; we work with whatever is available rather than forcing
    a swap.
    """
    try:
        with urllib.request.urlopen(ENDPOINT + "/v1/models", timeout=timeout) as r:
            data = json.loads(r.read().decode())
        ids = [m.get("id", "") for m in data.get("data", [])]
        chain = [MODEL] + BACKUP_MODELS
        ok = any(any(c in i for i in ids) for c in chain)
        return ok, ids
    except Exception as e:
        return False, [f"{type(e).__name__}: {e}"]


def _chat_once(model, system, user, schema_name, schema, max_tokens,
               temperature, timeout):
    """Single strict-JSON chat completion against one model. Raises on failure."""
    # NOTE 2026-10-02: Ollama's /v1/chat/completions does NOT do constrained
    # decoding for response_format json_schema (model returns valid JSON but
    # ignores the schema shape). The native /api/chat with `format` = JSON
    # schema DOES enforce it. Use the native API. (ATS_API_FLAVOR=openai
    # keeps the old OpenAI-compatible path for LM Studio.)
    if API_FLAVOR == "openai":
        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True,
                                "schema": schema},
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            ENDPOINT + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    # Ollama native: true constrained decoding via `format`.
    body = {
        "model": model,
        "stream": False,
        "format": schema,
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        ENDPOINT + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    content = data["message"]["content"]
    return json.loads(content)


def _lms_loaded():
    """Returns the identifier of the currently loaded LM Studio model, or None.

    Uses `lms ps` over SSH on the model host. Returns None on any error
    (caller falls back to trying the request directly).
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from common import host_ssh
        out = host_ssh("~/.lmstudio/bin/lms ps 2>/dev/null | tail -n +3 | head -5",
                      timeout=30, retries=2)
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("IDENTIFIER"):
                continue
            ident = line.split()[0]
            if ident and ident != "-":
                return ident
        return None
    except Exception:
        return None


def _lms_ensure(target):
    """Ensure `target` is the loaded LM Studio model (one-at-a-time rule).

    If a different model is CONFIRMED loaded, unloads it and loads `target`
    (~30-60s). If the loaded state can't be determined (SSH/API error),
    does NOTHING - avoids churning models on transient errors.
    Only applies to the LM Studio backend (API_FLAVOR=openai).
    """
    if API_FLAVOR != "openai":
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from common import host_ssh
        current = _lms_loaded()
        if current is None:
            return  # unknown state: don't churn
        if target in current:
            return  # already loaded
        host_ssh(f"~/.lmstudio/bin/lms unload {shlex.quote(current)} 2>&1 | tail -1",
                timeout=120, retries=1)
        host_ssh(f"~/.lmstudio/bin/lms load {shlex.quote(target)} 2>&1 | tail -1",
                timeout=300, retries=1)
    except Exception:
        pass


def chat(system, user, schema_name, schema, max_tokens=2000, temperature=0.1,
         retries=3, timeout=180, deadline=None, cancel_event=None):
    """One strict-JSON chat completion. Returns the parsed JSON object.

    Tries models in order: MODEL, then BACKUP_MODELS. Each model gets
    `retries` attempts with exponential backoff before moving to the next.

    NOTE: only ONE model needs to be loaded at a time; the probe in chat()
    prefers whichever chain model is actually loaded.
    This function does NOT auto-swap models (to avoid fighting manual ops).
    If a fallback swap is needed, call _lms_ensure(target) explicitly before
    chat(), or run `lms unload <cur> && lms load <next>` on the model host.
    The 180s timeout covers a cold load.
    """
    def remaining():
        if cancel_event is not None and cancel_event.is_set():
            raise ModelBudgetExceeded("wall-clock budget exceeded")
        if deadline is None:
            return None
        left = deadline - time.monotonic()
        if left <= 0:
            raise ModelBudgetExceeded("wall-clock budget exceeded")
        return left

    def request_timeout(limit):
        left = remaining()
        return min(limit, left) if left is not None else limit

    last = None
    probe_timeout = request_timeout(5)
    # Prefer the actually-loaded model first (single-model hard rule:
    # another lane may have swapped it). Probe is cheap.
    try:
        pok, pids = probe(timeout=probe_timeout)
        if pok:
            chain = [MODEL] + BACKUP_MODELS
            loaded_first = [c for c in chain
                            if any(c in i for i in pids)]
            rest = [c for c in chain if c not in loaded_first]
            order = loaded_first + rest
        else:
            order = [MODEL] + BACKUP_MODELS
    except Exception:
        order = [MODEL] + BACKUP_MODELS
    for model in order:
        remaining()
        for attempt in range(retries):
            call_timeout = request_timeout(timeout)
            try:
                result = _chat_once(model, system, user, schema_name, schema,
                                    max_tokens, temperature, call_timeout)
                remaining()
                return result
            except ModelBudgetExceeded:
                raise
            except Exception as e:
                last = e
                left = remaining()
                if attempt + 1 < retries:
                    delay = 2 ** attempt
                    if left is not None:
                        delay = min(delay, left)
                    if cancel_event is not None:
                        cancel_event.wait(delay)
                    else:
                        time.sleep(delay)
                    remaining()
    raise RuntimeError(
        f"model call failed on {[MODEL] + BACKUP_MODELS} "
        f"after {retries} tries each: {last}"
    )


DEFAULT_MAX_TOKENS = 2000
DEFAULT_WALL_TIMEOUT_S = 300
TELEMETRY_MAX_BYTES = 5 * 1024 * 1024
TELEMETRY_BACKUPS = 2
_TELEMETRY_LOCK = threading.Lock()
DEFAULT_TELEMETRY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "model_telemetry.jsonl")


class ModelBudgetExceeded(TimeoutError):
    """Raised by budgeted_chat when the wall-clock budget expires."""


def _positive_num(value, default, cast):
    try:
        v = cast(value)
        return v if v > 0 and math.isfinite(v) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _env_num(key, default, cast):
    return _positive_num(os.environ.get(key, default), default, cast)


def _write_telemetry(path, rec):
    """Append metadata with 5 MiB rotation and two retained generations.

    The lock serializes threads in this process only. There is no process
    lock; concurrent processes should use separate telemetry paths. Logging
    failures never break a model call.
    """
    try:
        line = json.dumps(rec) + "\n"
        with _TELEMETRY_LOCK:
            if (os.path.exists(path) and
                    os.path.getsize(path) + len(line.encode("utf-8")) > TELEMETRY_MAX_BYTES):
                for generation in range(TELEMETRY_BACKUPS, 1, -1):
                    previous = "%s.%d" % (path, generation - 1)
                    if os.path.exists(previous):
                        os.replace(previous, "%s.%d" % (path, generation))
                os.replace(path, str(path) + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


def budgeted_chat(system, user, schema_name, schema, max_tokens=2000,
                  temperature=0.1, retries=3, timeout=180,
                  telemetry_path=None, wall_timeout=None):
    """chat() with a token ceiling, a hard wall-clock budget, and telemetry.

    - max_tokens is clamped to JOBPILOT_MODEL_MAX_TOKENS (default 2000).
    - The whole call (all retries and fallback models) must finish within
      wall_timeout seconds (default JOBPILOT_MODEL_WALL_TIMEOUT_S, 300), even
      if the server keeps dribbling tokens. Each request socket timeout is
      capped to the remaining budget; the worker deadline also stops retries.
    - On expiry raises ModelBudgetExceeded (a TimeoutError) with
      "wall-clock budget exceeded" in the message.
    - Invalid, non-finite or non-positive wall budgets use the default 300s.
    - One JSON line per call is appended to the telemetry file, rotated at
      5 MiB with two backups. No cross-process lock; use one path per process.
      Token counts are not plumbed through _chat_once, so "tokens" is null, never guessed.

    chat() runs in a daemon worker thread with a deadline and cancellation
    event. Request timeouts are capped to the remaining budget, and expiry
    prevents further retries or fallback model calls. An in-flight socket
    read cannot be force-killed by Python; its result is discarded and the
    worker stops when that request returns. Callers treat expiry as no answer.

    On success returns exactly what chat() returns.
    """
    ceiling = _env_num("JOBPILOT_MODEL_MAX_TOKENS", DEFAULT_MAX_TOKENS, int)
    effective = min(int(max_tokens), ceiling)
    wall = (_positive_num(wall_timeout, DEFAULT_WALL_TIMEOUT_S, float)
            if wall_timeout is not None else
            _env_num("JOBPILOT_MODEL_WALL_TIMEOUT_S",
                     DEFAULT_WALL_TIMEOUT_S, float))
    path = telemetry_path or os.environ.get("JOBPILOT_MODEL_TELEMETRY",
                                            DEFAULT_TELEMETRY)
    box = {}
    cancel_event = threading.Event()
    t0 = time.monotonic()
    deadline = t0 + wall

    def work():
        try:
            box["result"] = chat(system, user, schema_name, schema,
                                 max_tokens=effective,
                                 temperature=temperature, retries=retries,
                                 timeout=timeout, deadline=deadline,
                                 cancel_event=cancel_event)
        except BaseException as e:
            box["error"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(max(0, deadline - time.monotonic()))
    latency = round(time.monotonic() - t0, 3)
    if th.is_alive() or isinstance(box.get("error"), ModelBudgetExceeded):
        cancel_event.set()
        status, err = "timeout", ("wall-clock budget exceeded: %ss for %s"
                                  % (wall, schema_name))
    elif "error" in box:
        # Exception messages can contain server echoes of applicant prompts.
        status, err = "error", type(box["error"]).__name__
    else:
        status, err = "ok", None
    _write_telemetry(path, {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_chain": [MODEL] + BACKUP_MODELS,
        "schema_name": schema_name,
        "max_tokens_requested": max_tokens,
        "max_tokens_effective": effective,
        "wall_timeout_s": wall,
        "latency_s": latency,
        "tokens": None,
        "status": status,
        "error": err,
    })
    if status == "timeout":
        raise ModelBudgetExceeded(err)
    if status == "error":
        raise box["error"]
    return box["result"]


if __name__ == "__main__":
    ok, info = probe()
    print(json.dumps({"ok": ok, "models": info}))
