#!/usr/bin/env python3
# claude-agents-config:managed
"""Suggest skills, plugins, MCP servers, mods, and hooks for the current project.

Scans project files, manifests, and opt-in usage counts to propose tailored
capabilities. Never accesses the network, never spawns subprocesses, never reads
.env values, and never installs anything automatically.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import unicodedata

_ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1b\][^\x1b\x07]*(?:\x07|\x1b\\)
    | \x1b\[[0-9;?]*[ -/]*[@-~]
    | \x1b[PX^_][^\x1b]*(?:\x1b\\)
    | \x1b[@-Z\\-_]
    | \x1b
    """,
    re.VERBOSE,
)


def sanitize_text(text: str) -> str:
    """Strip terminal escape sequences, C0/C1 controls, Cf, and U+2028/2029."""
    if not isinstance(text, str):
        return ""
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    out = []
    for ch in cleaned:
        cp = ord(ch)
        if 0x00 <= cp <= 0x1F or cp == 0x7F:
            continue
        if 0x80 <= cp <= 0x9F:
            continue
        if cp in (0x2028, 0x2029):
            continue
        if unicodedata.category(ch) == "Cf":
            continue
        out.append(ch)
    return "".join(out)


def sanitize_value(val: object) -> object:
    """Recursively sanitize strings in nested objects."""
    if isinstance(val, str):
        return sanitize_text(val)
    if isinstance(val, dict):
        return {sanitize_text(str(k)): sanitize_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [sanitize_value(v) for v in val]
    return val

# Suppress bytecode emission before importing local helpers
sys.dont_write_bytecode = True
_HERE = Path(__file__).resolve().parent
SCRIPTS = _HERE if (_HERE / "provider_catalog.py").is_file() else _HERE.parent / "scripts"
if SCRIPTS.is_dir() and str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
try:
    import provider_catalog  # noqa: E402
except ImportError:
    provider_catalog = None

# Ensure stdout uses UTF-8 across all platforms
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SUGGESTIONS_FORMAT = "agentfleet.suggestions.v1"
CATALOG_FORMAT = "agentfleet.suggestions-catalog.v1"

SKIP_DIRS = {
    "node_modules",
    "vendor",
    ".git",
    "dist",
    "build",
    ".venv",
    "venv",
    "target",
    "__pycache__",
    ".next",
}
MAX_WALK_DEPTH = 3
MAX_WALK_ENTRIES = 2000
MAX_FILE_BYTES = 256 * 1024  # 256 KB

KIND_ORDER = {
    "plugin": 0,
    "hook": 1,
    "create-skill": 2,
    "mod": 3,
    "claude-md": 4,
}


def resolve_home(home_arg: str | None = None) -> Path:
    """Resolve the user's home directory across CLI args, env vars, and layouts."""
    if home_arg:
        return Path(home_arg).expanduser().resolve()
    fleet_home = os.environ.get("CLAUDE_FLEET_HOME")
    if fleet_home:
        return Path(fleet_home).expanduser().resolve()
    script_dir = Path(__file__).resolve().parent
    if script_dir.name == ".claude":
        return script_dir.parent
    return Path.home().resolve()


def load_catalog(script_dir: Path | None = None) -> dict:
    """Find and load catalog in installed or repository layouts."""
    if script_dir is None:
        script_dir = Path(__file__).resolve().parent
    installed_path = script_dir / "fleet-suggestions.json"
    if installed_path.is_file():
        try:
            return json.loads(installed_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    repo_path = script_dir.parent / "config" / "suggestions.json"
    if repo_path.is_file():
        try:
            return json.loads(repo_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return {"format": CATALOG_FORMAT, "version": "unknown", "entries": []}


def scan(project: Path) -> dict:
    """Bounded scan of project files and manifests.

    Traverses breadth-first to read every root-level entry first, then descends
    level by level up to 3 levels, visiting at most 2000 entries. Skips
    heavy/vendored directories, never reads files >256KB, and NEVER reads .env
    values (only key names from .env.example / .env.sample).
    """
    project = Path(project).expanduser().resolve()
    files: set[str] = set()
    manifest_deps: set[str] = set()
    compose_images: set[str] = set()
    env_keys: set[str] = set()
    formatters: set[str] = set()
    has_env = False

    entries_visited = 0

    def _process_file(entry: os.DirEntry) -> None:
        nonlocal has_env
        name = entry.name
        try:
            rel_path = Path(entry.path).relative_to(project).as_posix()
        except ValueError:
            return

        files.add(sanitize_text(rel_path))

        # Check if .env file exists (without ever opening it)
        if name == ".env" or name.startswith(".env."):
            if not (name.endswith(".example") or name.endswith(".sample")):
                has_env = True
                return

        # Check formatter configs
        if name in {".prettierrc", ".prettierrc.json", ".prettierrc.yml", ".prettierrc.yaml",
                    ".prettierrc.js", ".prettierrc.cjs", ".prettierrc.mjs", ".prettierrc.toml",
                    "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs"}:
            formatters.add("prettier")
        elif name in {"biome.json", "biome.jsonc"}:
            formatters.add("biome")
        elif name == "pint.json":
            formatters.add("pint")
        elif name in {"rustfmt.toml", ".rustfmt.toml"}:
            formatters.add("rustfmt")

        # Size guard: never read files >256 KB
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            return
        if size > MAX_FILE_BYTES:
            return

        # Parse .env.example / .env.sample: key names ONLY, never values
        if name in {".env.example", ".env.sample"}:
            try:
                content = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key = line.split("=", 1)[0].strip()
                        if key:
                            env_keys.add(sanitize_text(key))
            except OSError:
                pass
            return

        # Manifest: package.json
        if name == "package.json":
            try:
                data = json.loads(Path(entry.path).read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    for sec in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                        deps = data.get(sec)
                        if isinstance(deps, dict):
                            manifest_deps.update(sanitize_text(d) for d in deps.keys())
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass

        # Manifest: composer.json
        elif name == "composer.json":
            try:
                data = json.loads(Path(entry.path).read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    for sec in ("require", "require-dev"):
                        deps = data.get(sec)
                        if isinstance(deps, dict):
                            manifest_deps.update(sanitize_text(d) for d in deps.keys())
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass

        # Manifest: pyproject.toml
        elif name == "pyproject.toml":
            try:
                text = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                if "[tool.black]" in text:
                    formatters.add("black")
                if "[tool.ruff]" in text:
                    formatters.add("ruff")

                # Try stdlib tomllib (Python 3.11+)
                parsed = False
                try:
                    import tomllib
                    toml_data = tomllib.loads(text)
                    parsed = True
                    # project.dependencies
                    proj = toml_data.get("project", {})
                    if isinstance(proj, dict):
                        for item in proj.get("dependencies", []):
                            if isinstance(item, str):
                                m = re.match(r"^([a-zA-Z0-9_\-]+)", item.strip())
                                if m:
                                    manifest_deps.add(sanitize_text(m.group(1)))
                    # tool.poetry.dependencies
                    tool = toml_data.get("tool", {})
                    if isinstance(tool, dict):
                        poetry = tool.get("poetry", {})
                        if isinstance(poetry, dict):
                            for sec in ("dependencies", "dev-dependencies"):
                                deps = poetry.get(sec)
                                if isinstance(deps, dict):
                                    manifest_deps.update(sanitize_text(d) for d in deps.keys())
                except Exception:
                    pass

                # Fallback regex if tomllib was unavailable or failed
                if not parsed:
                    for dep in re.findall(r"""['"]([a-zA-Z0-9_\-]+)(?:[><=~^;].*?)?['"]""", text):
                        manifest_deps.add(sanitize_text(dep))
            except OSError:
                pass

        # Manifest: requirements*.txt
        elif name.startswith("requirements") and name.endswith(".txt"):
            try:
                text = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    m = re.match(r"^([a-zA-Z0-9_\-]+)", line)
                    if m:
                        manifest_deps.add(sanitize_text(m.group(1)))
            except OSError:
                pass

        # Manifest: Gemfile
        elif name == "Gemfile":
            try:
                text = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                for gem in re.findall(r"""gem\s+['"]([a-zA-Z0-9_\-]+)['"]""", text):
                    manifest_deps.add(sanitize_text(gem))
            except OSError:
                pass

        # Manifest: go.mod
        elif name == "go.mod":
            try:
                text = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                for mod in re.findall(r"""(?:require\s+([^\s\(\)]+)|^\s+([^\s\(\)]+)\s+v)""", text, re.MULTILINE):
                    m_name = mod[0] or mod[1]
                    if m_name:
                        manifest_deps.add(sanitize_text(m_name))
            except OSError:
                pass

        # Manifest: Cargo.toml
        elif name == "Cargo.toml":
            try:
                text = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                in_deps = False
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        sec = line[1:-1].strip()
                        in_deps = sec in {"dependencies", "dev-dependencies", "build-dependencies"}
                        continue
                    if in_deps and "=" in line:
                        dep = line.split("=", 1)[0].strip()
                        if dep:
                            manifest_deps.add(sanitize_text(dep))
            except OSError:
                pass

        # Docker Compose images
        elif name.startswith("docker-compose") or name.startswith("compose") and (name.endswith(".yml") or name.endswith(".yaml")):
            try:
                text = Path(entry.path).read_text(encoding="utf-8", errors="replace")
                for img in re.findall(r"""^\s*image:\s*['"]?([^\s'"]+)""", text, re.MULTILINE):
                    compose_images.add(sanitize_text(img))
            except OSError:
                pass

    queue: deque[tuple[Path, int]] = deque([(project, 1)])

    while queue:
        curr, depth = queue.popleft()
        if depth > MAX_WALK_DEPTH:
            continue
        if depth > 1 and entries_visited >= MAX_WALK_ENTRIES:
            break

        try:
            items = list(os.scandir(curr))
        except OSError:
            continue

        # Sort for deterministic traversal order
        items.sort(key=lambda e: e.name)

        for entry in items:
            if depth > 1 and entries_visited >= MAX_WALK_ENTRIES:
                break
            entries_visited += 1

            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue

            if is_dir:
                if name in SKIP_DIRS:
                    continue
                if depth < MAX_WALK_DEPTH:
                    queue.append((Path(entry.path), depth + 1))
            elif is_file:
                _process_file(entry)

    return {
        "files": files,
        "deps": manifest_deps,
        "compose_images": compose_images,
        "env_keys": env_keys,
        "has_env": has_env,
        "formatters": formatters,
    }


def inventory(home: Path, project: Path) -> dict:
    """Inspect installed plugins, available marketplace plugins, skills, and hooks."""
    home = Path(home).expanduser().resolve()
    project = Path(project).expanduser().resolve()

    # 1. Installed plugins from <home>/.claude/plugins/installed_plugins.json
    installed_keys: set[str] = set()
    installed_names: set[str] = set()
    installed_file = home / ".claude" / "plugins" / "installed_plugins.json"
    if installed_file.is_file():
        try:
            data = json.loads(installed_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                plugins = data.get("plugins")
                if isinstance(plugins, dict):
                    installed_keys = set(plugins.keys())
                    installed_names = {k.split("@")[0] for k in installed_keys}
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    # 2. Available plugins from marketplaces
    # <home>/.claude/plugins/marketplaces/*/.claude-plugin/marketplace.json
    # and <home>/.claude/agentfleet/mods/.claude-plugin/marketplace.json
    available_plugins: dict[str, str] = {}  # plugin_name -> marketplace_name

    marketplace_files: list[Path] = []
    marketplaces_dir = home / ".claude" / "plugins" / "marketplaces"
    if marketplaces_dir.is_dir():
        for m_dir in marketplaces_dir.iterdir():
            if m_dir.is_dir():
                mf = m_dir / ".claude-plugin" / "marketplace.json"
                if mf.is_file():
                    marketplace_files.append(mf)

    # AgentFleet mods marketplace in home
    mods_market = home / ".claude" / "agentfleet" / "mods" / ".claude-plugin" / "marketplace.json"
    if mods_market.is_file():
        marketplace_files.append(mods_market)
    else:
        # Fallback to repo mods marketplace if available
        repo_mods = Path(__file__).resolve().parent.parent / "mods" / ".claude-plugin" / "marketplace.json"
        if repo_mods.is_file():
            marketplace_files.append(repo_mods)

    # Extra known marketplaces from settings.json if any
    settings_file = home / ".claude" / "settings.json"
    if settings_file.is_file():
        try:
            s_data = json.loads(settings_file.read_text(encoding="utf-8"))
            if isinstance(s_data, dict):
                extra = s_data.get("extraKnownMarketplaces", {})
                if isinstance(extra, dict):
                    for m_name, info in extra.items():
                        if isinstance(info, dict):
                            src_path = info.get("source", {}).get("path")
                            if src_path:
                                candidate = Path(src_path) / ".claude-plugin" / "marketplace.json"
                                if candidate.is_file():
                                    marketplace_files.append(candidate)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    for mf in marketplace_files:
        try:
            m_data = json.loads(mf.read_text(encoding="utf-8"))
            if isinstance(m_data, dict):
                m_name = m_data.get("name") or mf.parent.parent.name
                for p in m_data.get("plugins", []):
                    if isinstance(p, dict) and "name" in p:
                        available_plugins[p["name"]] = m_name
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue

    # 3. User skills: <home>/.claude/skills/*
    user_skills: set[str] = set()
    user_skills_dir = home / ".claude" / "skills"
    if user_skills_dir.is_dir():
        for item in user_skills_dir.iterdir():
            user_skills.add(item.stem if item.is_file() else item.name)

    # 4. Project skills: <project>/.claude/skills/*
    project_skills: set[str] = set()
    proj_skills_dir = project / ".claude" / "skills"
    if proj_skills_dir.is_dir():
        for item in proj_skills_dir.iterdir():
            project_skills.add(item.stem if item.is_file() else item.name)

    # 5. Project hooks from <project>/.claude/settings.json
    has_format_hook = False
    has_protect_hook = False
    proj_settings = project / ".claude" / "settings.json"
    if proj_settings.is_file():
        try:
            ps_data = json.loads(proj_settings.read_text(encoding="utf-8"))
            if isinstance(ps_data, dict):
                hooks = ps_data.get("hooks", {})
                if isinstance(hooks, dict):
                    # Check PostToolUse for formatters
                    for hook_group in hooks.get("PostToolUse", []):
                        if isinstance(hook_group, dict):
                            for h in hook_group.get("hooks", []):
                                cmd = h.get("command", "")
                                if any(x in cmd for x in ("format-after-edit", "prettier", "biome", "pint", "ruff", "black", "rustfmt")):
                                    has_format_hook = True
                    # Check PreToolUse for secrets protection
                    for hook_group in hooks.get("PreToolUse", []):
                        if isinstance(hook_group, dict):
                            for h in hook_group.get("hooks", []):
                                cmd = h.get("command", "")
                                if any(x in cmd for x in ("protect-secrets", ".env")):
                                    has_protect_hook = True
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    # 6. Project MCP servers from <project>/.mcp.json
    project_mcp_servers: set[str] = set()
    mcp_file = project / ".mcp.json"
    if mcp_file.is_file():
        try:
            mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
            if isinstance(mcp_data, dict):
                servers = mcp_data.get("mcpServers")
                if isinstance(servers, dict):
                    project_mcp_servers.update(servers.keys())
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    return {
        "installed": installed_keys,
        "installed_names": installed_names,
        "available": available_plugins,
        "user_skills": user_skills,
        "project_skills": project_skills,
        "has_format_hook": has_format_hook,
        "has_protect_hook": has_protect_hook,
        "project_mcp_servers": project_mcp_servers,
    }


def load_usage(home: Path, project: Path) -> dict:
    """Read opt-in usage stats for project if available."""
    key = hashlib.sha256(str(Path(project).expanduser().resolve()).encode("utf-8")).hexdigest()[:16]
    usage_file = home / ".claude" / "agentfleet" / "usage" / f"{key}.json"
    if usage_file.is_file():
        try:
            data = json.loads(usage_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("format") == "agentfleet.usage.v1":
                return data
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return {}


def load_suggestions_store(home: Path) -> dict:
    """Load suggestions.json store."""
    store_file = home / ".claude" / "agentfleet" / "suggestions.json"
    if store_file.is_file():
        try:
            data = json.loads(store_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("format") == SUGGESTIONS_FORMAT:
                return data
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return {"format": SUGGESTIONS_FORMAT, "projects": {}}


def save_suggestions_store(home: Path, data: dict) -> None:
    """Write suggestions.json store atomically with 0600 permissions."""
    store_dir = home / ".claude" / "agentfleet"
    store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        store_dir.chmod(0o700)
    except OSError:
        pass

    path = store_dir / "suggestions.json"
    temp = path.with_name(f".suggestions.{os.getpid()}.{secrets.token_hex(6)}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            payload = json.dumps(data, indent=2) + "\n"
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def forget(home: Path) -> list[str]:
    """Delete usage directory and suggestions.json store."""
    removed: list[str] = []
    store_dir = home / ".claude" / "agentfleet"

    usage_dir = store_dir / "usage"
    if usage_dir.exists():
        if usage_dir.is_dir():
            shutil.rmtree(usage_dir, ignore_errors=True)
        else:
            usage_dir.unlink(missing_ok=True)
        removed.append(str(usage_dir))

    sugg_path = store_dir / "suggestions.json"
    if sugg_path.exists():
        sugg_path.unlink(missing_ok=True)
        removed.append(str(sugg_path))

    return removed


def _derive_verb(command: str) -> str:
    """Extract a short action verb from a frequent command, restricted to [a-z0-9][a-z0-9-]{0,39}."""
    parts = command.strip().split()
    if not parts:
        return ""
    candidate = ""
    # e.g., npm run build -> build
    if len(parts) >= 3 and parts[0] in ("npm", "pnpm", "yarn", "bun") and parts[1] == "run":
        candidate = parts[2]
    # e.g., php artisan test -> test
    elif len(parts) >= 3 and parts[0] == "php" and parts[1] == "artisan":
        candidate = parts[2]
    # e.g., composer test -> test
    elif len(parts) >= 2 and parts[0] in ("composer", "cargo", "make"):
        candidate = parts[1]
    else:
        candidate = parts[0]

    candidate = candidate.lower()
    candidate = re.sub(r"[^a-z0-9-]", "-", candidate).lstrip("-")
    candidate = candidate[:40]
    if candidate and re.match(r"^[a-z0-9][a-z0-9-]{0,39}$", candidate):
        return candidate
    return ""


def suggest(home: Path, project: Path) -> list[dict]:
    """Evaluate catalog entries for project and return ranked suggestions."""
    project = Path(project).expanduser().resolve()
    home = Path(home).expanduser().resolve()

    catalog = load_catalog()
    signals = scan(project)
    inv = inventory(home, project)
    usage = load_usage(home, project)

    store = load_suggestions_store(home)
    proj_key = str(project)
    proj_record = store.get("projects", {}).get(proj_key, {})
    dismissed = set(proj_record.get("dismissed", []))

    results: list[dict] = []

    for entry in catalog.get("entries", []):
        eid = entry.get("id")
        kind = entry.get("kind")
        if not eid or not kind or eid in dismissed:
            continue

        # Eligibility check for plugins and mods
        if kind in ("plugin", "mod"):
            install_info = entry.get("install")
            if not isinstance(install_info, dict) or "plugin" not in install_info:
                continue
            plugin_spec = install_info["plugin"]
            p_name = plugin_spec.split("@")[0]
            # Must not be already installed
            if plugin_spec in inv["installed"] or p_name in inv["installed_names"]:
                continue
            # Must exist in available marketplaces
            if p_name not in inv["available"]:
                continue

        # Eligibility check for hooks
        elif kind == "hook":
            if eid == "format-after-edit" and inv["has_format_hook"]:
                continue
            if eid == "protect-secrets" and inv["has_protect_hook"]:
                continue

        # Eligibility check for claude-md
        elif kind == "claude-md":
            if "CLAUDE.md" in signals["files"] or (project / "CLAUDE.md").exists():
                continue

        # Match evaluation
        match_spec = entry.get("match", {})
        matched = False
        matched_signal = ""
        strength = 0  # 3 = explicit dependency/file, 2 = usage, 1 = generic

        # Special kind: create-skill from usage
        if kind == "create-skill":
            min_count = match_spec.get("bashMinCount", 10)
            min_sessions = match_spec.get("bashMinSessions", 3)
            bash_usage = usage.get("bash", {})
            for cmd, stats in bash_usage.items():
                if not isinstance(stats, dict):
                    continue
                c_count = stats.get("count", 0)
                c_sess = stats.get("sessions", 0)
                if c_count >= min_count and c_sess >= min_sessions:
                    verb = _derive_verb(cmd)
                    if not verb:
                        continue
                    skill_id = f"create-skill-{verb}"
                    if skill_id in dismissed or verb in inv["project_skills"] or verb in inv["user_skills"]:
                        continue
                    tmpl = entry.get("template", "")
                    tmpl = tmpl.replace("{{command}}", cmd).replace("{{project}}", project.name).replace("{{verb}}", verb)
                    results.append({
                        "id": skill_id,
                        "kind": "create-skill",
                        "title": f"Project skill for '{cmd}'",
                        "why": f"Frequently ran '{cmd}' ({c_count} times across {c_sess} sessions)",
                        "install": "AgentFleet drafts this for you",
                        "access": f"Local project skill in .claude/skills/{verb}/SKILL.md",
                        "template": tmpl,
                        "_strength": 2,
                    })
            continue

        # Special hook: format-after-edit
        if eid == "format-after-edit":
            if signals["formatters"]:
                matched = True
                matched_signal = f"Found formatter config ({', '.join(sorted(signals['formatters']))})"
                strength = 3
            else:
                # Check usage bash heads
                bash_usage = usage.get("bash", {})
                for b_cmd, b_stats in bash_usage.items():
                    if any(f in b_cmd for f in ("prettier", "pint", "ruff format", "black")):
                        if isinstance(b_stats, dict) and b_stats.get("count", 0) >= 5:
                            matched = True
                            matched_signal = f"Frequently formatted with '{b_cmd}' ({b_stats['count']} times)"
                            strength = 2
                            break

        # Special hook: protect-secrets
        elif eid == "protect-secrets":
            if signals["has_env"]:
                matched = True
                matched_signal = "Found .env file; denies tool writes targeting .env*"
                strength = 3

        # Standard matching rules
        else:
            # 1. Explicit file patterns (depth <= 3)
            if not matched and "files" in match_spec:
                patterns = match_spec["files"]
                for p in patterns:
                    for f in signals["files"]:
                        if fnmatch.fnmatch(f, p) or fnmatch.fnmatch(os.path.basename(f), p) or f.startswith(p.rstrip("/*")):
                            matched = True
                            matched_signal = f"Found {f}"
                            strength = 3
                            break
                    if matched:
                        break

            # 2. Explicit dependencies
            if not matched and "deps" in match_spec:
                for d in match_spec["deps"]:
                    if d in signals["deps"]:
                        matched = True
                        matched_signal = f"Dependencies include {d}"
                        strength = 3
                        break

            # 3. Docker compose images
            if not matched and "composeImages" in match_spec:
                for img_sub in match_spec["composeImages"]:
                    for actual_img in signals["compose_images"]:
                        if img_sub.lower() in actual_img.lower():
                            matched = True
                            matched_signal = f"Docker compose uses image '{actual_img}'"
                            strength = 3
                            break
                    if matched:
                        break

            # 4. Env keys (.env.example / .env.sample prefixes)
            if not matched and "envKeys" in match_spec:
                for prefix in match_spec["envKeys"]:
                    for k in signals["env_keys"]:
                        if k.startswith(prefix) or k == prefix.rstrip("_"):
                            matched = True
                            matched_signal = f".env.example contains {k}"
                            strength = 3
                            break
                    if matched:
                        break

            # 5. Usage slash commands
            if not matched and "commands" in match_spec:
                cmd_spec = match_spec["commands"]
                cmd_usage = usage.get("commands", {})
                if isinstance(cmd_spec, dict):
                    for cmd_name, min_cnt in cmd_spec.items():
                        actual_cnt = cmd_usage.get(cmd_name, {}).get("count", 0) if isinstance(cmd_usage.get(cmd_name), dict) else 0
                        if actual_cnt >= min_cnt:
                            matched = True
                            matched_signal = f"Used {cmd_name} {actual_cnt} times"
                            strength = 2
                            break
                elif isinstance(cmd_spec, list):
                    for cmd_name in cmd_spec:
                        actual_cnt = cmd_usage.get(cmd_name, {}).get("count", 0) if isinstance(cmd_usage.get(cmd_name), dict) else 0
                        if actual_cnt >= 1:
                            matched = True
                            matched_signal = f"Used {cmd_name} {actual_cnt} times"
                            strength = 2
                            break

            # 6. Usage bash heads
            if not matched and "bashHeads" in match_spec:
                b_spec = match_spec["bashHeads"]
                b_usage = usage.get("bash", {})
                if isinstance(b_spec, dict):
                    for b_head, min_cnt in b_spec.items():
                        actual_cnt = b_usage.get(b_head, {}).get("count", 0) if isinstance(b_usage.get(b_head), dict) else 0
                        if actual_cnt >= min_cnt:
                            matched = True
                            matched_signal = f"Frequently ran '{b_head}' ({actual_cnt} times)"
                            strength = 2
                            break
                elif isinstance(b_spec, list):
                    for b_head in b_spec:
                        actual_cnt = b_usage.get(b_head, {}).get("count", 0) if isinstance(b_usage.get(b_head), dict) else 0
                        if actual_cnt >= 1:
                            matched = True
                            matched_signal = f"Ran '{b_head}' ({actual_cnt} times)"
                            strength = 2
                            break

            # 7. Usage domains
            if not matched and "domains" in match_spec:
                d_spec = match_spec["domains"]
                d_usage = usage.get("domains", {})
                if isinstance(d_spec, dict):
                    for d_name, min_cnt in d_spec.items():
                        actual_cnt = d_usage.get(d_name, {}).get("count", 0) if isinstance(d_usage.get(d_name), dict) else 0
                        if actual_cnt >= min_cnt:
                            matched = True
                            matched_signal = f"Accessed domain {d_name} ({actual_cnt} times)"
                            strength = 2
                            break

            # 8. Missing files (e.g. CLAUDE.md)
            if not matched and "missing" in match_spec:
                missing_reqs = match_spec["missing"]
                if all(mf not in signals["files"] for mf in missing_reqs):
                    matched = True
                    matched_signal = f"Project lacks {', '.join(missing_reqs)}"
                    strength = 1

            # 9. Generic minDeps
            if not matched and "minDeps" in match_spec:
                if len(signals["deps"]) >= match_spec["minDeps"]:
                    matched = True
                    matched_signal = f"Project has {len(signals['deps'])} dependencies"
                    strength = 1

        if matched:
            install_cmd = "AgentFleet drafts this for you"
            if entry.get("install") and isinstance(entry["install"], dict) and "plugin" in entry["install"]:
                install_cmd = f"claude plugin install {entry['install']['plugin']}"

            why_text = f"{matched_signal}: {entry['why']}" if matched_signal else entry["why"]

            res_item = {
                "id": eid,
                "kind": kind,
                "title": entry.get("title", eid),
                "why": why_text,
                "install": install_cmd,
                "access": entry.get("access", ""),
                "_strength": strength,
            }
            if kind in ("hook", "create-skill", "claude-md") or "template" in entry:
                res_item["template"] = entry.get("template")

            results.append(res_item)

    # Rank by match strength (3 > 2 > 1), then kind order, then id
    results.sort(key=lambda x: (-x.get("_strength", 0), KIND_ORDER.get(x["kind"], 99), x["id"]))

    for r in results:
        r.pop("_strength", None)

    return [sanitize_value(r) for r in results]


def _pending_notice(cwd: str, home: Path) -> tuple[str | None, int]:
    try:
        project = Path(cwd).expanduser().resolve()
        home = Path(home).expanduser().resolve()
        catalog = load_catalog()
        catalog_ver = catalog.get("version", "unknown")

        suggestions_list = suggest(home, project)
        pending_count = len(suggestions_list)
        if pending_count == 0:
            return None, 0

        store = load_suggestions_store(home)
        proj_key = str(project)
        proj_entry = store.get("projects", {}).get(proj_key, {})

        if proj_entry.get("notified") == catalog_ver:
            return None, pending_count

        line = (
            f"AgentFleet: {pending_count} suggestion{'' if pending_count == 1 else 's'} (skills, plugins, MCP servers, mods, hooks) "
            f"{'matches' if pending_count == 1 else 'match'} this project. Mention once, briefly, at the end of your reply that "
            f"/fleet-suggest lists them; do not install anything."
        )
        return sanitize_text(line), pending_count
    except Exception:
        return None, 0


def pending_notice(cwd: str, home: Path) -> str | None:
    """Check suggestions and return notice line without recording notified state."""
    line, _ = _pending_notice(cwd, home)
    return line


def mark_notified(cwd: str, home: Path, pending_count: int | None = None) -> None:
    """Record that the notice line for current catalog version was shown."""
    try:
        project = Path(cwd).expanduser().resolve()
        home = Path(home).expanduser().resolve()
        catalog = load_catalog()
        catalog_ver = catalog.get("version", "unknown")

        if pending_count is None:
            pending_count = len(suggest(home, project))

        store = load_suggestions_store(home)
        proj_key = str(project)
        projects = store.setdefault("projects", {})
        proj_entry = projects.setdefault(proj_key, {
            "name": project.name,
            "catalog": catalog_ver,
            "pending": 0,
            "seen": None,
            "notified": None,
            "dismissed": [],
            "updated": "",
        })

        proj_entry["name"] = project.name
        proj_entry["catalog"] = catalog_ver
        proj_entry["pending"] = pending_count
        proj_entry["notified"] = catalog_ver
        proj_entry["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        save_suggestions_store(home, store)
    except Exception:
        pass


def notice(cwd: str, home: Path) -> str | None:
    """Prompt hook entry point: check suggestions and return notice line if needed.

    Cheap (no subprocess; <300 ms), never raises, updates suggestions.json,
    and returns notice text only when pending > 0 and notified != catalog version.
    """
    try:
        line, count = _pending_notice(cwd, home)
        if line is not None:
            mark_notified(cwd, home, count)
        return line
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI handler for fleet_suggest.py."""
    parser = argparse.ArgumentParser(
        description="AgentFleet project capability suggestion helper"
    )
    parser.add_argument("--home", help="AgentFleet / Claude home directory")
    parser.add_argument("--project", help="Project directory to scan (default: current directory)")
    parser.add_argument("--json", action="store_true", help="Output suggestions as JSON")
    parser.add_argument("--dismiss", nargs="+", metavar="ID", help="Dismiss suggestion IDs for this project")
    parser.add_argument("--seen", action="store_true", help="Mark suggestions as seen for this catalog version")
    parser.add_argument("--forget", action="store_true", help="Delete usage stats and suggestions.json")

    args = parser.parse_args(argv)

    home = resolve_home(args.home)
    project = Path(args.project or os.getcwd()).expanduser().resolve()

    catalog = load_catalog()
    catalog_ver = catalog.get("version", "unknown")

    if args.forget:
        removed = forget(home)
        if removed:
            print(f"Removed: {', '.join(removed)}")
        else:
            print("Nothing to remove.")
        return 0

    store = load_suggestions_store(home)
    proj_key = str(project)
    projects = store.setdefault("projects", {})
    proj_entry = projects.setdefault(proj_key, {
        "name": project.name,
        "catalog": catalog_ver,
        "pending": 0,
        "seen": None,
        "notified": None,
        "dismissed": [],
        "updated": "",
    })

    if args.dismiss:
        dismissed_set = set(proj_entry.get("dismissed", []))
        for did in args.dismiss:
            dismissed_set.add(did)
        proj_entry["dismissed"] = sorted(dismissed_set)
        proj_entry["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_suggestions_store(home, store)
        # Update suggestions and pending count
        current_suggs = suggest(home, project)
        proj_entry["pending"] = len(current_suggs)
        save_suggestions_store(home, store)
        print(f"Dismissed suggestions: {', '.join(args.dismiss)}")
        return 0

    if args.seen:
        proj_entry["seen"] = catalog_ver
        proj_entry["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_suggestions_store(home, store)
        print(f"Marked suggestions as seen for catalog {catalog_ver}.")
        return 0

    suggestions = suggest(home, project)

    # Update store with current scan
    proj_entry["name"] = project.name
    proj_entry["catalog"] = catalog_ver
    proj_entry["pending"] = len(suggestions)
    proj_entry["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_suggestions_store(home, store)

    if args.json:
        payload = {
            "project": str(project),
            "catalog": catalog_ver,
            "suggestions": suggestions,
        }
        json.dump(sanitize_value(payload), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    # Default human-readable compact output
    if not suggestions:
        print(f"No pending AgentFleet suggestions for {project.name}.")
        return 0

    print(f"AgentFleet suggestions for {project.name} ({len(suggestions)} available, catalog {catalog_ver}):\n")
    grouped: dict[str, list[dict]] = {}
    for s in suggestions:
        grouped.setdefault(s["kind"], []).append(s)

    labels = {
        "plugin": "Plugins & MCP Servers",
        "hook": "Project Hooks",
        "create-skill": "Frequent Command Skills",
        "mod": "AgentFleet Mods",
        "claude-md": "Project Guide",
    }

    for k in ("plugin", "hook", "create-skill", "mod", "claude-md"):
        items = grouped.get(k, [])
        if not items:
            continue
        print(f"  [{labels.get(k, k.title())}]")
        for item in items:
            print(f"  • {item['title']} ({item['id']})")
            print(f"    Why: {item['why']}")
            print(f"    Access: {item['access']}")
            print(f"    Action: {item['install']}")
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
