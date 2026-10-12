#!/usr/bin/env python3
"""Local osu!standard dashboard, adapted from the Mania render bench."""
from __future__ import annotations

import argparse
import codecs
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
RESOLUTIONS = ("1280x720", "1920x1080")
FPS = (30, 60)
ENCODERS = ("auto", "h264_vaapi", "h264_nvenc", "libx264")
GENERATED = re.compile(r"r3d-web-\d{8}-\d{6}-[0-9a-f]{12}\.mp4\Z")
PROGRESS = re.compile(r"rendering(?:…|\.{3})\s+(\d{1,3})%", re.IGNORECASE)
RUNTIME_MODULES = ("osrparse", "numpy", "PIL", "moderngl")


class APIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")


def confined(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.resolve().is_relative_to(root) for root in roots)


def catalog_id(kind: str, path: Path) -> str:
    return kind + "-" + hashlib.sha256(os.fsencode(path)).hexdigest()[:20]


def validate_renderer_repo(path: Path) -> Path:
    repo = path.expanduser().resolve()
    if not all((repo / name).is_file() for name in (
            "osu_std_renderer/cli.py", "osu_std_renderer/__main__.py")):
        raise ValueError(
            f"renderer repo must contain osu_std_renderer/cli.py and osu_std_renderer/__main__.py: {repo}")
    return repo


def renderer_python(renderer_repo: Path) -> Path | str:
    python = renderer_repo / ".venv/bin/python"
    return python if python.is_file() and os.access(python, os.X_OK) else sys.executable


def ensure_runtime(parser: argparse.ArgumentParser, argv=None) -> None:
    """Use a prepared local environment before scanning fixtures or launching jobs."""
    missing = [name for name in RUNTIME_MODULES if importlib.util.find_spec(name) is None]
    if not missing:
        return
    probe = "; ".join(f"import {name}" for name in RUNTIME_MODULES)
    for python in (REPO / ".dashboard-venv/bin/python", REPO / ".venv/bin/python"):
        if not python.is_file() or not os.access(python, os.X_OK):
            continue
        if os.path.abspath(python) == os.path.abspath(sys.executable):
            continue
        try:
            result = subprocess.run([str(python), "-c", probe],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=10, check=False)
            if result.returncode != 0:
                continue
        except (OSError, subprocess.SubprocessError):
            continue
        arguments = sys.argv[1:] if argv is None else argv
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *arguments])
        return
    parser.error(
        f"Python {sys.executable} is missing renderer dependencies: {', '.join(missing)}. "
        f"Start with {REPO / 'tools/start-local-render-web.sh'} to prepare its environment.")


def renderer_info(renderer_repo: Path) -> dict:
    """Read target identity at startup/rescan, never on progress polling."""
    info = {"path": str(renderer_repo), "branch": "unknown", "head": "unknown"}
    for key, arguments in (("branch", ["branch", "--show-current"]),
                           ("head", ["rev-parse", "--short", "HEAD"])):
        try:
            result = subprocess.run(["git", "-C", str(renderer_repo), *arguments],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, timeout=2, check=False)
            if result.returncode == 0 and result.stdout.strip():
                info[key] = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass  # A checkout without readable Git metadata can still render.
    return info


def replay_header(path: Path):
    # Script invocation puts tools/ on sys.path; import only the existing parser.
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from osu_std_renderer.replay import parse_replay

    return parse_replay(path)[1]


@dataclass
class Entry:
    id: str
    label: str
    path: Path
    details: dict = field(default_factory=dict)
    source: Path | None = None  # Exact .osu identity, passed directly to the standard CLI.

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, **self.details}


@dataclass
class Catalog:
    replays: dict[str, Entry] = field(default_factory=dict)
    beatmaps: dict[str, Entry] = field(default_factory=dict)
    skins: dict[str, Entry] = field(default_factory=dict)
    diagnostics: deque = field(default_factory=lambda: deque(maxlen=80))

    def public(self) -> dict:
        return {"replays": [e.public() for e in self.replays.values()],
                "beatmaps": [e.public() for e in self.beatmaps.values()],
                "skins": [e.public() for e in self.skins.values()],
                "diagnostics": list(self.diagnostics)}


