# Windows build and verification

Instructions for an agent working on a Windows machine. AgentFleet has not yet
been run end to end on real Windows; the Windows code paths have only been
tested on Linux. Your job is to build it, run it, fix what breaks, and push the
fixes for review.

AgentFleet has nothing to compile. On Windows, "build" means:

1. Build the release zip from git and prove it is byte-identical to the
   published one.
2. Run the test suite and the mod tests.
3. Install AgentFleet into a scratch profile and exercise every command, hook
   and install path a Windows user hits.
4. Fix the Windows bugs you find, with tests, and push them on a branch.

The maintainer reviews the branch, bumps the version, writes the changelog,
and builds and deploys the release from Linux. You do none of those.

## Rules

- Work on the branch `fix/windows-runtime`, created from the branch that holds
  this file. Never push to `main`, never merge, never force-push.
- Never deploy. `packaging/deploy.sh` is maintainer-only.
- Do not edit `VERSION`, `CHANGELOG.md`, `site/index.html` or `site/docs/`.
  You may edit `site/install.ps1`, the Windows bootstrap.
- Install only into scratch folders under `$env:TEMP`. Install into the real
  user profile only when the user asks you to (step 7).
- Never ask for, print or commit an API key or gateway token. The scratch
  installs below use `--provider native`, which needs no key.
- Runtime code uses only the Python standard library and Node.js built-ins.
- The installer and the doctor scan every bundle file, `tests/` included, for
  key-shaped strings and machine-local paths. Never put a real path such as
  `C:\Users\<name>` in a bundle file; build fake keys at runtime in tests,
  for example `"sk-" + "1234567890" * 2`. Never weaken that scan.
- Every fix needs a test that fails without the fix. Windows-only behaviour
  gets a Windows-only test (`@unittest.skipUnless(os.name == "nt", "...")`).
- Never skip, delete or loosen a test just to get a green run. A test may be
  skipped on Windows only when the behaviour it checks is genuinely
  POSIX-only (pty, `sh` scripts, Unix file modes); use
  `@unittest.skipUnless(os.name == "posix", "<reason>")` and list it in your
  report.
- Stop stray processes by process ID (`Stop-Process -Id <id>`), never by name:
  `Stop-Process -Name python` kills unrelated Python processes too.
- If you run AgentFleet's fleet lanes, its lane-guard mod blocks them from
  committing and pushing. Do those steps yourself.

## 0. Prerequisites

- Git for Windows. It includes Git Bash, which Claude Code uses to run hooks;
  without Git Bash, Claude Code runs them with PowerShell.
- Python 3.10 or newer, with the `py` launcher.
- Node.js 18 or newer.
- Claude Code (`claude --version`).
- GitHub CLI `gh`, signed in, if you will open the pull request yourself.

