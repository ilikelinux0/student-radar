"""The sweep workflow stays pinned and matches the spec's CI shape."""

from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/sweep.yml")


def test_sweep_workflow_is_scheduled_pinned_and_commits_the_db() -> None:
    """Breaks if the cron, OIDC permissions, force input, or action pins drift."""
    text = WORKFLOW.read_text(encoding="utf-8")
    # PyYAML 1.1 treats the key "on" as boolean true.
    workflow = yaml.safe_load(text)
    trigger = workflow[True]

    assert trigger["schedule"] == [{"cron": "0 6 * * *"}]
    force = trigger["workflow_dispatch"]["inputs"]["force"]
    assert force["type"] == "boolean"
    assert force["default"] is False
    assert workflow["permissions"] == {"contents": "write", "id-token": "write"}
    assert workflow["concurrency"]["group"] == "sweep"
    assert workflow["concurrency"]["cancel-in-progress"] is False

    uses = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("uses:"):
            uses.append(stripped.split("#", 1)[0].removeprefix("uses:").strip())
    assert uses
    for use in uses:
        _repo, sha = use.split("@", 1)
        assert len(sha) == 40
        assert sha.isalnum()

    assert "data/radar.db" in text
    assert "github-actions[bot]" in text
    assert "git pull --rebase --no-edit origin main" in text
    assert "needs_playwright" in text
    assert "--force" in text
    assert "role-to-assume" in text
    assert "GEMINI_API_KEY" in text
    steps = workflow["jobs"]["sweep"]["steps"]
    aws = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith(
            "aws-actions/configure-aws-credentials@"
        )
    )
    assert aws["if"] == "steps.scorer.outputs.provider == 'bedrock'"


def test_sweep_step_can_import_the_browser_it_installed() -> None:
    """Breaks if Playwright is installed for a js source but the sweep lacks it."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["sweep"]["steps"]
    sweep = next(step for step in steps if step.get("name") == "Sweep")

    assert (
        "steps.browsers.outputs.needed == 'true' && '--with playwright'"
        in (sweep["run"])
    )


def test_sweep_reads_the_profile_and_commits_the_snapshot() -> None:
    """Breaks if the profile secret, model secrets, or snapshot paths are dropped."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["sweep"]["steps"]
    names = [step.get("name") for step in steps]
    sweep = steps[names.index("Sweep")]
    commit = steps[names.index("Commit findings and snapshot")]

    assert names.index("Sweep") < names.index("Commit findings and snapshot")
    assert "if" not in sweep
    assert sweep["env"]["RADAR_PROFILE"] == "${{ secrets.RADAR_PROFILE }}"
    assert "SMTP_PASS" not in sweep["env"]
    assert "--panel" not in sweep["run"]
    for path in (
        "data/radar.db",
        "web/public/data/offers.json",
        "web/public/data/sources.json",
    ):
        assert path in commit["run"]
