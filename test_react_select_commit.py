import json
import shutil
import subprocess
import unittest

from hard_patterns import react_select_commit_js, react_select_state_js


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS fixture test")
class ReactSelectCommitTests(unittest.TestCase):
    def run_fixture(self, *, multi):
        wanted = ["Yes", "No"] if multi else ["H1 Visa"]
        options = [
            {"label": "Yes", "value": 1},
            {"label": "No", "value": 0},
            {"label": "H1 Visa", "value": "h1"},
        ]
        expression = react_select_commit_js('document.querySelector("#field")', wanted)
        state_expression = react_select_state_js(
            'document.querySelector("#field")', wanted)
        fixture = f"""
const calls=[];
const input={{}};
const props={{
  options:{json.dumps(options)}, isMulti:{str(multi).lower()}, value:"",
  onChange:(value, meta)=>{{props.value=value; calls.push({{value,meta}})}}
}};
input["__reactFiber$fixture"]={{memoizedProps:props,return:null}};
global.document={{querySelector:()=>input}};
const result=eval({json.dumps(expression)});
const state=eval({json.dumps(state_expression)});
console.log(JSON.stringify({{result,state,calls}}));
"""
        completed = subprocess.run(
            ["node", "-e", fixture], check=True, capture_output=True, text=True
        )
        return json.loads(completed.stdout)

    def test_single_select_passes_the_option_object_to_react_onchange(self):
        data = self.run_fixture(multi=False)
        self.assertTrue(data["result"]["ok"])
        self.assertTrue(data["state"]["matches"])
        self.assertEqual(data["calls"][0]["value"], {"label": "H1 Visa", "value": "h1"})
        self.assertEqual(data["calls"][0]["meta"]["action"], "select-option")

    def test_multi_select_passes_the_selected_option_array(self):
        data = self.run_fixture(multi=True)
        self.assertTrue(data["result"]["ok"])
        self.assertTrue(data["state"]["matches"])
        self.assertEqual(data["calls"][0]["value"], [
            {"label": "Yes", "value": 1}, {"label": "No", "value": 0}
        ])


if __name__ == "__main__":
    unittest.main()
