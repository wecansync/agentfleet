#!/usr/bin/env python3
"""Install, update and connect agent-brain, the free tool that records the
tokens and cost of each Claude Code session.

Releases come from the manifest at agentbrain.wecansync.com: an archive must
match the manifest's size and SHA-256, and only the agent-brain executable is
taken from it. The Claude Code hooks belong to agent-brain itself:
`agent-brain install --assistant claude-code` writes them and
`agent-brain uninstall --assistant claude-code` removes them.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile

sys.dont_write_bytecode = True
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import provider_catalog  # noqa: E402

DEFAULT_BASE_URL = "https://agentbrain.wecansync.com"
BASE_URL_ENV = "AGENTFLEET_AGENT_BRAIN_URL"
MANIFEST_LIMIT = 256 * 1024
ARCHIVE_LIMIT = 128 * 1024 * 1024
BINARY_LIMIT = 256 * 1024 * 1024
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
ASSET_NAME_RE = re.compile(r"agent-brain_[a-z0-9]+_[a-z0-9]+\.(?:tar\.gz|zip)")


class AgentBrainError(Exception):
    pass


def executable_name() -> str:
    return "agent-brain.exe" if os.name == "nt" else "agent-brain"


def _real_home(home: Path) -> bool:
    try:
        return home.resolve(strict=False) == Path.home().resolve(strict=False)
    except RuntimeError:
        return False


def install_dir(home: Path) -> Path:
    """Where agent-brain's own installer puts it, so both update the same file."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local and _real_home(home) else home / "AppData" / "Local"
        return base / "Programs" / "agent-brain"
    return home / ".local" / "bin"


def managed_binary(home: Path) -> Path:
    return install_dir(home) / executable_name()


def find_binary(home: Path) -> Path | None:
    """The agent-brain to run: the one on PATH, else the one in the standard folder."""
    found = shutil.which("agent-brain")
    if found and os.path.isabs(found):
        # On Windows shutil.which also searches the current directory first.
        trusted = {Path(entry).resolve(strict=False) for entry in os.environ.get("PATH", "").split(os.pathsep)
                   if entry and os.path.isabs(entry)}
        if Path(found).parent.resolve(strict=False) in trusted:
            return Path(found)
    managed = managed_binary(home)
    return managed if managed.is_file() else None


def environment(home: Path) -> dict[str, str]:
    env = provider_catalog.claude_cli_environment(home)
    real = _real_home(home)
    if not real:
        # agent-brain keeps its data in the config folders; for another home
        # they must not point back at this user's.
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            env.pop(name, None)
        if os.name == "nt":
            env["APPDATA"] = str(home / "AppData" / "Roaming")
            env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    # agent-brain's own settings reach it; the gateway token and other
    # credentials never do.
    for name, value in os.environ.items():
        if real and name.upper().startswith("AGENT_BRAIN_"):
            env[name] = value
    return env


def parse_version(text: object) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def version_of(binary: Path, home: Path) -> tuple[int, int, int] | None:
    """The release version `agent-brain --version` reports; None for a dev build."""
    try:
        result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15, env=environment(home), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"\bversion\s+v?(\d+\.\d+\.\d+)\s*$", result.stdout.strip())
    return parse_version(match.group(1)) if match else None


def base_url() -> str:
    url = os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
    _check_url(url)
    return url


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise AgentBrainError(f"{BASE_URL_ENV} must use https")


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect hop that leaves https, not only the last one:
    the manifest's checksums are only as trustworthy as its transport."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsRedirects)


def _get(url: str, limit: int, timeout: float) -> bytes:
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "agentfleet"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            _check_url(response.geturl())
            data = response.read(limit + 1)
    except AgentBrainError:
        raise
    except (OSError, ValueError) as exc:
        raise AgentBrainError(f"could not download {url}: {exc}") from exc
    if len(data) > limit:
        raise AgentBrainError(f"{url} is larger than expected")
    return data


