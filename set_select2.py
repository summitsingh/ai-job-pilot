#!/usr/bin/env python3
"""Set a Greenhouse react-select via TRUSTED CDP clicks. Usage: set_select2.py --port 9232 --id 4033065002 --value "I don't wish to answer" """
import argparse, base64, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok
def b64(s): return base64.b64encode(s.encode()).decode()

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--id", required=True)
ap.add_argument("--value", required=True)
a = ap.parse_args()
port = a.port

# 1. Tag the control with a temp ID via JS
js_tag = f"""(() => {{
  const input = document.getElementById('{a.id}');
  if (!input) return 'NOINPUT';
  const control = input.closest('.select__control');
  if (!control) return 'NOCONTROL';
  control.id = 'tmp-ctrl-{a.id}';
  control.scrollIntoView({{behavior:'instant', block:'center'}});
  return 'tagged';
}})()"""
print("tag:", cdp_ok(port, "evalb64", b64(js_tag), timeout=90)["result"])

# 2. Trusted click on the control via driver fclick
r = cdp_ok(port, "fclick", b64(f"#tmp-ctrl-{a.id}"), timeout=90)
print("fclick control:", r.get("ok"), r.get("error", ""))
time.sleep(2)

# 3. Tag the matching option with a temp ID
val_esc = a.value.replace("'", "\\'")
js_opt = f"""(() => {{
  const opts = Array.from(document.querySelectorAll('[role="option"], .select__option')).filter(el => el.offsetParent);
  const target = opts.find(el => el.innerText.trim() === '{val_esc}');
  if (!target) return 'NOTFOUND:' + opts.map(o=>o.innerText.trim().slice(0,25)).join('|');
  target.id = 'tmp-opt-{a.id}';
  return 'opt tagged';
}})()"""
print("tag opt:", cdp_ok(port, "evalb64", b64(js_opt), timeout=90)["result"])

# 4. Trusted click on the option
r = cdp_ok(port, "fclick", b64(f"#tmp-opt-{a.id}"), timeout=90)
print("fclick option:", r.get("ok"), r.get("error", ""))
time.sleep(1)

# 5. Verify via React fiber
js_ver = f"""(() => {{
  const input = document.getElementById('{a.id}');
  let el = input, fiber = null;
  while (el && !fiber) {{
    for (const k of Object.keys(el)) {{
      if (k.indexOf('__reactFiber') === 0) {{ fiber = el[k]; break; }}
    }}
    el = el.parentElement;
  }}
  let f = fiber, depth = 0, vs = 'notfound';
  while (f && depth < 30) {{
    const p = f.memoizedProps || {{}};
    if (p.options && p.value !== undefined) {{
      try {{ vs = JSON.stringify(p.value).substring(0,50); }} catch(e) {{}}
      break;
    }}
    f = f.return; depth++;
  }}
  return vs;
}})()"""
print("react value:", cdp_ok(port, "evalb64", b64(js_ver), timeout=90)["result"])