All commands below are PowerShell, run from the repository root. Use `py -3`;
if the `py` launcher is missing, use `python`. If your shell tool is Git Bash
(Claude Code's Bash tool is, on Windows), save each PowerShell block as a
`.ps1` file in your temp folder and run it with
`powershell -NoProfile -ExecutionPolicy Bypass -File <file>`.

## 1. Clone and check the bundle

```powershell
git clone -c core.autocrlf=false https://github.com/wecansync/claude-agents-config.git
cd claude-agents-config
git switch docs/windows-build
git switch -c fix/windows-runtime
$env:PYTHONDONTWRITEBYTECODE = '1'
py -3 -B bin\install.py --check
```

`--check` verifies every bundle file's sha256 against `manifest.json`, then
runs the doctor. It must exit 0.

Why `core.autocrlf=false`: `.gitattributes` pins LF line endings for most
bundle files, but not for 31 of them: the `mods/**/*.ts` and `*.tsx` files,
the three `LICENSE` files, `.gitattributes`, `.gitignore`, and the
extensionless scripts `bin/agentfleet`, `bin/claude-agents-doctor`,
`bin/claude-fleet-setup` and `bin/claude-fleet-sync`. Git for Windows defaults
to `core.autocrlf=true`, which checks those files out with CRLF, and the
installer then refuses the clone with "checksum mismatch before installation".
Fix 3.2 below removes the need for this flag.

`PYTHONDONTWRITEBYTECODE` keeps `__pycache__` folders out of the tree:
`packaging/update-manifest.py` refuses to run while one exists.

## 2. Build the release and compare it with the published one

Build from the exact commit the live release was built from. Archive
timestamps come from that commit's time, so any other commit gives different
bytes.

```powershell
$latest = Invoke-RestMethod https://agentfleet.wecansync.com/releases/latest.json
$out = Join-Path $env:TEMP 'af-dist'
py -3 -B packaging\build-release.py --ref $latest.commit --out $out
$dir = Join-Path $out "releases\$($latest.version)"
(Get-FileHash "$dir\agentfleet-$($latest.version).zip").Hash.ToLower() -eq $latest.zip_sha256
(Get-FileHash "$dir\agentfleet-$($latest.version).tar.gz").Hash.ToLower() -eq $latest.tarball_sha256
```

Both lines must print `True`. If either prints `False`, the build is not
reproducible on Windows: report it with both hashes. Do not change any
published file to make them match.

## 3. Fix the known issues first

Reading the code found three likely problems. Confirm each on Windows before
fixing it.

### 3.1 `packaging/update-manifest.py` loses executable modes on Windows

The script takes each file's mode from the stat execute bit. On Windows that
bit is only set for `.exe`, `.bat`, `.cmd` and `.com` files, so regenerating
the manifest there would record the 19 executable bundle files (the `bin/`
scripts, most of `scripts/`, the `.sh` wrappers and two tests) as `0o644`. Linux and macOS installs would
then fail their mode check.

- Fix: take the mode from the git index (`git ls-files -s -z`: `100755` means
  `0o755`, anything else `0o644`). Fall back to the stat bit only for files
  git does not track yet.
- Check: on a clean tree, `py -3 -B packaging\update-manifest.py` followed by
  `git diff --stat` shows no change to `manifest.json` or `checksums.sha256`.
- Until this fix is in, do not commit a `manifest.json` or `checksums.sha256`
  generated on Windows.

### 3.2 `.gitattributes` does not pin LF for 31 bundle files

See step 1.

- Fix: make every text file LF, for example with `* text=auto eol=lf` as the
  first line of `.gitattributes`.
- Check:
  - `git add --renormalize .` stages no content changes; every file in the
    repository is stored with LF today.
  - A second clone made without `-c core.autocrlf=false` passes
    `py -3 -B bin\install.py --check`.
- `.gitattributes` is itself a bundle file, so regenerate the manifest after
  this change, using the script fixed in 3.1.
- Also run `install.cmd --dry-run` and `update.cmd --dry-run` from a clone.
  These batch files are stored with LF endings and use `goto` labels, which
  `cmd.exe` can misread in LF-only files. If they misbehave, store `*.cmd`
  with CRLF in git (`*.cmd text eol=crlf`) and regenerate the manifest; the
  checksums then stay the same on every platform.

### 3.3 The command folder may not be on PATH

The installer puts `agentfleet.cmd` and `agentfleet.ps1` in
`%USERPROFILE%\.local\bin`. The team instructions tell Windows users to run
`agentfleet update`, which only works if that folder is on PATH. It often is,
because Claude Code's own Windows installer uses the same folder.

- Check: after the bootstrap install in step 6, open a new terminal and see
  whether `agentfleet version` runs.
- If it doesn't run:
  - Make `site/install.ps1` add the folder to the **user** PATH when it is
    missing, using `[Environment]::SetEnvironmentVariable('Path', ..., 'User')`,
    and print that it did. Never change the machine PATH.
  - Also check the installer's PATH tip (`bin/install.py`, the line that
    prints "Tip: add ... to your PATH"). It compares paths as exact strings;
    on Windows, compare case-insensitively and ignore a trailing backslash.

## 4. Run the tests

