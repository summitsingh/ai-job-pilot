"""Sanitize untrusted text before placing it in terminal output."""
import re

# Remove entire control sequences before removing individual control bytes.
# OSC supports BEL and ST termination, including an unfinished final sequence.
_OSC = re.compile(r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c|$)", re.DOTALL)
_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*(?:[@-~]|$)")
_ESCAPE = re.compile(r"\x1b[ -/]*[@-~]")
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_terminal(value):
    """Return text without ANSI CSI/OSC escapes or ASCII control characters."""
    text = str(value)
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESCAPE.sub("", text)
    return _CONTROLS.sub("", text)
