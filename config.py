#!/usr/bin/env python3
"""Shared config loading: config.json file, with env vars taking precedence.

For settings whose consumer uses this loader, precedence is:
environment variable > config.json > built-in default.

config.json lives next to the scripts (copy config.example.json to
config.json; it is gitignored). It holds non-secret settings like
JOBPILOT_CDP_URL, JOBPILOT_QUEUE, TRACKER_BACKENDS. Model settings
(JOBPILOT_MODEL_*, JOBPILOT_API_FLAVOR, JOBPILOT_BACKUP_MODELS) and tracker
credentials/options (TRACKER_* except TRACKER_BACKENDS, plus
GOOGLE_APPLICATION_CREDENTIALS) are read from the environment by their
consumers; placing them in config.json does not configure those consumers.
The setup wizard defaults to the working directory and prints a
JOBPILOT_CONFIG export when its output differs from the scripts path.

Usage:
    from config import load_config
    cfg = load_config()  # dict of known keys

    cdp_url = os.environ.get("JOBPILOT_CDP_URL",
                             cfg.get("JOBPILOT_CDP_URL", "127.0.0.1:9226"))
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")


def load_config(path=None):
    """Load config.json. Returns {} when the file is absent.

    Raises ValueError on malformed JSON (fail loud, not silent).
    """
    p = path or os.environ.get("JOBPILOT_CONFIG", CONFIG_PATH)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config file must hold a JSON object: %s" % p)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def get(key, default=None, cfg=None):
    """Env var wins, then config.json, then default."""
    if key in os.environ:
        return os.environ[key]
    cfg = cfg if cfg is not None else load_config()
    return cfg.get(key, default)
