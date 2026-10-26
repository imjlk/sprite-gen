# SPDX-License-Identifier: Apache-2.0
"""The T4 lane (`scripts/t4_lane.py`, docs/release.md) runs the torch backend's tests on CUDA, beside the Linux lane's run of
them on the CPU (`scripts/linux_lane.sh`). A commit passes on the two together only where they install one PyTorch on one
Python: its CUDA build there, its CPU build here."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pinned(text: str, name: str) -> str:
    found = re.findall(rf'^{name}\s*=\s*"?([^"\s]+)"?$', text, re.MULTILINE)
    assert len(found) == 1, f"{name} is pinned on one line of its own, found {found}"
    return found[0]


def test_the_t4_lane_installs_the_pytorch_and_the_python_the_linux_lane_does():
    linux = (ROOT / "scripts" / "linux_lane.sh").read_text(encoding="utf-8")
    t4 = (ROOT / "scripts" / "t4_lane.py").read_text(encoding="utf-8")
    assert _pinned(t4, "TORCH_VERSION") == _pinned(linux, "TORCH_VERSION")
    python = _pinned(linux, "PINNED_PYTHON")
    assert _pinned(t4, "PINNED_PYTHON") == python
    assert _pinned(t4, "PYTHON_IMAGE").startswith(f"python:{python}.")
