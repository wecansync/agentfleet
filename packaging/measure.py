#!/usr/bin/env python3
"""Measure whether an AgentFleet change helps: run the same tasks on two refs.

Each ref is installed into its own throwaway home with your lane models and
settings (an `agentfleet configure --export` preset). Every task then runs
`claude -p` in a fresh clone of its repository, alternating base and
candidate so gateway load and time of day fall on both equally. After each
run the task's check command decides pass or fail.

The summary compares tokens, list-price cost, turns, passes and the size of
each run's change; every run's diff is kept in diffs/. A difference
counts only when every run of one ref is below every run of the other, with
at least three runs each; otherwise it is "inconclusive". Runs with errors or
failed lanes make a task "confounded": the numbers then say more about the
gateway than about the change. Both rules come from Google Research's
Harness², which marks an unattributable difference as confounded rather than
guessing.

Maintainer tool: it spends real tokens, runs the agent with permission checks
bypassed inside the throwaway clones, and needs a gateway or API-key
provider, since a throwaway home has no subscription login.

    python3 packaging/measure.py --base v2.14.0 --candidate HEAD --tasks tasks.json --runs 3

tasks.json: {"tasks": [{"name": "...", "repo": "path/to/git/repo",
"prompt": "...", "check": "shell command run in the clone (optional)"}]}.
A relative repo path is resolved against the tasks file.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MIN_RUNS = 3
PROVIDER_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
TOKEN_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")


def load_tasks(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{path}: needs a non-empty \"tasks\" list")
    names = set()
    for task in tasks:
        if not isinstance(task, dict) or not all(isinstance(task.get(key), str) and task[key] for key in ("name", "repo", "prompt")):
            raise ValueError(f"{path}: every task needs a name, repo and prompt")
        if task["name"] in names:
            raise ValueError(f"{path}: task name {task['name']!r} is used twice")
        if "check" in task and not isinstance(task["check"], str):
            raise ValueError(f"{path}: {task['name']}: check must be a shell command")
        names.add(task["name"])
        task["repo"] = str((path.parent / task["repo"]).resolve())
    return tasks


def parse_run(stdout: str) -> dict:
    """The numbers this tool compares, from `claude -p --output-format json`."""
    data = json.loads(stdout)
    models = data.get("modelUsage") or {}
    lanes = data.get("subagent_stats") or {}
    return {
        "error": bool(data.get("is_error")),
        "tokens": sum(int(usage.get(field) or 0) for usage in models.values() for field in TOKEN_FIELDS),
        "cost": float(data.get("total_cost_usd") or 0),
        "turns": int(data.get("num_turns") or 0),
        "seconds": round((data.get("duration_ms") or 0) / 1000, 1),
        "lanesStarted": int(lanes.get("spawned") or 0),
        "lanesFailed": int(lanes.get("failed") or 0),
        "models": sorted(models),
        # Bounded copy of the final reply, so a run that edits nothing can be diagnosed.
        "finalText": str(data.get("result") or "")[:1000],
    }


def verdict(base: list[float], candidate: list[float]) -> str:
    if len(base) < MIN_RUNS or len(candidate) < MIN_RUNS:
        return f"inconclusive: fewer than {MIN_RUNS} runs each"
    if max(candidate) < min(base):
        return "candidate lower"
    if min(candidate) > max(base):
        return "candidate higher"
    return "inconclusive: the runs overlap"


def spread(values: list[float], digits: int) -> str:
    if not values:
        return "-"
    fmt = f"{{:,.{digits}f}}"
    return f"{fmt.format(statistics.mean(values))} ({fmt.format(min(values))}–{fmt.format(max(values))})"


def summarize(runs: list[dict]) -> str:
    lines = ["# AgentFleet measurement", ""]
    for task in dict.fromkeys(run["task"] for run in runs):
        side = {ref: [run for run in runs if run["task"] == task and run["variant"] == ref] for ref in ("base", "candidate")}
        good = {ref: [run for run in side[ref] if not run["error"]] for ref in side}
        troubled = sum(1 for ref in side for run in side[ref] if run["error"] or run.get("lanesFailed"))
        lines += [f"## {task}", ""]
        if troubled:
            lines += [f"Confounded: {troubled} run(s) had an error or a failed lane, so the numbers below say more about the provider than the change.", ""]
        lines += ["| | base | candidate | verdict |", "|---|---|---|---|"]
        passed = {ref: f"{sum(1 for run in side[ref] if run.get('passed'))}/{len(side[ref])}" for ref in side}
        lines.append(f"| check passed | {passed['base']} | {passed['candidate']} | |")
        for label, key, digits in (("tokens", "tokens", 0), ("cost at list price ($)", "cost", 3), ("turns", "turns", 1), ("seconds", "seconds", 0),
                                   ("lines changed", "linesChanged", 0), ("files changed", "filesChanged", 1)):
            values = {ref: [run[key] for run in good[ref]] for ref in side}
            judged = "confounded" if troubled else verdict(values["base"], values["candidate"])
            lines.append(f"| {label} | {spread(values['base'], digits)} | {spread(values['candidate'], digits)} | {judged} |")
        lanes = {ref: f"{sum(run.get('lanesStarted', 0) for run in side[ref])} / {sum(run.get('lanesFailed', 0) for run in side[ref])}" for ref in side}
        errors = {ref: sum(1 for run in side[ref] if run["error"]) for ref in side}
        lines += [f"| lanes started / failed | {lanes['base']} | {lanes['candidate']} | |",
                  f"| errors | {errors['base']} | {errors['candidate']} | |", ""]
        failed = [run for ref in side for run in side[ref] if run.get("checkOutput")]
        for bad in failed:
            last = bad["checkOutput"].splitlines()[-1]
            lines.append(f"- {bad['variant']} run {bad.get('run', '?')} failed its check: `{last}` (full output in runs.jsonl)")
        if failed:
            lines.append("")
    lines.append("Cost uses Claude Code's list prices; models it has no price for count as $0, so tokens are the safer measure.")
    return "\n".join(lines) + "\n"


def clean_env(home: Path, provider: bool) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "C.UTF-8", "TERM": "dumb"}
    if provider:
        env.update({key: os.environ[key] for key in PROVIDER_ENV if os.environ.get(key)})
    return env


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if proc.returncode != 0:
        raise SystemExit(f"measure: {' '.join(cmd[:3])} … failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[-400:]}")
    return proc


def install(ref: str, work: Path, preset: Path) -> Path:
    """Install AgentFleet at ``ref`` into a new home with the preset applied."""
    src, home = Path(tempfile.mkdtemp(prefix="src-", dir=work)), Path(tempfile.mkdtemp(prefix="home-", dir=work))
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", ref], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(src)], input=archive.stdout, check=True)
    env = clean_env(home, provider=True)
    run([sys.executable, "-B", str(src / "bin" / "install.py"), "--apply", "--home", str(home), "--no-wizard"], env=env)
    agentfleet = str(home / ".local" / "bin" / "agentfleet")
    run([agentfleet, "configure", "--preset", str(preset)], env=env)
    # The preset carries the user's auto-update choice; an update mid-run would replace the ref under test.
    run([agentfleet, "auto-update", "off"], env=env)
    return home


def change_size(clone: Path, start: str, diff_path: Path | None) -> dict:
    """Files and lines changed since ``start``, counting the agent's commits and new files.

    Stages into a separate index, so the clone's own index is left as the agent left it.
    """
    env = {**os.environ, "GIT_INDEX_FILE": str(clone.parent / "measure.index")}
    git = {"cwd": clone, "env": env, "capture_output": True, "encoding": "utf-8", "errors": "replace"}
    subprocess.run(["git", "add", "-A"], **git)
    numstat = subprocess.run(["git", "diff", "--cached", "--numstat", start], **git).stdout
    rows = [line.split("\t") for line in numstat.splitlines() if line.count("\t") >= 2]
    if diff_path is not None:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(subprocess.run(["git", "diff", "--cached", start], **git).stdout, encoding="utf-8")
    # Binary files show "-" for both counts.
    added = sum(int(row[0]) for row in rows if row[0].isdigit())
    removed = sum(int(row[1]) for row in rows if row[1].isdigit())
    return {"filesChanged": len(rows), "linesAdded": added, "linesRemoved": removed, "linesChanged": added + removed}


def run_task(task: dict, home: Path, work: Path, model: str | None, timeout: int, diff_path: Path | None = None) -> dict:
    clone = Path(tempfile.mkdtemp(prefix="task-", dir=work)) / "repo"
    run(["git", "clone", "-q", "--no-hardlinks", task["repo"], str(clone)])
    # A local clone's origin is the real repository; the agent runs with permissions bypassed.
    subprocess.run(["git", "-C", str(clone), "remote", "remove", "origin"], capture_output=True)
    start = run(["git", "-C", str(clone), "rev-parse", "HEAD"]).stdout.strip()
    cmd = ["claude", "-p", task["prompt"], "--output-format", "json", "--permission-mode", "bypassPermissions"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, cwd=clone, env=clean_env(home, provider=False), capture_output=True, text=True, timeout=timeout)
        result = parse_run(proc.stdout) if proc.stdout.strip() else {"error": True}
        result["error"] = result["error"] or proc.returncode != 0
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        result = {"error": True, "detail": type(exc).__name__}
    # Measured before the check, so files the check writes don't count.
    result.update(change_size(clone, start, diff_path))
    if task.get("check"):
        try:
            check = subprocess.run(task["check"], shell=True, cwd=clone, capture_output=True, text=True, timeout=timeout)
            result["passed"] = check.returncode == 0
            output = check.stdout + check.stderr
        except subprocess.TimeoutExpired:
            result["passed"], output = False, f"check timed out after {timeout} s"
        if not result["passed"]:
            result["checkOutput"] = output.strip()[-2000:]
    else:
        result["passed"] = not result["error"]
    shutil.rmtree(clone.parent, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="git ref of the current behavior")
    parser.add_argument("--candidate", required=True, help="git ref of the change")
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=MIN_RUNS, help=f"runs per task and ref (default {MIN_RUNS})")
    parser.add_argument("--preset", type=Path, help="AgentFleet preset to apply (default: export yours)")
    parser.add_argument("--model", help="main-session model for every run (default: the preset's)")
    parser.add_argument("--timeout", type=int, default=900, help="seconds per run (default 900)")
    parser.add_argument("--out", type=Path, default=Path("measure-out"))
    args = parser.parse_args(argv)
    tasks = load_tasks(args.tasks)
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agentfleet-measure-") as raw:
        work = Path(raw)
        preset = args.preset
        if preset is None:
            preset = work / "preset.json"
            run(["agentfleet", "configure", "--export", str(preset)])
        homes = {"base": install(args.base, work, preset), "candidate": install(args.candidate, work, preset)}
        runs = []
        log = (args.out / "runs.jsonl").open("w", encoding="utf-8")
        with log:
            for task in tasks:
                for index in range(args.runs):
                    for variant in ("base", "candidate"):
                        diff_path = args.out / "diffs" / f"{task['name']}-{variant}-{index + 1}.diff"
                        result = run_task(task, homes[variant], work, args.model, args.timeout, diff_path)
                        result.update(task=task["name"], variant=variant, run=index + 1)
                        runs.append(result)
                        log.write(json.dumps(result) + "\n")
                        log.flush()
                        print(f"measure: {task['name']} {variant} {index + 1}/{args.runs}: "
                              f"{'error' if result['error'] else str(result.get('tokens')) + ' tokens'}, "
                              f"{result['linesChanged']} lines changed, "
                              f"check {'passed' if result['passed'] else 'failed'}", flush=True)
    summary = summarize(runs)
    (args.out / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
