# AgentFleet and agent-brain check for a real Windows machine.
#
# Covers what CI cannot: the machine's own Claude Code, its settings and its
# Git Bash. Install or update both tools first, in PowerShell:
#   irm https://agentfleet.wecansync.com/install.ps1 | iex
#   irm https://agentbrain.wecansync.com/install.ps1 | iex
# then download this script, run it, and paste its whole output back:
#   irm https://agentfleet.wecansync.com/windows-check.ps1 -OutFile windows-check.ps1
#   powershell -ExecutionPolicy Bypass -File .\windows-check.ps1
#
# It runs only AgentFleet's and agent-brain's own hooks, which Claude Code also
# runs at the start of every session; other hooks are listed but not run.
# Running agent-brain's hooks records one test session named
# "agentfleet-windows-check" in agent-brain's local usage.
# Works in Windows PowerShell 5.1 and PowerShell 7.

$ErrorActionPreference = 'Continue'
$claudeDir = Join-Path $env:USERPROFILE '.claude'

function Section($title) { Write-Output ''; Write-Output "== $title ==" }

function Show-Command($label, [scriptblock]$block) {
    Write-Output "-- $label"
    try {
        $out = & $block 2>&1 | Out-String
        Write-Output $out.TrimEnd()
        Write-Output "   (exit $LASTEXITCODE)"
    } catch {
        Write-Output "   FAILED: $($_.Exception.Message)"
    }
}

# Runs one hook command the way Claude Code does on Windows: as a bash command
# line in Git Bash, with the event JSON on stdin. The command goes through a
# script file so no Windows argument quoting sits in between.
function Invoke-GitBash($bash, $command, $stdin, $timeoutMs) {
    $script = Join-Path ([System.IO.Path]::GetTempPath()) ('af-check-' + [Guid]::NewGuid().ToString('N') + '.sh')
    [System.IO.File]::WriteAllText($script, $command + "`n", (New-Object System.Text.UTF8Encoding($false)))
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $bash
        $psi.Arguments = '"' + ($script -replace '\\', '/') + '"'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.WorkingDirectory = (Get-Location).Path
        $p = [System.Diagnostics.Process]::Start($psi)
        $outTask = $p.StandardOutput.ReadToEndAsync()
        $errTask = $p.StandardError.ReadToEndAsync()
        $p.StandardInput.Write($stdin)
        $p.StandardInput.Close()
        if (-not $p.WaitForExit($timeoutMs)) {
            try { $p.Kill() } catch { }
            return [pscustomobject]@{ Exit = 'timeout'; Out = ''; Err = "no exit after $($timeoutMs / 1000) s" }
        }
        return [pscustomobject]@{ Exit = $p.ExitCode; Out = $outTask.Result; Err = $errTask.Result }
    } finally {
        Remove-Item -LiteralPath $script -Force -ErrorAction SilentlyContinue
    }
}

function First-Lines($text, $count) {
    if (-not $text) { return '' }
    return (($text -split "`r?`n" | Where-Object { $_ } | Select-Object -First $count) -join ' | ')
}