def ini_sections(text: str) -> dict[str, dict[str, str]]:
    """Read only lightweight header facts, without invoking beatmap conversion."""
    sections = {}
    section = ""
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith(("//", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].casefold()
            if section == "hitobjects":
                break
            sections.setdefault(section, {})
        elif ":" in line:
            key, _, value = line.partition(":")
            sections.setdefault(section, {})[key.strip().casefold()] = value.strip()
    return sections


def beatmap_metadata(data: bytes, relative: Path) -> dict:
    sections = ini_sections(data.decode("utf-8-sig", errors="replace"))
    metadata = sections.get("metadata", {})
    details = {key: metadata.get(key, "") for key in ("artist", "title", "creator", "version")}
    try:
        mode = int(sections.get("general", {}).get("mode", "0"))
    except ValueError:
        mode = None
    try:
        circle_size = float(sections.get("difficulty", {}).get("circlesize", ""))
        if not math.isfinite(circle_size):
            circle_size = None
    except ValueError:
        circle_size = None
    title = " - ".join(filter(None, (details["artist"], details["title"]))) or relative.stem
    if details["version"]:
        title += f" [{details['version']}]"
    source_info = f"Mode {mode} source" if mode is not None else "Unknown source mode"
    if circle_size is not None:
        cs = f"{circle_size:g}"
        source_info = f"CS {cs} · {source_info}"
    return {**details, "mode": mode, "circle_size": circle_size, "display_title": title,
            "source_info": source_info, "filename": relative.name,
            "relative_path": relative.as_posix(), "relative_folder": relative.parent.as_posix()}


def scan_fixtures(roots: tuple[Path, ...], skin_roots: tuple[Path, ...] = ()) -> Catalog:
    catalog = Catalog()
    hashes = {}
    hash_cache = {}
    for root in dict.fromkeys((*roots, *skin_roots)):
        if not root.is_dir():
            catalog.diagnostics.append(f"Search root is absent: {root}")
            continue
        for directory, children, files in os.walk(root, followlinks=False):
            children.sort()
            folder = Path(directory)
            for name in sorted(files):
                path = folder / name
                if name.lower() != "skin.ini" and path.suffix.lower() not in (".osu", ".osr"):
                    continue
                if not confined(path, (root,)) or not path.is_file():
                    continue
                relative = path.relative_to(root)
                label = relative.as_posix()
                try:
                    if name.lower() == "skin.ini":
                        key = catalog_id("skin", folder)
                        if key not in catalog.skins:
                            general = ini_sections(path.read_text(
                                encoding="utf-8-sig", errors="replace")).get("general", {})
                            relative_folder = relative.parent.as_posix()
                            display_name = general.get("name", "") or relative_folder
                            skin_label = (f"{display_name} · {relative_folder}" if
                                          display_name != relative_folder else relative_folder)
                            catalog.skins[key] = Entry(key, skin_label, folder, {
                                "name": display_name, "relative_path": relative_folder})
                    if root not in roots:
                        continue
                    if path.suffix.lower() == ".osu":
                        key = catalog_id("map", path)
                        if key in catalog.beatmaps:
                            continue
                        canonical = path.resolve()
                        if canonical not in hash_cache:
                            data = path.read_bytes()
                            hash_cache[canonical] = (hashlib.md5(data).hexdigest(), data)
                        digest, data = hash_cache[canonical]
                        details = {**beatmap_metadata(data, relative), "md5": digest}
                        details.update(enabled=details["mode"] == 0, error=None)
                        if not details["enabled"]:
                            details["error"] = "Only osu!standard (mode 0) beatmaps can run"
                        map_label = " · ".join((details["display_title"], details["source_info"],
                                                details["relative_folder"]))
                        catalog.beatmaps[key] = Entry(key, map_label, folder, details, path)
                        if not details["enabled"]:
                            continue
                        if digest in hashes:
                            first = catalog.beatmaps[hashes[digest]].details["relative_path"]
                            catalog.diagnostics.append(
                                f"Duplicate beatmap MD5: {label}; preferring {first}")
                        else:
                            hashes[digest] = key
                    elif path.suffix.lower() == ".osr":
                        key = catalog_id("replay", path)
                        if key in catalog.replays:
                            continue
                        details = {"enabled": False, "error": None, "mode": None,
                                   "relative_path": label, "filename": path.name,
                                   "modified": path.stat().st_mtime}
                        try:
                            replay = replay_header(path)
                            details.update(player=replay.player_name,
                                           beatmap_md5=replay.beatmap_md5,
                                           mods=int(replay.mods), mode=replay.mode,
                                           enabled=replay.mode == 0)
                            if replay.mode != 0:
                                details["error"] = "Only osu!standard (mode 0) replays can run"
                        except Exception as exc:  # Bad fixtures must not break discovery.
                            details.update(error=str(exc)[:300], mode=getattr(exc, "mode", None))
                        replay_label = path.name
                        if details.get("player"):
                            replay_label += " · " + details["player"]
                        catalog.replays[key] = Entry(key, replay_label, path, details)
                except OSError as exc:
                    catalog.diagnostics.append(f"{label}: {exc}")
    for replay in catalog.replays.values():
        replay.details["beatmap_id"] = hashes.get(replay.details.get("beatmap_md5"))
    catalog.replays = dict(sorted(catalog.replays.items(), key=lambda item: (
        -item[1].details["modified"], item[1].details["relative_path"].casefold(), item[0])))
    catalog.skins = dict(sorted(catalog.skins.items(), key=lambda item: (
        item[1].details["name"].casefold(), item[1].details["relative_path"].casefold(), item[0])))
    return catalog


class LogTail:
    """Bound both logical lines and an unterminated line; recognise live CR progress."""
    def __init__(self):
        self.lines = deque(maxlen=80)
        self.pending = ""
        self.progress = 0

    def feed(self, text: str) -> None:
        pieces = re.split(r"[\r\n]", self.pending + text)
        self.pending = pieces.pop()[-4096:]
        for line in pieces:
            if line:
                self.lines.append(line[-1000:])
        matches = PROGRESS.findall("\n".join((*pieces, self.pending)))
        if matches:
            self.progress = min(100, int(matches[-1]))

    def finish(self) -> None:
        if self.pending:
            self.lines.append(self.pending[-1000:])
            self.pending = ""


@dataclass
class Job:
    id: str
    filename: str
    labels: dict
    started: float = field(default_factory=time.time)
    state: str = "rendering"
    ended: float | None = None
    exit_code: int | None = None
    log: LogTail = field(default_factory=LogTail)
    cancel_requested: bool = False
    cancel_complete: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    worker: threading.Thread | None = None

    def public(self) -> dict:
        return {"id": self.id, "state": self.state, "progress": self.log.progress,
                "started": timestamp(self.started),
                "ended": timestamp(self.ended) if self.ended else None,
                "elapsed_seconds": round((self.ended or time.time()) - self.started, 1),
                "labels": self.labels, "output_filename": self.filename,
                "exit_code": self.exit_code, "log_tail": list(self.log.lines),
                "cancelling": self.cancel_requested and self.state == "rendering"}


def terminate_group(process: subprocess.Popen) -> None:
    """Kill the session's group, including children surviving the parent exit."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, 0)
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


class Dashboard:
    def __init__(self, repo: Path, roots: tuple[Path, ...], skin_roots: tuple[Path, ...],
                 output_dir: Path, history: int = 10, *, renderer_repo: Path | None = None):
        self.repo = repo.resolve()
        self.renderer_repo = (validate_renderer_repo(renderer_repo)
                              if renderer_repo is not None else self.repo)
        self.renderer_executable = (str(renderer_python(self.renderer_repo))
                                    if renderer_repo is not None else sys.executable)
        self.renderer = renderer_info(self.renderer_repo)
        self.roots = tuple(p.resolve() for p in roots)
        self.skin_roots = tuple(p.resolve() for p in skin_roots)
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.history_limit = history
        self.lock = threading.RLock()
        self.catalog = scan_fixtures(self.roots, self.skin_roots)
        self.job: Job | None = None
        self.labels: dict[str, str] = {}
        self.latest: str | None = None
        self.refresh_history()
        if self.history:
            self.latest = self.history[0]["filename"]

    def rescan(self) -> dict:
        catalog = scan_fixtures(self.roots, self.skin_roots)
        renderer = renderer_info(self.renderer_repo)
        with self.lock:
            self.catalog = catalog
            self.renderer = renderer
            return self.catalog_public()

    def catalog_public(self) -> dict:
        with self.lock:
            return {**self.catalog.public(), "renderer": dict(self.renderer),
                    "roots": [str(root) for root in self.roots]}

    def output_path(self, name: str) -> Path:
        if (not name or Path(name).name != name or "\\" in name or "\0" in name
                or Path(name).suffix.lower() != ".mp4"):
            raise APIError(404, "Video not found")
        path = self.output_dir / name
        if path.is_symlink() or path.resolve().parent != self.output_dir:
            raise APIError(404, "Video not found")
        return path

    def refresh_history(self, *, prune: bool = False) -> list[dict]:
        with self.lock:
            files = []
            for path in self.output_dir.glob("*.mp4"):
                try:
                    if (self.job and self.job.state != "done"
                            and path.name == self.job.filename):
                        continue
                    if self.output_path(path.name).is_file():
                        files.append((path, path.stat()))
                except (OSError, APIError):
                    continue
            files.sort(key=lambda item: item[1].st_mtime, reverse=True)
            if prune:
                generated = [p for p, _ in files if GENERATED.fullmatch(p.name)]
                for path in generated[self.history_limit:]:
                    try:
                        self.output_path(path.name).unlink(missing_ok=True)
                    except (OSError, APIError) as exc:
                        if self.job:
                            self.job.log.lines.append(f"History cleanup: {exc}"[:1000])
                files = [(p, s) for p, s in files if p.exists()]
            names = {p.name for p, _ in files}
            self.labels = {name: label for name, label in self.labels.items() if name in names}
            self.history = [{"filename": p.name, "label": self.labels.get(p.name, p.name),
                             "timestamp": timestamp(s.st_mtime), "size": s.st_size,
                             "url": "/renders/" + quote(p.name, safe="")}
                            for p, s in files[:self.history_limit]]
            return self.history

    def status(self) -> dict:
        with self.lock:
            result = self.job.public() if self.job else {"state": "idle", "progress": 0}
            result["latest"] = next((r for r in self.history if r["filename"] == self.latest), None)
            return result

    def _delete_finished_output(self, filename: str) -> None:
        if self.job and self.job.state == "rendering" and self.job.filename == filename:
            raise APIError(409, "This render is active. Cancel it before deleting its output.")
        path = self.output_path(filename)
        if self.renderer_repo != self.repo and path.is_relative_to(self.renderer_repo):
            raise APIError(403, "Cannot delete files from the external renderer checkout")
        try:
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise APIError(404, "Video not found")
            path.unlink()
        except FileNotFoundError as exc:
            raise APIError(404, "Video not found; it may already have been deleted") from exc
        except OSError as exc:
            raise APIError(500, f"Could not delete render: {exc}") from exc

    def delete_renders(self, filename: str | None = None) -> dict:
        """Serialize deletion with job launch/completion; bulk candidates stay server-owned."""
        with self.lock:
            deleted = []
            candidates = ([filename] if filename is not None else
                          [p.name for p in self.output_dir.glob("*.mp4")])
            try:
                for name in candidates:
                    try:
                        self._delete_finished_output(name)
                    except APIError as exc:
                        if filename is None and exc.status in (404, 409):
                            continue  # Bulk skips active, missing, symlink and nonregular entries.
                        raise
                    deleted.append(name)
            finally:
                self.refresh_history()
                if self.latest not in {entry["filename"] for entry in self.history}:
                    self.latest = self.history[0]["filename"] if self.history else None
            return {"deleted": deleted, "history": self.history, "latest": self.status()["latest"]}

    def _selection(self, entries: dict, key, roots: tuple[Path, ...]) -> Entry:
        entry = entries.get(key) if isinstance(key, str) else None
        if not entry or not entry.path.exists() or not confined(entry.path, roots):
            raise APIError(400, "Choose an available fixture from the catalog; rescan if needed")
        return entry

    def start(self, body: dict) -> dict:
        with self.lock:
            if self.job and self.job.state == "rendering":
                raise APIError(
                    409, "A render is already running. Cancel it or wait for it to finish.")
            allowed = {"replay", "beatmap", "skin", "resolution", "fps", "encoder",
                       "skin_hitsounds", "legacy_defaults", "renderer_default_font_and_ranks",
                       "start", "end"}
            if not isinstance(body, dict) or body.keys() - allowed:
                raise APIError(400, "Only catalog selections and displayed options are allowed")
            replay = self._selection(self.catalog.replays, body.get("replay"), self.roots)
            if not replay.details["enabled"] or not replay.path.is_file():
                raise APIError(400, "This replay is not a valid osu!standard fixture")
            beatmap = self._selection(self.catalog.beatmaps, body.get("beatmap"), self.roots)
            if not beatmap.details["enabled"]:
                raise APIError(400, "This beatmap is not a valid osu!standard fixture")
            if (not beatmap.source or not beatmap.source.is_file()
                    or not confined(beatmap.source, self.roots)):
                raise APIError(400, "Matched beatmap changed or leaves the fixture roots; rescan")
            skin = None
            if body.get("skin") is not None:
                skin = self._selection(
                    self.catalog.skins, body["skin"], self.roots + self.skin_roots)
                ini = skin.path / "skin.ini"
                if not ini.is_file() or not confined(ini, self.roots + self.skin_roots):
                    raise APIError(400, "Skin changed or leaves the configured roots; rescan")
            options = []
            for name, default, choices in (("resolution", "1280x720", RESOLUTIONS),
                                           ("fps", 60, FPS), ("encoder", "auto", ENCODERS)):
                value = body.get(name, default)
                if type(value) is not type(default) or value not in choices:
                    raise APIError(400, f"Invalid {name}; use one of {choices}")
                options.extend(("--" + name, str(value)))
            for name, flag in (("skin_hitsounds", "--use-skin-hitsounds"),
                               ("legacy_defaults", "--legacy-defaults"),
                               ("renderer_default_font_and_ranks",
                                "--renderer-default-font-and-ranks")):
                if type(body.get(name, False)) is not bool:
                    raise APIError(400, f"{name} must be true or false")
                if body.get(name):
                    options.append(flag)
            start, end = body.get("start"), body.get("end")
            for name, value in (("start", start), ("end", end)):
                if value is not None:
                    if (type(value) not in (int, float) or not math.isfinite(value)
                            or value < 0 or value > 86400):
                        raise APIError(400, f"{name} must be a number from 0 to 86400 seconds")
            if end is not None and end <= (start or 0):
                raise APIError(400, "Stop time must be after the start time")
            if start is not None:
                options.extend(("--start", str(start)))
            if end is not None:
                options.extend(("--max-seconds", str(end)))
            filename = "r3d-web-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-")
            filename += uuid.uuid4().hex[:12] + ".mp4"
            output = self.output_path(filename)
            command = [self.renderer_executable, "-m", "osu_std_renderer",
                       str(replay.path), str(beatmap.source), "-o", str(output), *options]
            if skin:
                command.extend(("--skin", str(skin.path)))
            job = Job(uuid.uuid4().hex, filename,
                      {"replay": replay.label, "beatmap": beatmap.label,
                       "skin": skin.label if skin else "None / default skin"})
            self.job = job
            try:
                job.process = subprocess.Popen(command, cwd=self.renderer_repo,
                                               stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               shell=False, start_new_session=True, bufsize=0)
            except OSError as exc:
                job.state, job.ended = "failed", time.time()
                job.log.lines.append(str(exc))
                raise APIError(500, f"Could not start renderer: {exc}") from exc
            job.worker = threading.Thread(target=self._monitor, args=(job,), daemon=True)
            job.worker.start()
            return job.public()

    def _monitor(self, job: Job) -> None:
        process = job.process
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while data := process.stdout.read(4096):
                with self.lock:
                    job.log.feed(decoder.decode(data))
            with self.lock:
                job.log.feed(decoder.decode(b"", final=True))
                job.log.finish()
            exit_code = process.wait()
        except OSError as exc:
            with self.lock:
                job.log.lines.append(f"Reading render output failed: {exc}")
            terminate_group(process)
            exit_code = process.returncode
        finally:
            process.stdout.close()
        # Keep the active slot occupied until cancellation has stopped the group.
        while True:
            with self.lock:
                if not job.cancel_requested or job.cancel_complete.is_set():
                    job.exit_code, job.ended = exit_code, time.time()
                    output = None
                    valid_output = False
                    try:
                        output = self.output_path(job.filename)
                        valid_output = output.is_file() and output.stat().st_size > 0
                    except (OSError, APIError) as exc:
                        job.log.lines.append(f"Output unavailable: {exc}"[:1000])
                    if job.cancel_requested:
                        job.state = "cancelled"
                    elif exit_code == 0 and valid_output:
                        job.state, job.log.progress = "done", 100
                        self.labels[job.filename] = job.labels["replay"]
                        self.latest = job.filename
                    else:
                        job.state = "failed"
                        if exit_code == 0:
                            job.log.lines.append("Renderer exited without a nonempty MP4")
                    if job.state != "done" and output is not None:
                        try:
                            output.unlink(missing_ok=True)
                        except OSError as exc:
                            job.log.lines.append(f"Partial output cleanup: {exc}"[:1000])
                    self.refresh_history(prune=job.state == "done")
                    break
            job.cancel_complete.wait()

    def cancel(self) -> dict:
        with self.lock:
            job = self.job
            if not job or job.state != "rendering":
                raise APIError(409, "There is no active render to cancel")
            if job.cancel_requested:
                return job.public()
            job.cancel_requested = True
        try:
            terminate_group(job.process)
        finally:
            job.cancel_complete.set()
        return self.status()


def byte_range(header: str | None, size: int) -> tuple[int, int, bool]:
    if header is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip()) if len(header) <= 128 else None
    if not match or not size or not any(match.groups()):
        raise APIError(416, "Requested byte range is not satisfiable")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    else:
        start, end = max(0, size - int(last)), size - 1
    if start > end or start >= size:
        raise APIError(416, "Requested byte range is not satisfiable")
    return start, end, True


def make_server(app: Dashboard, host: str = "0.0.0.0", port: int = 7377):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Polling should not flood the terminal.

        def send_bytes(self, status, data, content_type="application/json", headers=()):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def json(self, status, value):
            self.send_bytes(status, json.dumps(value).encode())

        def video(self, name):
            path = app.output_path(name)
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError as exc:
                raise APIError(404, "Video not found") from exc
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise APIError(404, "Video not found")
                try:
                    start, end, partial = byte_range(self.headers.get("Range"), info.st_size)
                except APIError:
                    self.send_bytes(416, b"", headers=(("Content-Range", f"bytes */{info.st_size}"),
                                                       ("Accept-Ranges", "bytes")))
                    return
                length = end - start + 1
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{info.st_size}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                stream.seek(start)
                while length:
                    chunk = stream.read(min(length, 65536))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    length -= len(chunk)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/":
                    self.send_bytes(200, PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/api/catalog":
                    self.json(200, app.catalog_public())
                elif path == "/api/status":
                    self.json(200, app.status())
                elif path == "/api/history":
                    self.json(200, app.refresh_history())
                elif path.startswith("/renders/"):
                    self.video(path.removeprefix("/renders/"))
                else:
                    raise APIError(404, "Not found")
            except APIError as exc:
                self.json(exc.status, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass  # Normal when the browser stops or seeks a video.

        do_HEAD = do_GET

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                if path not in ("/api/render", "/api/cancel", "/api/rescan",
                                "/api/renders/delete", "/api/renders/delete-old"):
                    raise APIError(404, "Not found")
                if self.headers.get_content_type() != "application/json":
                    raise APIError(400, "Send a JSON request")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        raise APIError(413, "Request body must be 1–8192 bytes")
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError) as exc:
                    raise APIError(400, "Invalid JSON request") from exc
                if not isinstance(body, dict):
                    raise APIError(400, "Expected a JSON object")
                if path == "/api/render":
                    self.json(202, app.start(body))
                elif path == "/api/renders/delete":
                    if body.keys() != {"filename"} or not isinstance(body["filename"], str):
                        raise APIError(400, "Provide only a render filename from history")
                    self.json(200, app.delete_renders(body["filename"]))
                elif body:
                    raise APIError(400, "This action expects an empty JSON object")
                elif path == "/api/cancel":
                    self.json(200, app.cancel())
                elif path == "/api/renders/delete-old":
                    self.json(200, app.delete_renders())
                else:
                    self.json(200, app.rescan())
            except APIError as exc:
                self.json(exc.status, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass

    return ThreadingHTTPServer((host, port), Handler)


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # connect only chooses a local route; no packet is sent, no DNS/external request.
            probe.connect(("255.255.255.255", 9))
            address = probe.getsockname()[0]
            return address if not address.startswith("127.") and address != "0.0.0.0" else None
    except OSError:
        return None


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7377)
    parser.add_argument("--root", type=Path, action="append")
    parser.add_argument("--skin-root", type=Path, action="append", default=[])
    parser.add_argument("--renderer-repo", type=Path,
                        help="Renderer checkout to execute (default: dashboard checkout); "
                             "prefers that checkout's executable .venv/bin/python")
    parser.add_argument("--output-dir", type=Path, default=REPO / "local-web-renders")
    parser.add_argument("--history", type=int, default=10)
    args = parser.parse_args(argv)
    if args.history < 1 or not 0 <= args.port <= 65535:
        parser.error("history must be positive; port must be between 0 and 65535")
    if args.renderer_repo is not None:
        try:
            validate_renderer_repo(args.renderer_repo)
        except ValueError as exc:
            parser.error(str(exc))
    ensure_runtime(parser, argv)
    roots = args.root if args.root is not None else (REPO / "test-render",)
    try:
        app = Dashboard(REPO, tuple(roots), tuple(args.skin_root), args.output_dir, args.history,
                        renderer_repo=args.renderer_repo)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    server = make_server(app, args.host, args.port)
    port = server.server_port
    print("R3D standard render UI", flush=True)
    print(f"  dashboard: {app.repo}", flush=True)
    print(f"  renderer:  {app.renderer['path']}", flush=True)
    print(f"  branch:    {app.renderer['branch']}", flush=True)
    print(f"  HEAD:      {app.renderer['head']}", flush=True)
    print(f"  python:    {sys.executable}", flush=True)
    print(f"  local: http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{port}",
          flush=True)
    address = lan_address() if args.host == "0.0.0.0" else None
    if address:
        print(f"  LAN:   http://{address}:{port}", flush=True)
    print(f"  outputs: {app.output_dir}", flush=True)
    print("Trusted local network only; no authentication. Ctrl+C stops the server and render.",
          flush=True)
    for diagnostic in app.catalog.diagnostics:
        print(f"  {diagnostic}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if app.job and app.job.state == "rendering":
                app.cancel()
        except APIError:
            pass  # A render may finish between checking its state and cancelling.
        finally:
            if app.job and app.job.worker:
                app.job.worker.join(timeout=3)
            server.server_close()


# Embedded mobile page keeps the tool a single file, with no frontend build step.
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>R3D · Standard render bench</title>
<style>
:root{color-scheme:dark;--bg:#10141d;--card:#1b2230;--muted:#aab6ca;--accent:#85e3bc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#f2f5fa;
font:16px/1.5 system-ui,sans-serif}main{max-width:760px;margin:auto;padding:24px 16px 48px}
h1{font-size:1.7rem;line-height:1.2;margin:4px 0}h2{font-size:1.15rem;margin:0 0 12px}
.eyebrow{font-size:.8rem;letter-spacing:.15em;color:var(--accent)}p{color:var(--muted)}
.card{background:var(--card);padding:20px;border-radius:16px;margin-top:20px}
label{display:block;margin:0 0 14px}select,button,input[type=search],input[type=number]{font:inherit;
min-height:48px;border-radius:9px}
select,input[type=search],input[type=number]{display:block;width:100%;margin-top:6px;padding:10px;background:#101824;
color:#fff;border:1px solid #45536a}button{cursor:pointer;padding:10px 16px;
border:1px solid #45536a;background:#29354a;color:#fff}button:disabled{opacity:.45;cursor:default}
.actions{display:flex;gap:12px;margin-top:18px}.actions button{flex:1}
#render{background:var(--accent);border-color:var(--accent);color:#09271d;font-weight:700}
#cancel{border-color:#f58c95;color:#ffb0b7;background:#36242d}
#rescan{width:100%}
.hint{font-size:.85rem;color:var(--muted);margin:4px 0 14px;overflow-wrap:anywhere}
.filter{margin-bottom:8px}.filter input{margin:0}.match-card{padding:12px 14px;
background:#101824;border:1px solid #45536a;border-radius:10px;margin-bottom:16px;
overflow-wrap:anywhere}.match-card p{margin:0}.match-card .hint{margin-top:4px}
#match{font-weight:700;color:#ffcd83}#match[data-kind=exact]{color:var(--accent)}
#map-title{color:#f2f5fa;margin-top:4px}#beatmap-field.matched{color:var(--muted)}
#beatmap-field.matched select{border-color:#354055;color:var(--muted)}
#beatmap-field.matched:focus-within select{color:#fff;border-color:var(--accent)}
#beatmap-field.needs-map select{border-color:#ffcd83}
progress{width:100%;height:14px;accent-color:var(--accent)}#status{overflow-wrap:anywhere}
details{margin-top:10px}summary{cursor:pointer;color:var(--muted)}.check{margin:12px 0}
.check input{width:20px;height:20px;vertical-align:middle;margin-right:8px}
pre{font-size:.8rem;white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}
video{display:block;width:100%;max-height:70vh;background:#000;border-radius:10px}
.recent{display:flex;align-items:center;gap:12px;border-top:1px solid #354055;padding:12px 0}
.recent div{flex:1;min-width:0;overflow-wrap:anywhere}.recent small{color:var(--muted)}
.recent .recent-actions{flex:none;display:flex;gap:10px}
.recent-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;
flex-wrap:wrap}.recent-heading h2{margin:0}
.danger{border-color:#b76a75;color:#ffb0b7;background:#36242d}
#error{color:#ffb0b7;overflow-wrap:anywhere}button:focus-visible,select:focus-visible,
input:focus-visible{
outline:3px solid var(--accent);outline-offset:3px}[hidden]{display:none!important}
@media(min-width:640px){.options{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
</style></head><body><main>
<div class="eyebrow">R3D / LOCAL TOOLS</div><h1>Standard render bench</h1>
<p>Pick a local fixture. Render it. Watch it here.</p>
<section class="card" aria-label="Renderer checkout"><h2>Renderer</h2>
<div id="renderer-revision">Loading renderer…</div>
<p class="hint" id="renderer-path"></p>
<p class="hint" id="fixture-roots"></p></section>
<section class="card"><form id="form">
<label class="filter"><input type="search" id="replay-filter" placeholder="Filter replays…"
aria-label="Filter replays" autocomplete="off"></label>
<label>Replay<select id="replay" required>
<option value="">Loading fixtures…</option></select></label>
<div class="match-card" aria-live="polite"><p id="match"></p>
<p id="map-title"></p><p class="hint" id="map-details"></p>
<p class="hint" id="map-file"></p></div>
<label id="beatmap-field">Beatmap<select id="beatmap" required></select></label>
<p class="hint" id="beatmap-note"></p>
<label class="filter"><input type="search" id="skin-filter" placeholder="Filter skins…"
aria-label="Filter skins" autocomplete="off"></label>
<label>Skin<select id="skin"><option value="">None / default skin</option></select></label>
<div class="options">
<label>Resolution<select id="resolution">
<option>1280x720</option><option>1920x1080</option></select></label>
<label>FPS<select id="fps"><option>30</option><option selected>60</option></select></label>
<label>Encoder<select id="encoder"><option>auto</option><option>h264_vaapi</option>
<option>h264_nvenc</option><option>libx264</option></select></label></div>
<details><summary>Advanced</summary>
<label class="check"><input type="checkbox" id="skin_hitsounds">Use skin hitsounds</label>
<label class="check"><input type="checkbox" id="legacy_defaults">Use legacy defaults</label>
<label class="check"><input type="checkbox" id="renderer_default_font_and_ranks">Use renderer fonts and ranks</label>
<label>Start at (map seconds)<input type="number" id="start" min="0" max="86400" step="any"
placeholder="Auto / skip intro"></label>
<label>Stop at (map seconds)<input type="number" id="end" min="0" max="86400" step="any"
placeholder="End of replay"></label>
</details><div class="actions"><button id="render" disabled>Render</button>
<button id="cancel" type="button" disabled>Cancel</button></div></form>
<p id="error" role="alert" hidden></p></section>
<section class="card" aria-label="Render progress">
<div id="status" role="status" aria-live="polite">Idle</div>
<progress id="progress" max="100" value="0"></progress>
<details><summary>Render log</summary><pre id="log">No render yet.</pre></details></section>
<section class="card" id="result" hidden><h2>Finished video</h2>
<p class="hint" id="video-label"></p>
<video id="player" controls playsinline preload="metadata"></video></section>
<section class="card"><div class="recent-heading"><h2>Recent renders</h2>
<button id="delete-old" class="danger" type="button" hidden>Delete all old renders</button>
</div><div id="history"><p>No videos yet.</p></div></section>
<button id="rescan" type="button" style="margin-top:20px">Rescan fixtures</button>
<p class="hint" id="diagnostics"></p>
</main><script>
const $=id=>document.getElementById(id);
const options=['resolution','fps','encoder'];
let catalog={replays:[],beatmaps:[],skins:[]},active=false,launching=false,lastLatest='',lastJob='';
let currentVideo='',recentEntries=[],deleting=false,historyVersion=0;
let saved={};try{const value=JSON.parse(localStorage.getItem('r3d-std-render-bench')||'{}');
if(value&&typeof value==='object'&&!Array.isArray(value))saved=value}catch{}
const perReplay=saved.perReplay&&typeof saved.perReplay==='object'&&!Array.isArray(saved.perReplay)?
saved.perReplay:{};
// Migrate the old global skin choice to the replay it belonged to.
if(saved.replay&&!perReplay[saved.replay]&&typeof saved.skin==='string')
perReplay[saved.replay]={skin:saved.skin};
for(const key of options){
if([...$(key).options].some(o=>o.value===saved[key]))$(key).value=saved[key]}
function preference(id){const value=perReplay[id];
if(!value||typeof value!=='object'||Array.isArray(value))perReplay[id]={};return perReplay[id]}
function save(){saved={replay:$('replay').value,perReplay,
...Object.fromEntries(options.map(k=>[k,$(k).value]))};
try{localStorage.setItem('r3d-std-render-bench',JSON.stringify(saved))}catch{}}
function error(message){$('error').textContent=message||'';$('error').hidden=!message}
async function api(path,body){const options=body===undefined?{}:{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
const response=await fetch(path,{...options,cache:'no-store'}),data=await response.json();
if(!response.ok)throw new Error(data.error||`Request failed (${response.status})`);return data}
function selected(){return catalog.replays.find(r=>r.id===$('replay').value)}
function buttons(){
$('render').disabled=active||launching||!selected()?.enabled||!catalog.beatmaps.some(b=>b.id===$('beatmap').value&&b.enabled!==false);
$('cancel').disabled=!active}
function matchHint(){const replay=selected(),matched=catalog.beatmaps.find(
b=>b.id===replay?.beatmap_id),exact=Boolean(matched&&matched.id===$('beatmap').value);
$('match').textContent=matched?'✓ Exact replay MD5 match':'No exact beatmap match';
$('match').dataset.kind=matched?'exact':'missing';
$('map-title').textContent=matched?.display_title||'';
$('map-details').textContent=matched?`${matched.source_info} · ${matched.relative_folder}`:'';
$('map-file').textContent=matched?matched.filename:'';
$('beatmap-field').classList.toggle('matched',exact);
$('beatmap-field').classList.toggle('needs-map',!matched);
$('beatmap-note').textContent=exact?'Auto-selected. Change below only for a manual override.':
matched?'Manual beatmap override selected. The exact match is shown above.':
'Choose a beatmap manually, or add the matching .osu and rescan.';buttons()}
function fill(id,entries,empty){const select=$(id);select.replaceChildren(new Option(empty,''));
for(const entry of entries){
const option=new Option(entry.label+(entry.error?` — ${entry.error}`:''),entry.id);
option.disabled=entry.enabled===false;select.add(option)}}
function contains(text,query){return String(text||'').toLocaleLowerCase().includes(query)}
function replayOptions(keep){const query=$('replay-filter').value.trim().toLocaleLowerCase();
const entries=catalog.replays.filter(r=>{const map=catalog.beatmaps.find(b=>b.id===r.beatmap_id);
return r.id===keep||[r.filename,r.relative_path,r.player,map?.artist,map?.title,map?.version]
.some(text=>contains(text,query))});fill('replay',entries,'Choose a standard replay');
if(entries.some(r=>r.id===keep&&r.enabled))$('replay').value=keep}
function skinOptions(keep){const query=$('skin-filter').value.trim().toLocaleLowerCase();
fill('skin',catalog.skins.filter(s=>s.id===keep||[s.name,s.relative_path]
.some(text=>contains(text,query))),'None / default skin');
if(catalog.skins.some(s=>s.id===keep))$('skin').value=keep}
function chooseReplay(newMatch=false){const replay=selected();
const prefs=replay?preference(replay.id):{};
if(newMatch||!catalog.beatmaps.some(b=>b.id===prefs.beatmap))delete prefs.beatmap;
$('beatmap').value=prefs.beatmap||replay?.beatmap_id||'';
if(!catalog.skins.some(s=>s.id===prefs.skin))prefs.skin='';
skinOptions(prefs.skin||'');matchHint();save()}
function setCatalog(data,remember=saved.replay,rescanning=false){const previous=selected();
$('renderer-revision').textContent=
`${data.renderer?.branch||'unknown'} · ${data.renderer?.head||'unknown'}`;
$('renderer-path').textContent=data.renderer?.path||'';
$('fixture-roots').textContent=data.roots?.length?`Source files: ${data.roots.join(', ')}`:'';
catalog=data;const replay=data.replays.find(r=>r.id===remember&&r.enabled)||
data.replays.find(r=>r.enabled);replayOptions(replay?.id||'');
fill('beatmap',data.beatmaps,'Choose a beatmap');
// A newly discovered exact match takes over an earlier manual fallback.
const newMatch=rescanning&&previous?.id===replay?.id&&!previous?.beatmap_id&&replay?.beatmap_id;
chooseReplay(Boolean(newMatch));
const bad=data.replays.filter(r=>!r.enabled).length;
$('diagnostics').textContent=[bad?`${bad} unavailable replay(s).`:'',...data.diagnostics].join(' ');
}
function video(entry,play=false){currentVideo=entry.filename;
$('result').hidden=false;$('player').src=entry.url;
$('video-label').textContent=entry.label;
if(play){$('player').play().catch(()=>{});$('result').scrollIntoView({behavior:'smooth',block:'center'})}}
function clearVideo(){const player=$('player');player.pause();player.removeAttribute('src');
player.load();currentVideo='';$('video-label').textContent='';$('result').hidden=true}
function showHistory(entries){recentEntries=entries;$('history').replaceChildren();
$('delete-old').hidden=!entries.length;$('delete-old').disabled=deleting||!entries.length;
if(!entries.length){$('history').textContent='No videos yet.';return}
for(const entry of entries){const row=document.createElement('div');row.className='recent';
const text=document.createElement('div'),label=document.createElement('div');
const meta=document.createElement('small');label.textContent=entry.label;
meta.textContent=`${new Date(entry.timestamp).toLocaleString()} · `+
`${(entry.size/1048576).toFixed(1)} MB`;
text.append(label,meta);const play=document.createElement('button');play.textContent='Play';
play.onclick=()=>video(entry,true);
const remove=document.createElement('button');remove.textContent='Delete';
remove.className='danger';remove.disabled=deleting;remove.onclick=()=>deleteRender(entry.filename);
const controls=document.createElement('div');controls.className='recent-actions';
controls.append(play,remove);row.append(text,controls);$('history').append(row)}}
async function history(){const version=historyVersion,entries=await api('/api/history');
if(version===historyVersion)showHistory(entries)}
function deletedRenders(data){historyVersion++;lastLatest=data.latest?.filename||'';
if(!currentVideo||data.deleted.includes(currentVideo)){
clearVideo();if(data.latest)video(data.latest)}showHistory(data.history)}
async function deleteRender(filename=null){if(deleting)return;
if(!confirm(filename===null?'Delete all saved renders from this dashboard?':
'Delete this render?'))return;
deleting=true;showHistory(recentEntries);error('');
try{deletedRenders(await api(filename===null?'/api/renders/delete-old':'/api/renders/delete',
filename===null?{}:{filename}))}
catch(exc){error(exc.message);await history()}
finally{deleting=false;showHistory(recentEntries)}}
$('delete-old').onclick=()=>deleteRender();
async function poll(){const version=historyVersion;try{const job=await api('/api/status');
if(version!==historyVersion)return;
active=job.state==='rendering';buttons();
$('progress').value=job.progress||0;
$('status').textContent=job.state==='idle'?'Idle — ready to render.':
`${job.cancelling?'Cancelling…':job.state} · ${job.progress}% · `+
`${job.elapsed_seconds}s · ${job.labels.replay}`;
$('log').textContent=(job.log_tail||[]).join('\n')||'No render log yet.';
if(job.latest&&job.latest.filename!==lastLatest){lastLatest=job.latest.filename;video(job.latest)}
if(!job.latest&&lastLatest){lastLatest='';clearVideo()}
const marker=`${job.id}:${job.state}`;if(marker!==lastJob){lastJob=marker;await history()}
}catch(exc){error(exc.message)}finally{setTimeout(poll,500)}}
$('replay').onchange=()=>chooseReplay();
$('beatmap').onchange=()=>{const replay=selected();if(replay){const prefs=preference(replay.id);
if($('beatmap').value&&$('beatmap').value!==replay.beatmap_id)prefs.beatmap=$('beatmap').value;
else delete prefs.beatmap}matchHint();save()};
$('skin').onchange=()=>{if(selected())preference(selected().id).skin=$('skin').value;save()};
for(const key of options)$(key).onchange=save;
// Keep the current selection visible even when a filter excludes it.
$('replay-filter').oninput=()=>replayOptions($('replay').value);
$('skin-filter').oninput=()=>skinOptions($('skin').value);
$('form').onsubmit=async event=>{event.preventDefault();error('');launching=true;buttons();save();
try{await api('/api/render',{replay:$('replay').value,beatmap:$('beatmap').value,
skin:$('skin').value||null,
resolution:$('resolution').value,fps:Number($('fps').value),encoder:$('encoder').value,
skin_hitsounds:$('skin_hitsounds').checked,legacy_defaults:$('legacy_defaults').checked,
renderer_default_font_and_ranks:$('renderer_default_font_and_ranks').checked,
start:$('start').value===''?null:Number($('start').value),
end:$('end').value===''?null:Number($('end').value)});active=true;
}catch(exc){error(exc.message)}finally{launching=false;buttons()}};
$('cancel').onclick=async()=>{error('');$('cancel').disabled=true;
try{await api('/api/cancel',{})}catch(exc){error(exc.message)}};
$('rescan').onclick=async()=>{error('');$('rescan').disabled=true;
try{const remember=$('replay').value;setCatalog(await api('/api/rescan',{}),remember,true)}
catch(exc){error(exc.message)}finally{$('rescan').disabled=false}};
(async()=>{try{setCatalog(await api('/api/catalog'));await history()}
catch(exc){error(exc.message)}poll()})();
</script></body></html>"""

if __name__ == "__main__":
    main()
