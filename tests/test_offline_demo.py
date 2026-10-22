from pathlib import Path
import runpy

import pytest


@pytest.mark.asyncio
async def test_offline_demo_runs_real_tools_and_resumes(tmp_path):
    demo = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "offline_demo.py"))
    summary = await demo["run_demo"](tmp_path / "demo")
    assert summary["session_resume"] == "passed"
    assert summary["bash_policy"] == "denied"