def platform_key() -> tuple[str, str]:
    if sys.platform.startswith("linux"):
        system = "linux"
    elif sys.platform == "darwin":
        system = "darwin"
    elif os.name == "nt":
        system = "windows"
    else:
        raise AgentBrainError(f"agent-brain has no release for {sys.platform}")
    machine = (os.environ.get("PROCESSOR_ARCHITEW6432") if os.name == "nt" else None) or platform.machine()
    arch = {"x86_64": "amd64", "amd64": "amd64", "x64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine.lower())
    if arch is None:
        raise AgentBrainError(f"agent-brain has no release for {machine}")
    return system, arch


def latest_release(timeout: float = 30) -> dict:
    """The newest release and its archive for this machine, from the site's manifest."""
    base = base_url()
    try:
        data = json.loads(_get(f"{base}/releases/latest.json", MANIFEST_LIMIT, timeout).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise AgentBrainError("the agent-brain release manifest is not valid JSON") from exc
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise AgentBrainError("the agent-brain release manifest has no valid version")
    system, arch = platform_key()
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    asset = next((item for item in assets if isinstance(item, dict) and item.get("os") == system and item.get("arch") == arch), None)
    if asset is None:
        raise AgentBrainError(f"agent-brain {version} has no release for {system}/{arch}")
    filename, digest, size = asset.get("filename"), asset.get("sha256"), asset.get("size")
    if (not isinstance(filename, str) or not ASSET_NAME_RE.fullmatch(filename)
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= ARCHIVE_LIMIT):
        raise AgentBrainError(f"the agent-brain {version} manifest entry for {system}/{arch} is invalid")
    return {"version": version, "filename": filename, "sha256": digest, "size": size,
            "url": f"{base}/releases/{version}/{filename}"}


def _extract(data: bytes, filename: str) -> bytes:
    """The executable at the archive's top level. Member names never become
    paths, so an archive cannot write anywhere else."""
    name = executable_name()
    content = None
    try:
        if filename.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                member = next((item for item in archive.infolist() if item.filename == name), None)
                if member is not None and not member.is_dir() and member.file_size <= BINARY_LIMIT:
                    content = archive.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for member in archive:
                    if member.name in (name, "./" + name):
                        if member.isreg() and member.size <= BINARY_LIMIT:
                            handle = archive.extractfile(member)
                            content = handle.read(BINARY_LIMIT + 1) if handle else None
                        break
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise AgentBrainError(f"could not read {filename}: {exc}") from exc
    if not content or len(content) > BINARY_LIMIT:
        raise AgentBrainError(f"{filename} does not contain {name}")
    return content


def _replace(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".agent-brain-", dir=target.parent)
    moved = None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o755)
        if os.name == "nt" and target.exists():
            # Windows cannot overwrite a running executable, but it can rename it.
            for stale in target.parent.glob(target.name + ".*.old"):
                try:
                    stale.unlink()
                except OSError:
                    pass
            moved = target.with_name(f"{target.name}.{os.getpid()}.old")
            os.replace(target, moved)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        if moved is not None and not target.exists():
            # Put the old copy back rather than leave the hooks with nothing to run.
            try:
                os.replace(moved, target)
            except OSError:
                pass
        raise


def install(home: Path, release: dict | None = None) -> tuple[str, Path]:
    """Download the release, check it against the manifest, and put the
    executable in the standard folder. Returns the version and its path."""
    release = release or latest_release()
    data = _get(release["url"], release["size"], timeout=120)
    if len(data) != release["size"] or hashlib.sha256(data).hexdigest() != release["sha256"]:
        raise AgentBrainError(f"{release['filename']} does not match the release manifest; nothing was installed")
    target = managed_binary(home)
    if target.is_symlink():
        raise AgentBrainError(f"{target} is a symlink; install agent-brain there yourself")
    try:
        _replace(target, _extract(data, release["filename"]))
    except OSError as exc:
        raise AgentBrainError(f"could not write {target}: {exc}") from exc
    return release["version"], target


def update(home: Path) -> str | None:
    """Replace the standard-folder agent-brain with a newer release. Returns
    the new version, or None when it is current, a dev build, or absent."""
    target = managed_binary(home)
    if target.is_symlink() or not target.is_file():
        return None
    current = version_of(target, home)
    if current is None:
        return None
    release = latest_release()
    if parse_version(release["version"]) <= current:
        return None
    version, _ = install(home, release)
    return version


def connected(home: Path) -> bool:
    """Whether settings.json runs agent-brain hooks it wrote itself."""
    try:
        settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in (hooks.values() if isinstance(hooks, dict) else ()):
        for group in groups if isinstance(groups, list) else ():
            for hook in (group.get("hooks") or ()) if isinstance(group, dict) else ():
                command = hook.get("command") if isinstance(hook, dict) else None
                if (isinstance(command, str) and "agent-brain" in command and " hook " in command
                        and "claude-agents-config:" not in command):
                    return True
    return False


def _run(home: Path, binary: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    try:
        result = subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, env=environment(home), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def connect(home: Path, binary: Path) -> tuple[int, str]:
    """Let agent-brain add its hooks and MCP server to Claude Code."""
    return _run(home, binary, "install", "--assistant", "claude-code")


def disconnect(home: Path, binary: Path) -> tuple[int, str]:
    """Let agent-brain remove its hooks and MCP server; its data stays."""
    return _run(home, binary, "uninstall", "--assistant", "claude-code")
