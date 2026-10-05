"""
Ashby GraphQL form-template capture.

Ashby's application forms submit field values through a GraphQL mutation
(typically named something like ``ApiSetFormValue``). Driving the form
through DOM events alone is unreliable: Ashby can silently discard
UI-driven field updates server-side even when the client shows no error.

The robust approach is to capture the mutation template once, then issue
the same GraphQL mutation directly for every field. This module provides
the two browser-side snippets needed for that:

1. ``TEMPLATE_HOOK_JS`` - installs ``fetch`` and ``XMLHttpRequest`` hooks
   that watch outbound GraphQL requests and stash the first form-value
   mutation template (query text plus the organization / render /
   definition identifiers) on ``window.__tpl``. Observed operation names
   are logged to ``window.__seen_ops`` for debugging.

2. ``trigger_template_capture(name, email, phone)`` - builds a snippet
   that edits one field (name, then email, then phone as fallback) with a
   temporary value change to force Ashby to emit a real mutation, which
   the hook then captures.

Usage sketch (CDP ``Runtime.evaluate``):

    evaluate(TEMPLATE_HOOK_JS)          # install hooks on page load
    evaluate(trigger_template_capture("Jane Doe", "jane@example.com", "+15551234567"))
    tpl = evaluate("JSON.stringify(window.__tpl)")   # None until a mutation fires
    ops = evaluate("JSON.stringify(window.__seen_ops)")

Retry the trigger a few times with increasing waits; some boards batch
mutations or only emit them after blur. Once ``window.__tpl`` is set,
replay its query with your own variables via a direct GraphQL POST and
verify server-side by reading the values back (a 200 response alone does
not prove the values stuck).
"""

TEMPLATE_HOOK_JS = r"""(() => {
  window.__tpl = null;
  window.__seen_ops = [];
  const capture = (d) => {
    try {
      if (d.operationName && !window.__seen_ops.includes(d.operationName)) {
        window.__seen_ops.push(d.operationName);
      }
      // Match the form-value mutation flexibly: the exact operation name
      // has drifted over time (ApiSetFormValue today, something else tomorrow).
      if (d.operationName && !window.__tpl &&
          (/setformvalue/i.test(d.operationName) || /formvalue/i.test(d.operationName))) {
        const v = d.variables || {};
        window.__tpl = {
          q: d.query,
          ids: {
            org: v.organizationHostedJobsPageName,
            render: v.formRenderIdentifier,
            def: v.formDefinitionIdentifier,
          },
        };
      }
    } catch (e) {}
  };
  // Hook fetch.
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts) => {
    const r = await realFetch(url, opts);
    try {
      capture(JSON.parse(String((opts && opts.body) || "")));
    } catch (e) {}
    return r;
  };
  // Hook XMLHttpRequest too: Ashby has been observed switching transports,
  // and a fetch-only hook silently misses everything in that case.
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__url = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      capture(JSON.parse(String(body || "")));
    } catch (e) {}
    return origSend.apply(this, arguments);
  };
  return "hooked-fetch-xhr";
})()"""


def trigger_template_capture(name, email, phone):
    """Build a JS snippet that forces Ashby to emit a form-value mutation.

    Tries the name field first, then email, then phone. The value is set
    through the native property setter with a trailing space appended and
    then removed, which forces the input/change events to fire even when
    the field already holds the target value.
    """
    import json

    candidates = json.dumps(
        [
            {"re": "name", "anti": "company|employer", "val": name},
            {"re": "email", "anti": None, "val": email},
            {"re": "phone|mobile", "anti": None, "val": phone},
        ]
    )
    return (
        r"""(() => {
  const candidates = """
        + candidates
        + r""";
  const results = [];
  for (const c of candidates) {
    const re = new RegExp(c.re, "i");
    const anti = c.anti ? new RegExp(c.anti, "i") : null;
    const wrap = [...document.querySelectorAll("[data-field-path]")].find((x) => {
      const le = x.querySelector("label");
      const t = (le ? le.innerText : "").trim();
      return re.test(t) && !(anti && anti.test(t));
    });
    const input = wrap && wrap.querySelector("input");
    if (!input) { results.push("no-field:" + c.re); continue; }
    input.scrollIntoView({block: "center"});
    input.focus();
    const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value");
    const set = (v) => { if (desc && desc.set) desc.set.call(input, v); else input.value = v; };
    const fire = (t) => input.dispatchEvent(new Event(t, {bubbles: true}));
    set(c.val + " "); fire("input");   // force a change even if value matches
    set(c.val); fire("input"); fire("change");
    input.blur();
    results.push("triggered:" + c.re);
    break;
  }
  return results.join("|") + " ops-seen:" + JSON.stringify(window.__seen_ops || []);
})()"""
    )
