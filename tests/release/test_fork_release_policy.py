# SPDX-License-Identifier: Apache-2.0
"""A fork tag cannot enable publishing; dispatch remains scoped dry-run."""
import ast
import json
from pathlib import Path
import re

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"


@pytest.mark.parametrize("repository,event,allowed", [
    ("imjlk/sprite-gen", "push", False),
    ("another/fork", "push", False),
    ("aldegad/sprite-gen", "push", True),
    ("imjlk/sprite-gen", "workflow_dispatch", True),
    ("aldegad/sprite-gen", "workflow_dispatch", True),
])
def test_release_job_policy(repository, event, allowed):
    text = WORKFLOW.read_text()
    job = text[text.index("\n  release:"):]
    expression = re.search(r"^    if: (.+)$", job, re.M).group(1)
    expression = expression.replace("github.repository", json.dumps(repository)).replace("github.event_name", json.dumps(event))
    expression = expression.replace("||", "or").replace("&&", "and")
    tree = ast.parse(expression, mode="eval")
    assert all(isinstance(n, (ast.Expression, ast.BoolOp, ast.Or, ast.And, ast.Compare, ast.Eq, ast.Constant)) for n in ast.walk(tree))
    assert bool(eval(compile(tree, "release-policy", "eval"), {"__builtins__": {}})) is allowed


def test_release_target_is_the_event_repository_and_dispatch_is_dry_run():
    text = WORKFLOW.read_text()
    assert 'RELEASE_REPO: ${{ github.repository }}' in text
    calls = [line.strip() for line in text.splitlines() if "python scripts/release_publish.py" in line]
    assert len(calls) == 2
    assert all('--repo "${RELEASE_REPO}"' in line for line in calls)
    assert '--dry-run' in calls[0] and '${INPUT_TAG}' in calls[0]
    assert '${GITHUB_REF_NAME}' in calls[1]
