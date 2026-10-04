#!/usr/bin/env python3
"""Click submit on an already-filled form and verify. Usage: submit_only.py --port 9228 --workdir /tmp/harness/dry-discord"""
import argparse, base64, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp, cdp_ok

def b64(s): return base64.b64encode(s.encode()).decode()

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--workdir", required=True)
a = ap.parse_args()

# find and click the submit button via JS click (most reliable)
js = """(() => {
  const btns = Array.from(document.querySelectorAll('button')).filter(b => {
    try { return b.offsetParent && !b.disabled; } catch(e){ return false; }
  });
  const sub = btns.find(b => /submit\\s*application/i.test((b.innerText||'').trim()));
  if (!sub) return 'NOBTN';
  sub.scrollIntoView({behavior:'instant', block:'center'});
  sub.click();
  return 'CLICKED:' + sub.innerText.trim().slice(0,40);
})()"""
r = cdp_ok(a.port, "evalb64", b64(js), timeout=120)
print("submit:", r["result"])
time.sleep(15)
url = cdp_ok(a.port, "url", timeout=60)["url"]
text = cdp_ok(a.port, "text", "4000", timeout=60)["text"]
tl = text.lower()
confirmed = ("/confirmation" in url) or ("thank you for applying" in tl)
print("url:", url)
print("confirmed:", confirmed)
print("text_start:", text[:300].replace("\n", " "))
json.dump({"url": url, "confirmed": confirmed, "text_start": text[:500]},
          open(os.path.join(a.workdir, "submit.json"), "w"), indent=1)
