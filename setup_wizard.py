#!/usr/bin/env python3
"""Interactive setup for browser lanes, queue paths, trackers, and facts.

Usage:
  python3 setup_wizard.py
  ai-job-pilot-setup --yes --dry-run
  ai-job-pilot-setup --yes --force

--yes takes defaults without launching browsers. --dry-run writes nothing.
Each optional step accepts 'skip'. Missing or invalid facts stop setup:
the wizard never invents applicant facts. Consent approval remains per
application, and filling remains deterministic-first.

Only non-secret keys go to config.json. Tokens, service-account paths,
spreadsheet IDs, and database IDs are represented by placeholders in
printed shell export lines, never stored in config.json. Environment
variables override answers and config defaults, as in config.py.
"""
import argparse
import getpass
import json
import os
import platform
import shlex
import sys
import tempfile

from config import get, load_config
from launch_browsers import (allocate_lanes, validate_count, launch_lane,
                             summary_table, wait_for_instance)
from tracker import parse_notion_db_id, parse_sheets_id

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_KEYS = ("GOOGLE_APPLICATION_CREDENTIALS", "TRACKER_SHEET_ID",
               "TRACKER_NOTION_TOKEN", "TRACKER_NOTION_DB")


def plan_setup(answers):
    """Pure config planning: only non-secret keys, no file or port probes.

    The caller allocates browser lanes with allocate_lanes before using
    this plan. Planning itself validates the requested count.
    """
    directory = answers.get("directory", os.getcwd())
    backends = [b.strip().lower() for b in
                answers.get("backends", "jsonl").split(",") if b.strip()]
    if not backends or any(b not in ("jsonl", "sheets", "notion")
                           for b in backends):
        raise ValueError("tracker backends must be jsonl, sheets, or notion")
    validate_count(answers.get("count", 2))
    base_port = int(answers.get("base_port", 9445))
    cfg = {
        "JOBPILOT_CDP_URL": answers.get("cdp_url") or
                            "127.0.0.1:%d" % base_port,
        "JOBPILOT_QUEUE": answers.get("queue") or
                          os.path.join(directory, "queue.json"),
        "JOBPILOT_CLAIMS": answers.get("claims") or
                           os.path.join(directory, "claims.jsonl"),
        "TRACKER_BACKENDS": ",".join(dict.fromkeys(backends)),
    }
    if answers.get("lane"):
        cfg["JOBPILOT_LANE"] = answers["lane"]
    if answers.get("target_metros"):
        cfg["JOBPILOT_TARGET_METROS"] = answers["target_metros"]
    return cfg


def ask(prompt, default="", secret=False, skip_default=True):
    """All interactive input lives here; blank or 'skip' uses the default."""
    text = prompt + (" [%s]" % default if default and not secret else "") + ": "
    value = (getpass.getpass(text) if secret else input(text)).strip()
    if not value or (skip_default and value.lower() == "skip"):
        return default
    return value


def collect_answers(defaults):
    """Collect optional steps, including an explicit browser launch choice."""
    answers = dict(defaults)
    answers["count"] = ask("Browser count (skip for defaults)", "2")
    answers["base_port"] = ask("Base port", "9445")
    answers["launch"] = ask("Launch these browsers now? yes/no/skip", "no"
                             ).lower() == "yes"
    answers["skip_queue"] = ask("Initialize queue files? yes/no/skip", "yes",
                                 skip_default=False).lower() != "yes"
    if not answers["skip_queue"]:
        answers["queue"] = ask("Queue path", answers["queue"])
        answers["claims"] = ask("Claims path", answers["claims"])
    answers["backends"] = ask("Trackers: jsonl,sheets,notion (or skip)",
                               answers["backends"])
    backends = [b.strip().lower() for b in answers["backends"].split(",")]
    if "sheets" in backends:
        answers["TRACKER_SHEET_ID"] = ask("Spreadsheet URL or ID (or skip)",
                                           defaults.get("TRACKER_SHEET_ID", ""), secret=True)
        answers["GOOGLE_APPLICATION_CREDENTIALS"] = ask(
            "Service-account file (or skip)",
            defaults.get("GOOGLE_APPLICATION_CREDENTIALS", ""), secret=True)
    if "notion" in backends:
        answers["TRACKER_NOTION_TOKEN"] = ask("Notion token (or skip)",
                                              defaults.get("TRACKER_NOTION_TOKEN", ""),
                                              secret=True)
        answers["TRACKER_NOTION_DB"] = ask("Notion database URL or ID (or skip)",
                                           defaults.get("TRACKER_NOTION_DB", ""), secret=True)
    answers["target_metros"] = ask("Target metros, separated by semicolons (or skip)",
                                    answers.get("target_metros", ""))
    return answers


def credential_exports(answers, backends):
    """Return shell-safe exports plus problems, without changing the environment."""
    keys = []
    if "sheets" in backends:
        keys += ["GOOGLE_APPLICATION_CREDENTIALS", "TRACKER_SHEET_ID"]
    if "notion" in backends:
        keys += ["TRACKER_NOTION_TOKEN", "TRACKER_NOTION_DB"]
    values = {key: os.environ.get(key, answers.get(key, "")) for key in keys}
    problems = []
    for key in keys:
        if not values[key].strip():
            problems.append("%s is missing" % key)
    credentials = values.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if "sheets" in backends and credentials and not os.path.isfile(credentials):
        problems.append("GOOGLE_APPLICATION_CREDENTIALS must point to an existing file")
    for key, parser in (("TRACKER_SHEET_ID", parse_sheets_id),
                        ("TRACKER_NOTION_DB", parse_notion_db_id)):
        if values.get(key):
            try:
                values[key] = parser(values[key])
            except ValueError:
                problems.append("%s is not a valid URL or ID" % key)
    exports = ["export %s=%s" % (key, shlex.quote(values[key]))
               for key in keys if values[key]]
    return exports, problems