Section 'Machine'
Write-Output "Windows: $([System.Environment]::OSVersion.VersionString)"
Write-Output "PowerShell: $($PSVersionTable.PSVersion)"
foreach ($name in 'claude', 'agentfleet', 'agent-brain', 'python', 'python3', 'py', 'node', 'git') {
    $found = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { Write-Output ("{0,-12} {1}" -f $name, $found.Source) } else { Write-Output ("{0,-12} not found" -f $name) }
}
$bash = $env:CLAUDE_CODE_GIT_BASH_PATH
if (-not $bash) {
    foreach ($candidate in (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')) {
        if (Test-Path -LiteralPath $candidate) { $bash = $candidate; break }
    }
}
if ($bash -and (Test-Path -LiteralPath $bash)) { Write-Output "Git Bash:    $bash" } else { $bash = $null; Write-Output 'Git Bash:    not found (Claude Code then runs hooks in PowerShell)' }

Section 'Versions'
Show-Command 'claude --version' { claude --version }
$record = Join-Path $claudeDir '.claude-agents-config-install.json'
if (Test-Path -LiteralPath $record) {
    try { Write-Output "AgentFleet: $((Get-Content -Raw -LiteralPath $record | ConvertFrom-Json).version)" } catch { Write-Output "AgentFleet: unreadable $record" }
} else { Write-Output 'AgentFleet: not installed (no install record)' }
Show-Command 'agent-brain --version' { agent-brain --version }

Section 'AgentFleet'
Show-Command 'agentfleet doctor' { agentfleet doctor }
Show-Command 'agentfleet status' { agentfleet status }

Section 'agent-brain'
Show-Command 'agent-brain status' { agent-brain status }
Show-Command 'agent-brain update --check' { agent-brain update --check }

Section 'Claude Code'
Show-Command 'claude mcp list' { claude mcp list }
Show-Command 'claude plugin list' { claude plugin list }

Section 'Hooks (run through Git Bash, as Claude Code does)'
$settingsPath = Join-Path $claudeDir 'settings.json'
$settings = $null
try { $settings = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json } catch { Write-Output "cannot read ${settingsPath}: $($_.Exception.Message)" }
if ($settings -and -not $bash) {
    Write-Output 'skipped: no Git Bash'
} elseif ($settings) {
    $cwd = (Get-Location).Path
    $hookCount = 0
    if ($settings.PSObject.Properties['hooks']) {
        foreach ($event in $settings.hooks.PSObject.Properties) {
            foreach ($group in @($event.Value)) {
                foreach ($hook in @($group.hooks)) {
                    if (-not $hook -or $hook.type -ne 'command' -or -not $hook.command) { continue }
                    if ($hook.command -notmatch 'claude-agents-config:|agent-brain(\.exe)?[''"]? hook ') {
                        Write-Output ("{0,-18} {1,-10} {2}" -f $event.Name, 'not run', '(not an AgentFleet or agent-brain hook)')
                        continue
                    }
                    $hookCount++
                    $payload = @{
                        session_id = 'agentfleet-windows-check'; transcript_path = ''; cwd = $cwd
                        hook_event_name = $event.Name; prompt = 'windows check'; source = 'startup'
                        tool_name = 'Read'; tool_input = @{ file_path = $cwd }; tool_response = @{}
                    } | ConvertTo-Json -Compress
                    $r = Invoke-GitBash $bash $hook.command $payload 30000
                    $status = if ($r.Exit -eq 0) { 'ok' } else { "FAIL ($($r.Exit))" }
                    Write-Output ("{0,-18} {1,-10} {2}" -f $event.Name, $status, $hook.command)
                    if ($r.Exit -ne 0) { Write-Output "    stderr: $(First-Lines $r.Err 3)" }
                }
            }
        }
    }
    Write-Output "$hookCount command hooks run"
    if ($settings.PSObject.Properties['statusLine'] -and $settings.statusLine.command) {
        $payload = @{ session_id = 'agentfleet-windows-check'; cwd = $cwd; model = @{ id = 'claude-opus-5.5'; display_name = 'Opus' }; workspace = @{ current_dir = $cwd } } | ConvertTo-Json -Compress
        $r = Invoke-GitBash $bash $settings.statusLine.command $payload 30000
        Write-Output "status line: exit $($r.Exit): $(First-Lines $r.Out 2)"
        if ($r.Exit -ne 0) { Write-Output "    stderr: $(First-Lines $r.Err 3)" }
    }
}

Section 'Inside Claude Code (do these by hand and note the result)'
Write-Output '1. Start Claude Code in any project folder. Note any hook error shown at startup.'
Write-Output '2. Run /fleet-status. It should print the AgentFleet version and "No action needed".'
Write-Output '3. Run /brain. A pane should open with usage at the top; Esc closes it.'
Write-Output '4. Ask: "Use fleet-explore-narrow to find where the README mentions installing." A lane should run and report.'
Write-Output '5. Check that the status line at the bottom shows the model and lane activity.'