Run one Python process per test module, in parallel; the serial suite is
slow.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$root = (Get-Location).Path
$jobs = foreach ($f in Get-ChildItem tests\test_*.py) {
  Start-Job -Name $f.BaseName -ArgumentList $root, $f.BaseName -ScriptBlock {
    param($root, $name)
    Set-Location $root
    & py -3 -B -m unittest "tests.$name" > (Join-Path $env:TEMP "af-$name.log") 2>&1
    $LASTEXITCODE
  }
}
$jobs | Wait-Job | ForEach-Object { '{0,-32} exit {1}' -f $_.Name, (Receive-Job $_) }
```

On Linux the suite is 302 tests with 3 skipped. On Windows the skips differ:

- `ShimExecutionTests` in `test_install_windows_repairs.py` runs only on
  Windows.
- The pty test in `test_configure.py` is skipped.

Read the log of every module that exits non-zero, then sort each failure into
one of two kinds:

- **A Windows bug in AgentFleet:** fix the product code and add a test.
- **A test that assumes POSIX:** `sh` scripts used as a fake `claude`, Unix
  modes, `/tmp` paths, pty. Make the test portable if that is cheap.
  Otherwise skip it on Windows under the rule above.

Then the mod tests. Run them from each mod folder that has a `tests` folder:

```powershell
foreach ($mod in 'fleet-status', 'lane-guard', 'fast-jev-compaction') {
  Push-Location "mods\$mod"
  claude plugin test
  claude plugin validate .
  Pop-Location
}
```

On Linux the results are fleet-status 109 passed, lane-guard 344 and
fast-jev-compaction 9. The only expected validator warning is that
fast-jev-compaction has no author.

## 5. Install from the clone into a scratch profile

```powershell
$sb = Join-Path $env:TEMP 'af-win-sandbox'
py -3 -B bin\install.py --apply --home $sb --provider native --no-wizard --no-mods --no-configure
$bin = "$sb\.local\bin"
```

The wrappers in `$bin` already carry `--home $sb`, so they act on the
scratch profile. Check each item below and note the result.

1. `& "$bin\agentfleet.cmd" version`, `status`, `config` and `doctor` exit 0,
   and the doctor reports PASS.
2. The same through PowerShell:
   `powershell -NoProfile -ExecutionPolicy Bypass -File "$bin\agentfleet.ps1" doctor`.
3. `& "$bin\claude-agents-doctor.cmd" --check` and
   `& "$bin\claude-fleet-sync.cmd" --check` exit 0.
4. Auto-compact:
   - `agentfleet.cmd config compact 30` sets `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
     to `30` in `$sb\.claude\settings.json`.
   - `config compact 10` is refused.
   - `config compact off` removes AgentFleet's override.
5. The configure wizard without a terminal:
   - `agentfleet.cmd configure --yes` exits 0.
   - `agentfleet.cmd configure --export $sb\team.json` writes a preset with
     no secrets in it.
   - `agentfleet.cmd configure --preset $sb\team.json` applies it.
6. Hooks: read `hooks` in `$sb\.claude\settings.json`. Run every command once
   under Git Bash (`bash -c '<command>'`) and once under PowerShell, with a
   small JSON payload on stdin. For example, for SessionStart:
   `{"hook_event_name":"SessionStart","source":"startup","session_id":"t","cwd":"C:\\Temp"}`.
   A Python traceback, a "command not found" error, or a quoting failure is
   a bug.
7. Rerun the same install command. It must succeed and change nothing.
8. Uninstall:
   `py -3 -B bin\install.py --uninstall --apply --home $sb`. Afterwards no
   AgentFleet files remain in `$sb\.claude`, and `settings.json` holds none
   of AgentFleet's settings.
9. Upgrade from 2.2.0, the path every existing Windows user takes:

   ```powershell
   git worktree add --detach (Join-Path $env:TEMP 'af220') 4e5aa38
   $up = Join-Path $env:TEMP 'af-win-upgrade'
   py -3 -B "$env:TEMP\af220\bin\install.py" --apply --home $up --provider native --no-wizard --no-mods
   py -3 -B bin\install.py --apply --home $up --no-mods --no-configure
   ```

   Then check:
   - `& "$up\.local\bin\agentfleet.cmd" doctor` reports PASS.
   - `settings.json` has `autoCompactWindow` set to `1000000` and
     `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` set to `70`.
   - `& "$up\.local\bin\agentfleet.cmd" rollback` restores the 2.2.0 state.

   Remove the worktree afterwards with
   `git worktree remove "$env:TEMP\af220"`.

## 6. The one-line Windows install, against a local server

This tests `irm ... | iex` and `agentfleet update` the way users run them,
without touching the live site. Commit your work first: the release is built
from a commit.

Start a local server with the release and the bootstrap script:

```powershell
$serve = Join-Path $env:TEMP 'af-serve'
py -3 -B packaging\build-release.py --ref HEAD --out $serve
Copy-Item site\install.ps1 "$serve\install.ps1"
$server = Start-Process py -ArgumentList '-3', '-m', 'http.server', '8765', '--bind', '127.0.0.1', '--directory', $serve -PassThru -WindowStyle Hidden
$server.Id | Set-Content (Join-Path $env:TEMP 'af-server.pid')
```

Save this as `$env:TEMP\af-boot.ps1` and run it with
`powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\af-boot.ps1`.
Running it as a separate process keeps the changed variables out of your
own session.

```powershell
$ErrorActionPreference = 'Stop'
$env:AGENTFLEET_BASE_URL = 'http://127.0.0.1:8765'
$env:USERPROFILE = Join-Path $env:TEMP 'af-boot-home'
$env:LOCALAPPDATA = Join-Path $env:TEMP 'af-boot-local'
$env:AGENTFLEET_MODS = '0'
New-Item -ItemType Directory -Force $env:USERPROFILE, $env:LOCALAPPDATA | Out-Null
irm http://127.0.0.1:8765/install.ps1 | iex
& "$env:USERPROFILE\.local\bin\agentfleet.cmd" doctor
if ($LASTEXITCODE -ne 0) { throw "doctor exited $LASTEXITCODE" }
& "$env:USERPROFILE\.local\bin\agentfleet.cmd" update --no-configure
if ($LASTEXITCODE -ne 0) { throw "update exited $LASTEXITCODE" }
```

It must exit 0. `agentfleet update` downloads `install.ps1` again and runs it
with `powershell -File`, so this also tests the update path. Check 3.3 here
as well.

Your shell has no interactive console, so ask the user to open a new
PowerShell window, paste this in, tell you what happens, and then close the
window (the variables it sets would otherwise stay in that window):

```powershell
$env:AGENTFLEET_BASE_URL = 'http://127.0.0.1:8765'
$env:USERPROFILE = Join-Path $env:TEMP 'af-boot-home'
$env:LOCALAPPDATA = Join-Path $env:TEMP 'af-boot-local'
$env:AGENTFLEET_MODS = '0'
irm http://127.0.0.1:8765/install.ps1 | iex
```

What to check:

- The setup wizard starts by itself after the install.
- Pressing Enter keeps each current value.
- At the TypeSafe key prompt, typed characters do not appear on screen. Leave
  it empty.

Afterwards, stop the server:
`Stop-Process -Id (Get-Content (Join-Path $env:TEMP 'af-server.pid'))`.

## 7. A real Claude Code session (only if the user agrees)

Hooks and mods run for real only inside Claude Code. With the user's OK:

1. Install from the clone into the real profile:
   `py -3 -B bin\install.py --apply`.
2. Restart Claude Code.
3. Check:
   - `/fleet-status` shows the profile, auto-compact percentage and JEV
     state.
   - A fleet subagent dispatch is followed by a lane report.
   - A fleet lane asked to run `git push` is blocked by lane-guard.
   - After `/model` switches to another model, `agentfleet config` still
     shows the same auto-compact percentage.

`agentfleet rollback` undoes the install.

## 8. Commit and push

1. Regenerate the manifest with the fixed script:
   `py -3 -B packaging\update-manifest.py`.
   `git diff manifest.json` must show only `sha256` changes for files you
   edited, and no `mode` changes.
2. Run the full step 4 again. Everything must pass.
3. Stage explicit paths only; never stage `dist/`, logs or scratch folders.
4. End every commit message with:

   ```
   Co-Authored-By: Claude Code <noreply@anthropic.com>
   ```

5. Push the branch: `git push -u origin fix/windows-runtime`.
6. If `gh` is signed in, open a pull request with
   `gh pr create --base main` and end its description with:

   ```
   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

   Do not merge it.

## 9. Report back

- **Machine:** Windows version, PowerShell version, and the Python, Node,
  Git and Claude Code versions.
- **Step 2:** whether the zip and tarball matched the published hashes.
- **Step 4:** each test module's exit code with pass, fail and skip counts,
  plus the mod test counts.
- **Steps 5–7:** the result of each check, with the exact error text for
  anything that failed.
- **Each fix:** the symptom, the cause, the change and the test that covers
  it.
- **Skips:** every test you skipped on Windows, and why.
- **Gaps:** anything you could not test.
- **Branch:** the branch name and the pull request link.
