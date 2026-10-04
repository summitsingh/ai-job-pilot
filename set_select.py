#!/usr/bin/env python3
"""Set a react-select by input ID to a value. Usage: set_select.py --port 9232 --id 4033064002 --value "I don't wish to answer" """
import argparse, base64, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok
def b64(s): return base64.b64encode(s.encode()).decode()

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--id", required=True)
ap.add_argument("--value", required=True)
a = ap.parse_args()

# 1. open the dropdown via real mouse events on the control
js_open = f"""(() => {{
  const input = document.getElementById('{a.id}');
  if (!input) return 'NOINPUT';
  const control = input.closest('.select__control');
  if (!control) return 'NOCONTROL';
  control.scrollIntoView({{behavior:'instant', block:'center'}});
  const r = control.getBoundingClientRect();
  const x = r.x + r.width/2, y = r.y + r.height/2;
  const o = {{bubbles:true, cancelable:true, clientX:x, clientY:y}};
  control.dispatchEvent(new MouseEvent('mousedown', o));
  control.dispatchEvent(new MouseEvent('mouseup', o));
  control.dispatchEvent(new MouseEvent('click', o));
  return 'opened';
}})()"""
print("open:", cdp_ok(a.port, "evalb64", b64(js_open), timeout=90)["result"])
time.sleep(2)

# 2. click the matching option
val_esc = a.value.replace("'", "\\'")
js_opt = f"""(() => {{
  const opts = Array.from(document.querySelectorAll('[role="option"], .select__option')).filter(el => el.offsetParent);
  const target = opts.find(el => el.innerText.trim() === '{val_esc}');
  if (!target) return 'NOTFOUND:' + opts.map(o=>o.innerText.trim().slice(0,30)).join('|');
  const r = target.getBoundingClientRect();
  const o = {{bubbles:true, cancelable:true, clientX:r.x+r.width/2, clientY:r.y+r.height/2}};
  target.dispatchEvent(new MouseEvent('mousedown', o));
  target.dispatchEvent(new MouseEvent('mouseup', o));
  target.dispatchEvent(new MouseEvent('click', o));
  return 'clicked';
}})()"""
print("option:", cdp_ok(a.port, "evalb64", b64(js_opt), timeout=90)["result"])
time.sleep(1)

# 3. verify
js_ver = f"""(() => {{
  const el = document.getElementById('{a.id}');
  return el.closest('.select__control').innerText.substring(0,60);
}})()"""
print("value:", cdp_ok(a.port, "evalb64", b64(js_ver), timeout=90)["result"])