def check_facts(path, example_path):
    """Validate facts without printing private contents or making any up."""
    if not os.path.isfile(path):
        raise ValueError("facts missing. Run: cp facts.example.json facts.json; "
                         "fill in real applicant details, then rerun setup.")
    with open(path) as f:
        facts = json.load(f)
    if not isinstance(facts, dict):
        raise ValueError("facts must be a JSON object; setup stopped")
    warning = not facts
    if os.path.isfile(example_path):
        with open(example_path) as f:
            example = json.load(f)
        warning = warning or facts == example
        if isinstance(example, dict):
            for key in ("first_name", "last_name", "email", "full_name"):
                if facts.get(key) and facts.get(key) == example.get(key):
                    warning = True
    return warning


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true", help="use defaults, no input or launch")
    ap.add_argument("--force", action="store_true", help="allow config overwrite")
    ap.add_argument("--dry-run", action="store_true", help="print plan, write nothing")
    ap.add_argument("--directory", default=os.getcwd(), help="setup file directory (default: current directory)")
    a = ap.parse_args(argv)
    directory = os.path.abspath(a.directory)
    config_path = os.environ.get("JOBPILOT_CONFIG",
                                 os.path.join(directory, "config.json"))
    if os.path.exists(config_path) and not a.force and not a.dry_run:
        print("Setup stopped: config.json exists; use --force to overwrite.")
        return 2
    try:
        file_cfg = load_config(config_path)
        defaults = {"directory": directory, "count": 2, "base_port": 9445,
                    "queue": get("JOBPILOT_QUEUE", os.path.join(directory, "queue.json"), file_cfg),
                    "claims": get("JOBPILOT_CLAIMS", os.path.join(directory, "claims.jsonl"), file_cfg),
                    "backends": get("TRACKER_BACKENDS", "jsonl", file_cfg),
                    "target_metros": get("JOBPILOT_TARGET_METROS", "", file_cfg)}
        for key in SECRET_KEYS:
            # Only env values or fresh human answers supply credentials.
            defaults[key] = os.environ.get(key, "")
        answers = defaults if a.yes else collect_answers(defaults)
        for key, answer_key in (("JOBPILOT_CDP_URL", "cdp_url"),
                                ("JOBPILOT_QUEUE", "queue"),
                                ("JOBPILOT_CLAIMS", "claims"),
                                ("JOBPILOT_LANE", "lane"),
                                ("TRACKER_BACKENDS", "backends"),
                                ("JOBPILOT_TARGET_METROS", "target_metros")):
            if key in os.environ:
                answers[answer_key] = os.environ[key]
            elif answer_key in ("cdp_url", "lane") and key in file_cfg:
                answers[answer_key] = file_cfg[key]
        lanes = allocate_lanes(validate_count(answers["count"]),
                               int(answers["base_port"]))
        print(summary_table(lanes))
        cfg = plan_setup(answers)
        exports, problems = credential_exports(answers, cfg["TRACKER_BACKENDS"].split(","))
        for problem in problems:
            print("Credential problem: " + problem)
        if exports:
            print("Set these exports privately in your shell profile (replace placeholders):")
            for line in exports:
                # Never print values, including environment-supplied credentials.
                key = line.split("=", 1)[0].removeprefix("export ")
                print("export %s=<your value>" % key)
        facts_path = get("JOBPILOT_FACTS", os.path.join(directory, "facts.json"), file_cfg)
        if check_facts(facts_path, os.path.join(HERE, "facts.example.json")):
            print("Warning: facts look empty or like the example template; review real details.")
        print("Config plan:")
        print(json.dumps(cfg, indent=2))
        if a.dry_run:
            if os.path.exists(config_path):
                print("Dry run: would overwrite %s; a real run requires --force." %
                      os.path.abspath(config_path))
            print("Dry run: no files written and no browsers launched.")
            return 0
        if not answers.get("skip_queue"):
            for path, contents in ((cfg["JOBPILOT_QUEUE"], "[]\n"),
                                   (cfg["JOBPILOT_CLAIMS"], "")):
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                try:
                    with open(path, "x") as f:
                        f.write(contents)
                except FileExistsError:
                    print("Keeping existing %s" % os.path.basename(path))
        if answers.get("launch"):
            for lane in lanes:
                launch_lane(platform.system(), lane)
                if not wait_for_instance(lane["port"]):
                    print("Browser failed to start on port %d" % lane["port"])
                    return 1
        # Publish config only after queue initialization and browser startup succeed.
        # A same-directory temporary file keeps readers from seeing partial JSON.
        os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", dir=os.path.dirname(os.path.abspath(config_path)),
                    prefix=".config-", suffix=".tmp", delete=False) as f:
                temp_path = f.name
                json.dump(cfg, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            if a.force:
                os.replace(temp_path, config_path)
            else:
                # Atomic exclusive publication preserves the no-overwrite race guard.
                os.link(temp_path, config_path)
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
        if os.path.abspath(config_path) != os.path.join(HERE, "config.json"):
            print("export JOBPILOT_CONFIG=%s" % shlex.quote(os.path.abspath(config_path)))
        print("Setup complete. Review facts and use lane_greenhouse.py --dry-run first.")
        return 0
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as e:
        print("Setup stopped: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
