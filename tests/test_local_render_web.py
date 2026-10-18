"""Local dashboard tests use fake renderer processes and a loopback HTTP server."""
import hashlib
import http.client
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import local_render_web as web


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / "fixtures"
    beatmap = root / "map"
    beatmap.mkdir(parents=True)
    source = beatmap / "chart.osu"
    source.write_bytes(b"osu file format v14\n[General]\nMode:0\n")
    md5 = hashlib.md5(source.read_bytes()).hexdigest()
    (root / "play.osr").write_text("standard:" + md5)
    (root / "mania.osr").write_text("mania")
    (root / "invalid.osr").write_text("bad")
    skin = root / "skin"
    (skin / "assets").mkdir(parents=True)
    (skin / "skin.ini").write_text("[General]\nName:Test")
    skin_root = tmp_path / "skins"
    skin_root.mkdir()
    (skin_root / "skin.ini").write_text("[General]\nName:External")
    (skin_root / "not-a-fixture.osr").write_text("standard:" + md5)

    def header(path):
        text = path.read_text()
        if text == "bad":
            raise ValueError("Invalid replay")
        return SimpleNamespace(player_name="Test player", mode=3 if text == "mania" else 0,
                               beatmap_md5=text.removeprefix("standard:"), mods=0)

    monkeypatch.setattr(web, "replay_header", header)
    return web.Dashboard(tmp_path, (root,), (skin_root,), tmp_path / "outputs")


def selection(app, **options):
    return {"replay": next(e.id for e in app.catalog.replays.values() if e.details["enabled"]),
            "beatmap": next(iter(app.catalog.beatmaps)), **options}


class FakePipe:
    def __init__(self):
        self.chunks = queue.Queue()
        self.closed = False

    def read(self, _):
        return self.chunks.get(timeout=3)

    def close(self):
        self.closed = True


class FakeProcess:
    pid = 4242

    def __init__(self):
        self.stdout = FakePipe()
        self.returncode = None
        self.finished = threading.Event()

    def complete(self, code=0, data=b""):
        if self.returncode is None:
            self.returncode = code
            if data:
                self.stdout.chunks.put(data)
            self.stdout.chunks.put(b"")
            self.finished.set()

    def wait(self, timeout=None):
        if not self.finished.wait(timeout):
            raise subprocess.TimeoutExpired("fake-render", timeout)
        return self.returncode


@pytest.fixture
def processes(monkeypatch):
    launched = []
    # subprocess.run also uses Popen; keep Git probes out of the fake renderer list.
    monkeypatch.setattr(web, "renderer_info", lambda repo: {
        "path": str(repo), "branch": "unknown", "head": "unknown"})

    def launch(command, **kwargs):
        process = FakeProcess()
        launched.append((process, command, kwargs))
        return process

    monkeypatch.setattr(web.subprocess, "Popen", launch)
    yield launched
    for process, _, _ in launched:
        process.complete(1)


def joined(app, state):
    app.job.worker.join(timeout=2)
    assert not app.job.worker.is_alive()
    assert app.status()["state"] == state


def test_discovery_and_exact_md5_auto_match(app):
    catalog = app.catalog.public()
    assert len(catalog["replays"]) == 3
    assert len(catalog["beatmaps"]) == 1
    assert len(catalog["skins"]) == 2  # Asset child directories are not skins.
    replay = next(e for e in catalog["replays"] if e["enabled"])
    assert replay["player"] == "Test player" and replay["mods"] == 0 and replay["mode"] == 0
    assert replay["beatmap_id"] == catalog["beatmaps"][0]["id"]
    assert str(app.roots[0]) not in json.dumps(catalog)
    assert "path" not in replay


def test_main_root_discovers_all_resources_without_skin_root(app):
    catalog = web.scan_fixtures(app.roots).public()
    assert len(catalog["replays"]) == 3 and len(catalog["beatmaps"]) == 1
    assert len(catalog["skins"]) == 1
    replay = next(e for e in catalog["replays"] if e["enabled"])
    assert replay["label"] == "play.osr · Test player"
    assert replay["relative_path"] == "play.osr"
    assert catalog["skins"][0]["label"] == "Test · skin"
    assert all(not e["label"].startswith("fixtures/") for kind in
               ("replays", "beatmaps", "skins") for e in catalog[kind])


def test_individual_osu_metadata_and_exact_match_win_over_filename(app):
    folder = next(iter(app.catalog.beatmaps.values())).path
    original = folder / "chart.osu"
    original.write_text("\ufeffosu file format v14\n[General]\nMode:0\n"
                        "[Metadata]\nArtist:Artist\nTitle:Merry Rizzmas\n"
                        "Creator:Mapper\nVersion:Insane\n[Difficulty]\nCircleSize:7\n")
    md5 = hashlib.md5(original.read_bytes()).hexdigest()
    (app.roots[0] / "play.osr").write_text("standard:" + md5)
    (folder / "play.osu").write_text("[General]\nMode:3\n[Difficulty]\nCircleSize:4\n"
                                   "[Metadata]\nTitle:Wrong filename match\nVersion:Easy\n")
    data = app.rescan()
    assert len(data["beatmaps"]) == 2
    replay = next(e for e in data["replays"] if e["enabled"])
    exact = next(e for e in data["beatmaps"] if e["id"] == replay["beatmap_id"])
    assert exact["md5"] == md5 and exact["filename"] == "chart.osu"
    assert exact["relative_folder"] == "map" and exact["relative_path"] == "map/chart.osu"
    assert (exact["artist"], exact["title"], exact["creator"], exact["version"]) == (
        "Artist", "Merry Rizzmas", "Mapper", "Insane")
    assert exact["mode"] == 0 and exact["circle_size"] == 7
    assert "Merry Rizzmas [Insane]" in exact["label"] and "map" in exact["label"]
    assert "7K" not in exact["label"] and "key_count" not in exact
    native = next(e for e in data["beatmaps"] if e["id"] != exact["id"])
    assert "CS 4" in native["label"] and not native["enabled"]


def test_duplicate_md5_paths_choose_deterministic_first_and_diagnose(app):
    source = next(iter(app.catalog.beatmaps.values())).path / "chart.osu"
    for name in ("z-copy", "a-copy"):
        folder = app.roots[0] / name
        folder.mkdir()
        (folder / "copy.osu").write_bytes(source.read_bytes())
    first = app.rescan()
    second = app.rescan()
    assert first == second and len(first["beatmaps"]) == 3
    replay = next(e for e in first["replays"] if e["enabled"])
    preferred = next(e for e in first["beatmaps"] if e["id"] == replay["beatmap_id"])
    assert preferred["relative_path"] == "a-copy/copy.osu"
    duplicates = [d for d in first["diagnostics"] if "Duplicate beatmap MD5" in d]
    assert len(duplicates) == 2 and all("a-copy/copy.osu" in d for d in duplicates)


def test_replay_catalog_is_newest_first_with_newest_valid_candidate(app):
    root = app.roots[0]
    for name, modified in (("play.osr", 10), ("mania.osr", 40), ("invalid.osr", 30)):
        os.utime(root / name, (modified, modified))
    (root / "recent.osr").write_text("standard:unmatched")
    os.utime(root / "recent.osr", (20, 20))
    entries = app.rescan()["replays"]
    assert [e["relative_path"] for e in entries] == [
        "mania.osr", "invalid.osr", "recent.osr", "play.osr"]
    assert next(e for e in entries if e["enabled"])["relative_path"] == "recent.osr"


def test_skin_names_and_case_insensitive_sort_with_path_fallback(app):
    for folder, content in (("z-skin", "\ufeff[General]\nName:argon Test\n"),
                            ("b-skin", "[General]\nName:Argon Test\n"),
                            ("unnamed", "[Mania]\nName:Not the skin name\n")):
        skin = app.roots[0] / folder
        skin.mkdir()
        (skin / "skin.ini").write_text(content)
    entries = app.rescan()["skins"]
    assert [e["relative_path"] for e in entries] == [
        "b-skin", "z-skin", ".", "skin", "unnamed"]
    assert entries[0]["name"] == "Argon Test" and entries[0]["label"] == "Argon Test · b-skin"
    assert entries[-1]["name"] == "unnamed" and entries[-1]["label"] == "unnamed"


def test_rescan_keeps_stable_ids_and_discovers_new_exact_match(app):
    replay = next(e for e in app.catalog.replays.values() if e.details["enabled"])
    source_bytes = b"[General]\nMode:0\n[Metadata]\nTitle:Newly matched\nVersion:Hard\n"
    replay.path.write_text("standard:" + hashlib.md5(source_bytes).hexdigest())
    before = app.rescan()
    assert next(e for e in before["replays"] if e["id"] == replay.id)["beatmap_id"] is None
    folder = app.roots[0] / "new-map"
    folder.mkdir()
    (folder / "new.osu").write_bytes(source_bytes)
    after = app.rescan()
    assert [e["id"] for e in before["replays"]] == [e["id"] for e in after["replays"]]
    assert before["skins"] == after["skins"]
    matched = next(e for e in after["replays"] if e["id"] == replay.id)["beatmap_id"]
    assert next(e for e in after["beatmaps"] if e["id"] == matched)["title"] == "Newly matched"


def test_overlapping_roots_hash_each_source_once_per_rescan(app, monkeypatch):
    source = next(iter(app.catalog.beatmaps.values())).path / "chart.osu"
    reads = []
    original = Path.read_bytes

    def read_bytes(path):
        if path == source:
            reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    catalog = web.scan_fixtures((app.roots[0], source.parent))
    assert len(reads) == 1 and len(catalog.beatmaps) == 1


def test_invalid_and_mania_replays_are_disabled(app, processes):
    for entry in app.catalog.replays.values():
        if not entry.details["enabled"]:
            assert entry.details["error"]
            with pytest.raises(web.APIError, match="not a valid osu!standard"):
                app.start(selection(app, replay=entry.id))
    assert not processes


def test_existing_renderer_parser_reads_standard_and_rejects_invalid_replays(tmp_path):
    from tests.test_leadin_offset import _build_osr

    synthetic = _build_osr("0|256|192|0,16|260|192|4,-12345|0|0|42,")
    try:
        header = web.replay_header(synthetic)
        assert header.mode == 0 and header.player_name == "tester"
        assert header.beatmap_md5 == "0" * 32
    finally:
        synthetic.unlink()
        synthetic.parent.rmdir()
    invalid = tmp_path / "invalid.osr"
    invalid.write_bytes(b"invalid replay")
    with pytest.raises(Exception, match="osrparse failed"):
        web.replay_header(invalid)


@pytest.mark.parametrize("field,value", [
    ("replay", "../../outside.osr"), ("beatmap", "/tmp/arbitrary"),
    ("skin", "map-does-not-exist"), ("replay", []), ("encoder", "auto; touch /tmp/pwn"),
    ("encoder", []), ("resolution", "4000x4000"), ("fps", 120), ("fps", "60"),
    ("skin_hitsounds", "true"), ("legacy_defaults", 1), ("renderer_default_font_and_ranks", "yes"),
    ("start", -1), ("start", True), ("end", "10"), ("end", float("nan")),
    ("start", float("inf")), ("end", 86401), ("end", 0), ("no_loudnorm", True), ("extra_args", "--allow-converted"),
    ("progress_style", "pie"), ("hud_scale", 1.25),
])
def test_ids_and_options_cannot_accept_paths_or_command_fragments(app, processes, field, value):
    with pytest.raises(web.APIError) as error:
        app.start(selection(app, **{field: value}))
    assert error.value.status == 400 and not processes


def test_discovery_and_launch_recheck_symlink_confinement(app, processes, tmp_path):
    outside = tmp_path / "outside.osr"
    outside.write_text("standard:any")
    link = app.roots[0] / "escaped.osr"
    link.symlink_to(outside)
    app.rescan()
    assert all(e.path != link for e in app.catalog.replays.values())
    replay = next(e for e in app.catalog.replays.values() if e.details["enabled"])
    replay.path.unlink()
    replay.path.symlink_to(outside)
    with pytest.raises(web.APIError):
        app.start(selection(app))
    assert not processes


def test_beatmap_symlink_escape_is_rejected_after_scan(app, processes, tmp_path):
    folder = next(iter(app.catalog.beatmaps.values())).path
    outside = tmp_path / "outside.osu"
    outside.write_bytes(b"source outside root")
    (folder / "chart.osu").unlink()
    (folder / "chart.osu").symlink_to(outside)
    with pytest.raises(web.APIError, match="leaves the fixture roots"):
        app.start(selection(app))
    assert not processes


def test_command_is_fixed_argv_current_python_and_repo_cwd(app, processes):
    app.start(selection(app))
    process, command, options = processes[0]
    assert isinstance(command, list)
    assert command[:3] == [sys.executable, "-m", "osu_std_renderer"]
    assert command[3] == str(app.catalog.replays[selection(app)["replay"]].path)
    assert command[4] == str(app.catalog.beatmaps[selection(app)["beatmap"]].source)
    assert command[5] == "-o" and Path(command[6]).parent == app.output_dir
    assert web.GENERATED.fullmatch(Path(command[6]).name)
    assert command[7:] == ["--resolution", "1280x720", "--fps", "60", "--encoder", "auto"]
    assert options == {"cwd": app.repo, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                       "stderr": subprocess.STDOUT, "shell": False,
                       "start_new_session": True, "bufsize": 0}
    process.complete(1)
    joined(app, "failed")


def test_selected_skin_and_advanced_flags_are_forwarded(app, processes):
    skin = next(iter(app.catalog.skins.values()))
    app.start(selection(app, skin=skin.id, skin_hitsounds=True, legacy_defaults=True,
                        renderer_default_font_and_ranks=True, hit_counter=True,
                        show_progress=True, start=35, end=40,
                        resolution="1920x1080", fps=30, encoder="libx264"))
    process, command, _ = processes[0]
    assert command[-2:] == ["--skin", str(skin.path)]
    assert "--use-skin-hitsounds" in command and "--legacy-defaults" in command
    assert "--renderer-default-font-and-ranks" in command
    assert "--hit-counter" in command and command.count("--show-progress") == 1
    assert command[command.index("--start") + 1] == "35"
    assert command[command.index("--max-seconds") + 1] == "40"
    assert "--allow-converted" not in command and "--convert-to-keys" not in command
    from osu_std_renderer.cli import build_parser

    parsed = build_parser().parse_args(command[3:])
    assert parsed.skin == skin.path and parsed.use_skin_hitsounds
    assert parsed.legacy_defaults and parsed.renderer_default_font_and_ranks
    assert parsed.hit_counter and parsed.show_progress and parsed.progress_style == "pie"
    assert parsed.start == 35 and parsed.max_seconds == 40
    process.complete(1)
    joined(app, "failed")


@pytest.mark.parametrize("value", [None, 0, 1, 0.0, 1.0, "true", "false", "--hit-counter", [], {}])
@pytest.mark.parametrize("field", ["hit_counter", "show_progress"])
def test_advanced_toggles_require_a_boolean_before_launch(app, processes, field, value):
    with pytest.raises(web.APIError, match=f"{field} must be true or false") as error:
        app.start(selection(app, **{field: value}))
    assert error.value.status == 400
    assert not processes and app.job is None


def test_manual_difficulty_override_passes_selected_osu_file(app, processes):
    folder = next(iter(app.catalog.beatmaps.values())).path
    source = folder / "alternate.osu"
    source.write_text("[General]\nMode:0\n[Metadata]\nVersion:Alternate\n")
    app.rescan()
    alternate = next(e for e in app.catalog.beatmaps.values() if e.source == source)
    app.start(selection(app, beatmap=alternate.id))
    process, command, _ = processes[0]
    assert command[4] == str(source)
    assert app.job.labels["beatmap"] == alternate.label
    process.complete(1)
    joined(app, "failed")


@pytest.mark.parametrize("mode", [1, 2, 3, "invalid"])
def test_nonstandard_maps_are_disabled_and_cannot_launch(app, processes, mode):
    folder = next(iter(app.catalog.beatmaps.values())).path
    source = folder / "unsupported.osu"
    source.write_text(f"[General]\nMode:{mode}\n")
    app.rescan()
    unsupported = next(e for e in app.catalog.beatmaps.values() if e.source == source)
    assert not unsupported.details["enabled"] and unsupported.details["error"]
    with pytest.raises(web.APIError, match="not a valid osu!standard"):
        app.start(selection(app, beatmap=unsupported.id))
    assert not processes


@pytest.mark.parametrize("start,end", [(10, 10), (20, 10)])
def test_clip_stop_must_follow_start(app, processes, start, end):
    with pytest.raises(web.APIError, match="Stop time must be after"):
        app.start(selection(app, start=start, end=end))
    assert not processes


def test_one_active_job_conflicts_without_second_subprocess(app, processes):
    app.start(selection(app))
    with pytest.raises(web.APIError, match="already running") as error:
        app.start(selection(app))
    assert error.value.status == 409 and len(processes) == 1
    processes[0][0].complete(1)
    joined(app, "failed")


def test_live_cr_progress_and_bounded_log_tail():
    log = web.LogTail()
    for chunk in ["loading\n\rrender", "ing… 4", "2%\r", "rendering… 79%"]:
        log.feed(chunk)
    assert log.progress == 79
    assert list(log.lines) == ["loading", "rendering… 42%"]
    for i in range(200):
        log.feed(f"line {i}\n")
    log.feed("x" * 100_000)
    assert len(log.lines) == 80 and len(log.pending) <= 4096
    log.finish()
    assert len(log.lines) == 80 and len(log.lines[-1]) <= 1000


def test_monitor_captures_split_utf8_progress_and_success(app, processes):
    app.start(selection(app))
    process = processes[0][0]
    encoded = "\rrendering… 42%".encode()
    process.stdout.chunks.put(encoded[:11])
    process.stdout.chunks.put(encoded[11:])
    deadline = time.monotonic() + 2
    while app.status()["progress"] != 42 and time.monotonic() < deadline:
        time.sleep(.01)
    assert app.status()["progress"] == 42
    app.output_path(app.job.filename).write_bytes(b"test mp4")
    process.complete(0, b"\rrendering... 100%")
    joined(app, "done")
    result = app.status()
    assert result["progress"] == 100 and result["exit_code"] == 0
    assert result["started"] and result["ended"] and result["elapsed_seconds"] >= 0
    assert result["latest"]["filename"] == app.job.filename


def test_zero_exit_without_output_is_failed(app, processes):
    app.start(selection(app))
    processes[0][0].complete(0)
    joined(app, "failed")
    assert "without a nonempty MP4" in app.status()["log_tail"][-1]


def test_launch_failure_is_reported_and_releases_active_slot(app, monkeypatch):
    def missing(*_, **__):
        raise OSError("Cannot launch Python")

    monkeypatch.setattr(web.subprocess, "Popen", missing)
    with pytest.raises(web.APIError) as error:
        app.start(selection(app))
    assert error.value.status == 500 and app.status()["state"] == "failed"
    assert app.status()["ended"]


def test_replaced_output_symlink_fails_without_exposing_target(app, processes, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    app.start(selection(app))
    (app.output_dir / app.job.filename).symlink_to(outside)
    processes[0][0].complete(0)
    joined(app, "failed")
    assert outside.read_bytes() == b"secret" and not app.refresh_history()


def test_cancel_signals_group_including_child_after_parent_exit(app, processes, monkeypatch):
    older = app.output_dir / "older-success.mp4"
    older.write_bytes(b"previous success")
    app.refresh_history()
    app.latest = older.name
    app.start(selection(app))
    partial = app.output_path(app.job.filename)
    partial.write_bytes(b"partial new render")
    process = processes[0][0]
    signals = []

    def kill_group(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGTERM:
            process.complete(-signal.SIGTERM)
        # Signal 0 succeeds: fake ffmpeg is still alive after its parent exits.

    monkeypatch.setattr(web.os, "killpg", kill_group)
    app.cancel()
    joined(app, "cancelled")
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, 0),
                       (process.pid, signal.SIGKILL)]
    assert older.read_bytes() == b"previous success" and not partial.exists()
    assert app.status()["latest"]["filename"] == older.name


def test_cancel_retains_active_slot_until_group_shutdown_finishes(app, processes, monkeypatch):
    app.start(selection(app))
    entered, release = threading.Event(), threading.Event()
    process = processes[0][0]

    def kill_group(_, sig):
        if sig == signal.SIGTERM:
            process.complete(-15)
        if sig == 0:
            entered.set()
            assert release.wait(2)

    monkeypatch.setattr(web.os, "killpg", kill_group)
    canceller = threading.Thread(target=app.cancel)
    canceller.start()
    assert entered.wait(2)
    try:
        assert app.status()["state"] == "rendering" and app.status()["cancelling"]
        with pytest.raises(web.APIError) as error:
            app.start(selection(app))
        assert error.value.status == 409
    finally:
        release.set()
        canceller.join(2)
    joined(app, "cancelled")


def test_group_timeout_escalates_and_missing_group_is_harmless(monkeypatch):
    process = FakeProcess()
    signals = []

    def kill_group(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            process.complete(-9)

    original_wait = process.wait
    waits = []

    def wait(timeout=None):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return original_wait(timeout)

    process.wait = wait
    monkeypatch.setattr(web.os, "killpg", kill_group)
    web.terminate_group(process)
    assert signals[-1] == (process.pid, signal.SIGKILL) and waits == [2, 2]

    def missing(*_):
        raise ProcessLookupError

    monkeypatch.setattr(web.os, "killpg", missing)
    web.terminate_group(process)


def test_startup_history_sort_and_pruning_stay_inside_output_dir(app, tmp_path):
    generated = []
    for i in range(4):
        path = app.output_dir / f"r3d-web-20261006-000000-{i:012x}.mp4"
        path.write_bytes(bytes([i]))
        os.utime(path, (i + 1, i + 1))
        generated.append(path)
    manual = app.output_dir / "keep me #1.mp4"
    manual.write_bytes(b"manual")
    os.utime(manual, (0, 0))
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"never delete")
    link = app.output_dir / "r3d-web-20261006-000000-ffffffffffff.mp4"
    link.symlink_to(outside)
    restarted = web.Dashboard(app.repo, app.roots, app.skin_roots, app.output_dir, history=2)
    assert restarted.history[0]["filename"] == generated[-1].name
    assert restarted.status()["latest"]["filename"] == generated[-1].name
    restarted.refresh_history(prune=True)
    assert [p.exists() for p in generated] == [False, False, True, True]
    assert manual.exists() and outside.read_bytes() == b"never delete"
    assert link.is_symlink() and len(restarted.history) == 2


@pytest.fixture
def server(app):
    instance = web.make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=instance.serve_forever,
                              kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(2)


def request(server, path, *, method="GET", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


@pytest.mark.parametrize("header,start,end", [
    ("bytes=10-19", 10, 19), ("bytes=60-", 60, 63), ("bytes=-5", 59, 63),
    ("bytes=60-1000", 60, 63), ("bytes=-1000", 0, 63),
])
def test_video_ranges_have_correct_headers_and_slice(app, server, header, start, end):
    data = bytes(range(64))
    (app.output_dir / "test.mp4").write_bytes(data)
    status, headers, content = request(server, "/renders/test.mp4", headers={"Range": header})
    assert status == 206 and content == data[start:end + 1]
    assert headers["Content-Range"] == f"bytes {start}-{end}/64"
    assert headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "video/mp4"
    assert int(headers["Content-Length"]) == end - start + 1


@pytest.mark.parametrize("header", ["bytes=64-", "bytes=10-4", "bytes=-0", "bytes=-",
                                    "bytes=0-1,4-5", "other=0-1", "bytes=" + "9" * 200 + "-"])
def test_unsatisfiable_ranges_return_416(app, server, header):
    (app.output_dir / "test.mp4").write_bytes(b"1234")
    status, headers, content = request(server, "/renders/test.mp4", headers={"Range": header})
    assert status == 416 and headers["Content-Range"] == "bytes */4" and content == b""


def test_full_mp4_get_and_head(app, server):
    (app.output_dir / "test.mp4").write_bytes(b"complete video")
    status, headers, content = request(server, "/renders/test.mp4")
    assert status == 200 and headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes" and content == b"complete video"
    status, headers, content = request(server, "/renders/test.mp4", method="HEAD")
    assert status == 200 and headers["Content-Length"] == "14" and content == b""


@pytest.mark.parametrize("path", ["/renders/../outside.mp4", "/renders/%2e%2e%2foutside.mp4",
                                  "/renders/%2fetc/passwd.mp4", "/renders/a%5coutside.mp4",
                                  "/renders/test.txt", "/.git/config", "/unknown"])
def test_http_never_exposes_arbitrary_files(app, server, tmp_path, path):
    (tmp_path / "outside.mp4").write_bytes(b"secret")
    assert request(server, path)[0] == 404


def test_http_output_symlink_is_rejected(app, server, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    (app.output_dir / "linked.mp4").symlink_to(outside)
    assert request(server, "/renders/linked.mp4")[0] == 404


def test_http_rejects_nonregular_video_files_without_blocking(app, server):
    os.mkfifo(app.output_dir / "pipe.mp4")
    assert request(server, "/renders/pipe.mp4")[0] == 404


def test_catalog_status_history_and_rescan_api(app, server):
    assert json.loads(request(server, "/api/status")[2])["state"] == "idle"
    assert len(json.loads(request(server, "/api/catalog")[2])["replays"]) == 3
    assert json.loads(request(server, "/api/history")[2]) == []
    (app.roots[0] / "new.osr").write_text("standard:unmatched")
    status, _, body = request(server, "/api/rescan", method="POST", body="{}",
                              headers={"Content-Type": "application/json"})
    assert status == 200 and len(json.loads(body)["replays"]) == 4


def test_render_and_conflict_http_response(app, server, processes):
    payload = json.dumps(selection(app))
    headers = {"Content-Type": "application/json"}
    status, _, body = request(server, "/api/render", method="POST", body=payload, headers=headers)
    assert status == 202 and json.loads(body)["state"] == "rendering"
    status, _, body = request(server, "/api/render", method="POST", body=payload, headers=headers)
    assert status == 409 and "already running" in json.loads(body)["error"]
    processes[0][0].complete(1)
    joined(app, "failed")


@pytest.mark.parametrize("field,flag", [
    ("hit_counter", "--hit-counter"), ("show_progress", "--show-progress"),
])
@pytest.mark.parametrize("value", [None, False, True])
def test_render_api_advanced_toggle_maps_to_existing_cli_flag(app, server, processes, field, flag, value):
    options = {} if value is None else {field: value}
    enabled = value is True
    payload = json.dumps(selection(app, **options))
    status, _, body = request(server, "/api/render", method="POST", body=payload,
                              headers={"Content-Type": "application/json"})
    assert status == 202 and json.loads(body)["state"] == "rendering"
    process, command, launch_options = processes[0]
    assert command.count(flag) == int(enabled)
    assert "--no-" + flag[2:] not in command
    assert launch_options["shell"] is False
    from osu_std_renderer.cli import build_parser

    parsed = build_parser().parse_args(command[3:])
    assert getattr(parsed, field) is enabled
    assert parsed.progress_style == "pie" and "--progress-style" not in command
    process.complete(1)
    joined(app, "failed")


@pytest.mark.parametrize("options", [{}, {"hit_lighting": True}, {"hit_lighting": False}],
                         ids=["omitted", "on", "off"])
def test_render_api_hit_lighting_preserves_cli_default_or_disables_it(app, server, processes, options):
    status, _, body = request(server, "/api/render", method="POST",
                              body=json.dumps(selection(app, **options)),
                              headers={"Content-Type": "application/json"})
    assert status == 202 and json.loads(body)["state"] == "rendering"
    assert len(processes) == 1
    process, command, _ = processes[0]
    enabled = options.get("hit_lighting", True)
    assert command.count("--no-hit-lighting") == int(not enabled)
    assert "--hit-lighting" not in command
    assert "--hit-counter" not in command and "--show-progress" not in command
    from osu_std_renderer.cli import build_parser

    parsed = build_parser().parse_args(command[3:])
    assert parsed.hit_lighting is enabled
    assert parsed.hit_counter is False and parsed.show_progress is False
    process.complete(1)
    joined(app, "failed")


@pytest.mark.parametrize("value", [None, 0, 1, 0.0, 1.0, "true", "false", [], [False], {}])
def test_render_api_rejects_nonboolean_hit_lighting_without_launch(app, server, processes, value):
    status, _, body = request(server, "/api/render", method="POST",
                              body=json.dumps(selection(app, hit_lighting=value)),
                              headers={"Content-Type": "application/json"})
    assert status == 400 and json.loads(body)["error"] == "hit_lighting must be true or false"
    assert not processes and app.job is None


@pytest.mark.parametrize("first_enabled", [True, False])
def test_render_api_hit_lighting_can_change_between_successive_jobs(app, server, processes, first_enabled):
    for enabled in (first_enabled, not first_enabled):
        status, _, _ = request(server, "/api/render", method="POST",
                               body=json.dumps(selection(app, hit_lighting=enabled)),
                               headers={"Content-Type": "application/json"})
        assert status == 202
        process, command, _ = processes[-1]
        assert command.count("--no-hit-lighting") == int(not enabled)
        assert "--hit-lighting" not in command
        process.complete(1)
        joined(app, "failed")
    assert len(processes) == 2
    # Only the generated output filename and the negative flag may differ.
    assert [arg for arg in processes[0][1][7:] if arg != "--no-hit-lighting"] == [
        arg for arg in processes[1][1][7:] if arg != "--no-hit-lighting"]
    assert processes[0][1][:6] == processes[1][1][:6]


@pytest.mark.parametrize("field", ["hit_counter", "show_progress"])
def test_render_api_rejects_invalid_advanced_toggle_without_launch(app, server, processes, field):
    status, _, body = request(server, "/api/render", method="POST",
                              body=json.dumps(selection(app, **{field: "true"})),
                              headers={"Content-Type": "application/json"})
    assert status == 400 and json.loads(body)["error"] == f"{field} must be true or false"
    assert not processes and app.job is None


def test_cancel_http_action_and_idle_conflict(app, server, processes, monkeypatch):
    headers = {"Content-Type": "application/json"}
    assert request(server, "/api/cancel", method="POST", body="{}", headers=headers)[0] == 409
    app.start(selection(app))
    process = processes[0][0]

    def kill_group(_, sig):
        if sig == signal.SIGTERM:
            process.complete(-15)

    monkeypatch.setattr(web.os, "killpg", kill_group)
    assert request(server, "/api/cancel", method="POST", body="{}", headers=headers)[0] == 200
    joined(app, "cancelled")


@pytest.mark.parametrize("body,content_type,status", [
    ("{}", "text/plain", 400), ("[]", "application/json", 400),
    ("not-json", "application/json", 400), ("x" * 9000, "application/json", 413),
])
def test_api_rejects_invalid_json_requests(server, body, content_type, status):
    result = request(server, "/api/render", method="POST", body=body,
                     headers={"Content-Type": content_type})
    assert result[0] == status and "error" in json.loads(result[2])


def test_mobile_page_contains_controls_inline_video_and_saved_selections(server):
    status, headers, body = request(server, "/")
    page = body.decode()
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert 'name="viewport"' in page
    assert '<video id="player" controls playsinline preload="metadata">' in page
    assert "localStorage" in page and "setTimeout(poll,500)" in page
    for name in ("replay", "beatmap", "skin", "resolution", "fps", "encoder", "render", "cancel"):
        assert f'id="{name}"' in page
    assert "None / default skin" in page and "Rescan fixtures" in page
    assert "Delete all old renders" in page and 'id="delete-old"' in page
    assert "remove.textContent='Delete'" in page
    for name in ("replay-filter", "skin-filter", "map-title", "map-details", "map-file"):
        assert f'id="{name}"' in page
    assert "Filter replays…" in page and "Filter skins…" in page
    assert "✓ Exact replay MD5 match" in page and "No exact beatmap match" in page
    assert "perReplay" in page and "preference(selected().id).skin" in page


@pytest.mark.parametrize("field", ["hit_counter", "show_progress"])
def test_advanced_toggle_checkbox_is_unchecked_in_advanced_settings(server, field):
    status, _, body = request(server, "/")
    assert status == 200
    advanced = body.decode().split("<details><summary>Advanced</summary>", 1)[1].split("</details>", 1)[0]
    checkbox = re.search(rf'<input\b[^>]*\bid="{field}"[^>]*>', advanced)
    assert checkbox and 'type="checkbox"' in checkbox.group()
    assert not re.search(r'\bchecked\b', checkbox.group())
    if field == "show_progress":
        assert "Show song progress (Legacy pie / Argon progress)" in advanced


def test_hit_lighting_checkbox_is_labelled_and_checked_in_advanced_settings(server):
    status, _, body = request(server, "/")
    assert status == 200
    advanced = body.decode().split("<details><summary>Advanced</summary>", 1)[1].split("</details>", 1)[0]
    label = re.search(r'<label\b[^>]*>(<input\b[^>]*\bid="hit_lighting"[^>]*>)'
                      r'Enable hit lighting</label>', advanced)
    assert label and 'type="checkbox"' in label.group(1)
    assert re.search(r'\bchecked\b', label.group(1))


@pytest.fixture
def run_page_js():
    """Optional Node exercises the actual embedded JS; no DOM package or renderer required."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; catalog/HTTP/page contract tests still run without it")
    script = web.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
    # Exclude only the automatic API startup; exercise real handlers/state functions below.
    script = script[:script.rindex("(async()=>{try{setCatalog(await api('/api/catalog'))")]
    ids = re.findall(r'id="([^"]+)"', web.PAGE)
    selects = re.findall(r'<select id="([^"]+)"', web.PAGE)
    checked_ids = re.findall(r'<input\b[^>]*\bid="([^"]+)"[^>]*\bchecked\b[^>]*>', web.PAGE)
    harness = r"""
const assert=require('node:assert/strict');
class Element{
constructor(select){this.select=select;this.options=[];this._value='';this.dataset={};
this.textContent='';this.hidden=false;this.checked=false;this.children=[];this.classes=new Set();
this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key)}}
get value(){return this._value}
set value(value){this._value=!this.select||this.options.some(o=>o.value===String(value))?
String(value):''}
replaceChildren(...values){this.children=values;this.options=values;this.value=values[0]?.value||''}
add(option){this.options.push(option)}
append(...values){this.children.push(...values)}
pause(){this.pauses=(this.pauses||0)+1}
load(){this.loads=(this.loads||0)+1}
removeAttribute(name){delete this[name]}
play(){return Promise.resolve()}
scrollIntoView(){}
}
globalThis.Option=class{constructor(label,value){this.label=label;this.value=value;
this.disabled=false}};
const elements=Object.fromEntries(ids.map(id=>[id,new Element(selects.includes(id))]));
for(const id of checkedIds)elements[id].checked=true;
for(const [id,values] of Object.entries({resolution:['1280x720','1920x1080'],
fps:['60','30'],encoder:['auto','libx264','h264_vaapi','h264_nvenc']}))
elements[id].replaceChildren(...values.map(v=>new Option(v,v)));
globalThis.document={getElementById:id=>elements[id],createElement:()=>new Element(false)};
globalThis.localStorage={value:JSON.stringify(initial),getItem(){return this.value},
setItem(key,value){assert.equal(key,'r3d-std-render-bench');this.value=value}};
const maps=[{id:'map-a',title:'Song Alpha',version:'Insane',artist:'Artist A',
display_title:'Song Alpha [Insane]',source_info:'CS 7 · Mode 0 source',
relative_folder:'beatmap4',filename:'alpha.osu',label:'Alpha'},
{id:'map-b',title:'Song Bravo',version:'Expert',artist:'Artist B',
display_title:'Song Bravo [Expert]',source_info:'CS 4 · Mode 0 source',
relative_folder:'beatmap7',filename:'bravo.osu',label:'Bravo'}];
const data={replays:[{id:'invalid',enabled:false,label:'invalid',filename:'invalid.osr'},
{id:'replay-a',enabled:true,label:'Newest.osr · Player A',filename:'Newest.osr',
player:'Player A',beatmap_id:'map-a'},
{id:'replay-b',enabled:true,label:'Other.osr · Player B',filename:'Other.osr',
player:'Player B',beatmap_id:'map-b'}],beatmaps:maps,skins:[
{id:'skin-argon',name:'Argon Test',relative_path:'skins/argon',label:'Argon Test · skins/argon'},
{id:'skin-night',name:'Night05',relative_path:'skins/night',label:'Night05 · skins/night'}],
diagnostics:[]};
"""

    def run(code, saved=None):
        source = (f"const ids={json.dumps(ids)},selects={json.dumps(selects)},"
                  f"checkedIds={json.dumps(checked_ids)},"
                  f"initial={json.dumps(saved or {})};\n" + harness + script +
                  "\n(async()=>{\n" + code + "\n})().catch(error=>{"
                  "console.error(error);process.exitCode=1});")
        result = subprocess.run([node, "-"], input=source, text=True, capture_output=True,
                                timeout=5, check=False)
        assert result.returncode == 0, result.stderr

    return run


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize("lighting", [True, False])
def test_page_submits_advanced_booleans_and_preserves_other_render_options(
        run_page_js, enabled, progress, lighting):
    run_page_js(f"const enabled={json.dumps(enabled)},progress={json.dumps(progress)},"
                f"lighting={json.dumps(lighting)};\n" + """
setCatalog(data);
assert.equal($('hit_lighting').checked,true);
for(const id of ['skin_hitsounds','legacy_defaults','renderer_default_font_and_ranks',
'hit_counter','show_progress'])assert.equal($(id).checked,false);
$('hit_counter').checked=enabled;
$('show_progress').checked=progress;
$('skin').value='skin-night';$('skin').onchange();$('resolution').value='1920x1080';
$('fps').value='30';$('encoder').value='libx264';
$('skin_hitsounds').checked=true;$('legacy_defaults').checked=true;
$('renderer_default_font_and_ranks').checked=true;
$('start').value='35';$('end').value='40';
save();const preferences=JSON.parse(localStorage.value);
assert.equal(Object.hasOwn(preferences,'hit_lighting'),false);
for(const prefs of Object.values(preferences.perReplay))
assert.equal(Object.hasOwn(prefs,'hit_lighting'),false);
const payloads=[];
globalThis.setTimeout=()=>{};
globalThis.fetch=async(path,options)=>{
if(path==='/api/status')return {ok:true,json:async()=>({state:'idle'})};
if(path==='/api/history')return {ok:true,json:async()=>[]};
assert.equal(path,'/api/render');assert.equal(options.method,'POST');
assert.equal(options.headers['Content-Type'],'application/json');
payloads.push(JSON.parse(options.body));
return {ok:true,json:async()=>({state:'rendering'})};
};
for(const hitLighting of [lighting,!lighting]){
$('hit_lighting').checked=hitLighting;
assert.equal($('render').disabled,false);
let prevented=false;
await $('form').onsubmit({preventDefault(){prevented=true}});
assert(prevented);
assert.deepEqual(payloads.at(-1),{replay:'replay-a',beatmap:'map-a',skin:'skin-night',
resolution:'1920x1080',fps:30,encoder:'libx264',skin_hitsounds:true,
legacy_defaults:true,renderer_default_font_and_ranks:true,hit_counter:enabled,
show_progress:progress,hit_lighting:hitLighting,start:35,end:40});
assert.equal(active,true);assert.equal(launching,false);assert.equal($('render').disabled,true);
await poll();
assert.equal(active,false);assert.equal($('render').disabled,false);
assert.equal($('hit_lighting').checked,hitLighting);
assert.deepEqual(JSON.parse(localStorage.value),preferences);
}
assert.equal(payloads.length,2);
""", {"perReplay": {"replay-b": {"skin": "skin-argon", "beatmap": "map-a"}}})


@pytest.mark.parametrize("remembered", [None, "invalid", "disappeared"])
def test_page_defaults_to_newest_valid_replay_and_exact_map(run_page_js, remembered):
    run_page_js("""
setCatalog(data);
assert.equal($('replay').value,'replay-a');assert.equal($('beatmap').value,'map-a');
assert.equal($('skin').value,'');assert.equal($('skin').options[0].label,'None / default skin');
assert.equal($('match').textContent,'✓ Exact replay MD5 match');
assert.equal($('map-title').textContent,'Song Alpha [Insane]');
assert.equal($('map-details').textContent,'CS 7 · Mode 0 source · beatmap4');
assert.equal($('map-file').textContent,'alpha.osu');
assert($('beatmap-field').classes.has('matched'));assert(!$('render').disabled);
assert.equal(active,false);assert.equal(launching,false);
""", {"replay": remembered})


def test_page_restores_per_replay_skin_and_remembers_explicit_none(run_page_js):
    run_page_js("""
setCatalog(data);assert.equal($('replay').value,'replay-b');
assert.equal($('skin').value,'skin-night');assert.equal($('fps').value,'30');
$('replay').value='replay-a';$('replay').onchange();assert.equal($('skin').value,'');
$('skin').value='skin-argon';$('skin').onchange();
$('replay').value='replay-b';$('replay').onchange();assert.equal($('skin').value,'skin-night');
$('skin').value='';$('skin').onchange();
$('replay').value='replay-a';$('replay').onchange();assert.equal($('skin').value,'skin-argon');
$('replay').value='replay-b';$('replay').onchange();assert.equal($('skin').value,'');
const persisted=JSON.parse(localStorage.value);
assert.equal(persisted.perReplay['replay-a'].skin,'skin-argon');
assert.equal(persisted.perReplay['replay-b'].skin,'');
""", {"replay": "replay-b", "fps": "30", "perReplay": {
        "replay-a": {"skin": ""}, "replay-b": {"skin": "skin-night"}}})


def test_page_preserves_manual_map_override_per_replay_until_cleared(run_page_js):
    run_page_js("""
setCatalog(data);$('beatmap').value='map-b';$('beatmap').onchange();
assert(!$('beatmap-field').classes.has('matched'));
$('replay').value='replay-b';$('replay').onchange();assert.equal($('beatmap').value,'map-b');
$('replay').value='replay-a';$('replay').onchange();assert.equal($('beatmap').value,'map-b');
setCatalog(data,'replay-a',true);assert.equal($('beatmap').value,'map-b');
$('beatmap').value='map-a';$('beatmap').onchange();
$('replay').value='replay-b';$('replay').onchange();
$('replay').value='replay-a';$('replay').onchange();assert.equal($('beatmap').value,'map-a');
assert(!JSON.parse(localStorage.value).perReplay['replay-a'].beatmap);
""")


def test_page_rescan_preserves_replay_skin_and_selects_new_exact_match(run_page_js):
    run_page_js("""
setCatalog({...data,replays:data.replays.map(r=>r.id==='replay-a'?{...r,beatmap_id:null}:r)});
assert.equal($('beatmap').value,'');assert.equal($('match').textContent,'No exact beatmap match');
assert($('beatmap-field').classes.has('needs-map'));assert($('render').disabled);
$('beatmap').value='map-b';$('beatmap').onchange();
$('skin').value='skin-night';$('skin').onchange();
setCatalog(data,'replay-a',true);
assert.equal($('replay').value,'replay-a');assert.equal($('skin').value,'skin-night');
assert.equal($('beatmap').value,'map-a');assert($('beatmap-field').classes.has('matched'));
assert(!JSON.parse(localStorage.value).perReplay['replay-a'].beatmap);
""")


def test_page_rescan_falls_back_when_replay_or_skin_disappears(run_page_js):
    run_page_js("""
setCatalog(data);assert.equal($('skin').value,'skin-night');
setCatalog({...data,skins:[]},'replay-a',true);assert.equal($('skin').value,'');
setCatalog({...data,replays:data.replays.filter(r=>r.id!=='replay-a')},'replay-a',true);
assert.equal($('replay').value,'replay-b');assert.equal($('beatmap').value,'map-b');
""", {"perReplay": {"replay-a": {"skin": "skin-night"}}})


def test_page_filters_filename_player_matched_song_difficulty_skin_name_path(run_page_js):
    run_page_js("""
setCatalog(data);
for(const query of ['Other.osr','PLAYER B','song bravo','expert']){
$('replay-filter').value=query;$('replay-filter').oninput();
assert.deepEqual($('replay').options.map(o=>o.value),['','replay-a','replay-b']);
assert.equal($('replay').value,'replay-a')}
$('replay-filter').value='does not exist';$('replay-filter').oninput();
assert.deepEqual($('replay').options.map(o=>o.value),['','replay-a']);
for(const query of ['Night05','SKINS/NIGHT']){
$('skin-filter').value=query;$('skin-filter').oninput();
assert.deepEqual($('skin').options.map(o=>o.value),['','skin-night'])}
$('skin').value='skin-night';$('skin').onchange();
$('skin-filter').value='Argon';$('skin-filter').oninput();
assert.equal($('skin').value,'skin-night');
assert.deepEqual($('skin').options.map(o=>o.value),['','skin-argon','skin-night']);
""")


def test_page_migrates_previous_global_skin_to_its_remembered_replay(run_page_js):
    run_page_js("""
setCatalog(data);assert.equal($('skin').value,'skin-night');
$('replay').value='replay-a';$('replay').onchange();assert.equal($('skin').value,'');
""", {"replay": "replay-b", "skin": "skin-night"})


def test_absent_roots_do_not_crash(tmp_path):
    app = web.Dashboard(tmp_path, (tmp_path / "missing",), (), tmp_path / "outputs")
    assert not app.catalog.replays and app.status()["state"] == "idle"
    assert app.catalog.diagnostics


def test_ready_runtime_keeps_current_python(monkeypatch):
    monkeypatch.setattr(web.importlib.util, "find_spec", lambda _: object())

    def unexpected(*_, **__):
        pytest.fail("A ready Python environment must not probe or restart")

    monkeypatch.setattr(web.subprocess, "run", unexpected)
    monkeypatch.setattr(web.os, "execv", unexpected)
    web.ensure_runtime(web.argparse.ArgumentParser(), [])


@pytest.mark.parametrize("explicit_argv", [None, ["--host", "127.0.0.1", "--port", "0"]])
def test_missing_dependency_switches_to_managed_runtime_and_preserves_args(
        tmp_path, monkeypatch, explicit_argv):
    python = tmp_path / ".dashboard-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    monkeypatch.setattr(web, "REPO", tmp_path)
    monkeypatch.setattr(web.importlib.util, "find_spec", lambda _: None)
    monkeypatch.setattr(web.sys, "argv", ["local_render_web.py", "--root", "/fixtures"])
    probes, launches = [], []

    def probe(command, **kwargs):
        probes.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(web.subprocess, "run", probe)
    monkeypatch.setattr(web.os, "execv", lambda executable, command: launches.append(
        (executable, command)))
    web.ensure_runtime(web.argparse.ArgumentParser(), explicit_argv)
    assert probes[0][0][:2] == [str(python), "-c"]
    assert all(f"import {name}" in probes[0][0][2] for name in web.RUNTIME_MODULES)
    arguments = explicit_argv if explicit_argv is not None else ["--root", "/fixtures"]
    assert launches == [(str(python), [str(python), str(Path(web.__file__).resolve()),
                                     *arguments])]


def test_missing_runtime_stops_before_scanning_or_serving(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(web, "REPO", tmp_path)
    monkeypatch.setattr(web.importlib.util, "find_spec", lambda _: None)

    def unexpected(*_, **__):
        pytest.fail("Missing dependencies must stop startup before scanning or serving")

    monkeypatch.setattr(web, "scan_fixtures", unexpected)
    monkeypatch.setattr(web, "make_server", unexpected)
    with pytest.raises(SystemExit) as error:
        web.main([])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "missing renderer dependencies" in message and "osrparse" in message
    assert str(tmp_path / "tools/start-local-render-web.sh") in message
    assert not (tmp_path / "local-web-renders").exists()


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "oserror"])
def test_unusable_managed_runtime_reports_setup_command(tmp_path, monkeypatch, capsys, failure):
    python = tmp_path / ".dashboard-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    monkeypatch.setattr(web, "REPO", tmp_path)
    monkeypatch.setattr(web.importlib.util, "find_spec", lambda _: None)

    def probe(command, **_):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 10)
        if failure == "oserror":
            raise OSError("Python could not start")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(web.subprocess, "run", probe)
    with pytest.raises(SystemExit):
        web.ensure_runtime(web.argparse.ArgumentParser(), [])
    assert "start-local-render-web.sh" in capsys.readouterr().err


@pytest.mark.parametrize("default_exists", [True, False])
def test_main_defaults_startup_urls_and_shutdown(tmp_path, monkeypatch, capsys, default_exists):
    fixture_root = tmp_path / "test-render"
    if default_exists:
        fixture_root.mkdir(parents=True)
    # Repository fixtures must never substitute for the manual library.
    (tmp_path / "tests/fixtures").mkdir(parents=True)
    (tmp_path / "test").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(web, "REPO", tmp_path)
    monkeypatch.setattr(web, "lan_address", lambda: "192.168.1.100")
    captured = []

    class Server:
        server_port = 7377
        closed = False

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    server = Server()

    def make_server(app, host, port):
        captured.append((app, host, port))
        return server

    monkeypatch.setattr(web, "make_server", make_server)
    web.main([])
    app, host, port = captured[0]
    assert host == "0.0.0.0" and port == 7377 and app.history_limit == 10
    assert app.roots == (fixture_root,) and app.output_dir == tmp_path / "local-web-renders"
    assert app.renderer_repo == tmp_path and app.renderer_executable == sys.executable
    assert server.closed
    output = capsys.readouterr().out
    assert "http://127.0.0.1:7377" in output and "http://192.168.1.100:7377" in output
    if not default_exists:
        assert f"Search root is absent: {fixture_root}" in output


@pytest.fixture
def renderer_checkout(tmp_path):
    target = tmp_path / "renderer"
    (target / "osu_std_renderer").mkdir(parents=True)
    (target / "osu_std_renderer/cli.py").write_text("# renderer entry point\n")
    (target / "osu_std_renderer/__main__.py").write_text("# module entry point\n")
    return target


@pytest.fixture
def startup(tmp_path, monkeypatch):
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    monkeypatch.setattr(web, "REPO", dashboard)
    monkeypatch.setattr(web, "lan_address", lambda: None)
    captured = SimpleNamespace(app=None, host=None, port=None, closed=False)

    class Server:
        server_port = 43210

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            captured.closed = True

    def make_server(app, host, port):
        captured.app, captured.host, captured.port = app, host, port
        return Server()

    monkeypatch.setattr(web, "make_server", make_server)
    return captured


def test_external_target_startup_resolves_path_keeps_dashboard_state(
        renderer_checkout, startup, monkeypatch, capsys):
    monkeypatch.chdir(renderer_checkout.parent)

    def git(command, **kwargs):
        assert command[:3] == ["git", "-C", str(renderer_checkout)]
        assert kwargs == {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
                          "text": True, "timeout": 2, "check": False}
        value = "target-branch" if command[3] == "branch" else "abc1234"
        return SimpleNamespace(returncode=0, stdout=value + "\n")

    monkeypatch.setattr(web.subprocess, "run", git)
    web.main(["--renderer-repo", "renderer", "--port", "0"])
    app = startup.app
    assert app.renderer_repo == renderer_checkout
    assert app.repo == web.REPO and app.output_dir == web.REPO / "local-web-renders"
    assert not (renderer_checkout / "local-web-renders").exists()
    assert app.roots == (web.REPO / "test-render",)
    assert startup.closed and startup.port == 0
    assert app.catalog_public()["renderer"] == {
        "path": str(renderer_checkout), "branch": "target-branch", "head": "abc1234"}
    output = capsys.readouterr().out
    for text in (f"dashboard: {web.REPO}", f"renderer:  {renderer_checkout}",
                 "branch:    target-branch", "HEAD:      abc1234", "http://127.0.0.1:43210"):
        assert text in output


@pytest.mark.parametrize("missing", ["osu_std_renderer/cli.py", "osu_std_renderer/__main__.py"])
def test_invalid_renderer_repo_fails_before_server_or_output_creation(
        renderer_checkout, startup, capsys, missing):
    (renderer_checkout / missing).unlink()
    with pytest.raises(SystemExit) as error:
        web.main(["--renderer-repo", str(renderer_checkout)])
    assert error.value.code == 2
    assert "renderer repo must contain" in capsys.readouterr().err
    assert startup.app is None and not (web.REPO / "local-web-renders").exists()


@pytest.mark.parametrize("kind", ["missing", "not-executable", "directory", "executable"])
def test_renderer_python_only_prefers_executable_venv_file(renderer_checkout, kind):
    python = renderer_checkout / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    if kind == "directory":
        python.mkdir()
    elif kind != "missing":
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755 if kind == "executable" else 0o644)
    expected = str(python) if kind == "executable" else sys.executable
    assert str(web.renderer_python(renderer_checkout)) == expected


@pytest.mark.parametrize("has_venv", [True, False])
def test_external_render_uses_target_python_and_cwd_keeps_output_in_dashboard(
        app, renderer_checkout, processes, monkeypatch, has_venv):
    python = renderer_checkout / ".venv/bin/python"
    if has_venv:
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
    # Keep fake renderer Popen separate from read-only Git metadata calls.
    monkeypatch.setattr(web, "renderer_info", lambda repo: {
        "path": str(repo), "branch": "test", "head": "abc1234"})
    external = web.Dashboard(app.repo, app.roots, app.skin_roots, app.output_dir,
                             renderer_repo=renderer_checkout)
    external.start(selection(external))
    process, command, options = processes[0]
    assert command[:3] == [str(python) if has_venv else sys.executable,
                           "-m", "osu_std_renderer"]
    assert options["cwd"] == renderer_checkout
    assert options["shell"] is False and options["start_new_session"] is True
    assert Path(command[6]).parent == app.output_dir
    assert external.repo == app.repo and external.roots == app.roots
    process.complete(1)
    joined(external, "failed")


@pytest.mark.parametrize("field", ["renderer_repo", "renderer-repo", "renderer_python",
                                  "executable", "cwd"])
def test_api_cannot_override_renderer_configuration(app, server, processes, field):
    payload = json.dumps(selection(app, **{field: "/tmp/arbitrary"}))
    status, _, content = request(server, "/api/render", method="POST", body=payload,
                                 headers={"Content-Type": "application/json"})
    assert status == 400 and "Only catalog selections" in json.loads(content)["error"]
    assert not processes


@pytest.mark.parametrize("failure", ["missing-git", "timeout", "nonzero", "empty"])
def test_renderer_git_metadata_failures_are_unknown(renderer_checkout, monkeypatch, failure):
    calls = []

    def git(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 2
        if failure == "missing-git":
            raise OSError("git unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0, stdout="")

    monkeypatch.setattr(web.subprocess, "run", git)
    assert web.renderer_info(renderer_checkout) == {
        "path": str(renderer_checkout), "branch": "unknown", "head": "unknown"}
    assert len(calls) == 2


def test_startup_survives_unreadable_git(renderer_checkout, startup, monkeypatch, capsys):
    def missing_git(*_, **__):
        raise OSError("git unavailable")

    monkeypatch.setattr(web.subprocess, "run", missing_git)
    web.main(["--renderer-repo", str(renderer_checkout)])
    assert startup.app.renderer["branch"] == startup.app.renderer["head"] == "unknown"
    assert startup.closed
    output = capsys.readouterr().out
    assert "branch:    unknown" in output and "HEAD:      unknown" in output


def test_renderer_metadata_is_cached_until_rescan(app, server, monkeypatch):
    calls = []

    def info(repo):
        calls.append(repo)
        return {"path": str(repo), "branch": f"branch-{len(calls)}",
                "head": f"head-{len(calls)}"}

    monkeypatch.setattr(web, "renderer_info", info)
    assert app.rescan()["renderer"]["branch"] == "branch-1"
    for _ in range(3):
        catalog = json.loads(request(server, "/api/catalog")[2])
        assert catalog["renderer"] == {
            "path": str(app.repo), "branch": "branch-1", "head": "head-1"}
        request(server, "/api/status")
    assert len(calls) == 1
    status, _, body = request(server, "/api/rescan", method="POST", body="{}",
                              headers={"Content-Type": "application/json"})
    assert status == 200 and json.loads(body)["renderer"]["head"] == "head-2"
    assert len(calls) == 2


def test_page_displays_target_metadata_and_refreshes_on_rescan(run_page_js):
    run_page_js("""
const renderer={path:'/target/renderer',branch:'target-branch',head:'abc1234'};
setCatalog({...data,renderer});
assert.equal($('renderer-revision').textContent,'target-branch · abc1234');
assert.equal($('renderer-path').textContent,'/target/renderer');
setCatalog({...data,renderer:{...renderer,branch:'switched-branch',head:'def5678'}},
'replay-a',true);
assert.equal($('renderer-revision').textContent,'switched-branch · def5678');
assert.equal($('replay').value,'replay-a');assert.equal($('beatmap').value,'map-a');
""")


@pytest.fixture
def saved_renders(app):
    for name, modified in (("older.mp4", 100), ("newest.mp4", 200)):
        path = app.output_dir / name
        path.write_bytes(b"saved video")
        os.utime(path, (modified, modified))
        app.labels[name] = f"Friendly {name}"
    app.latest = "newest.mp4"
    return app.refresh_history()


def delete_request(server, *, filename=None, bulk=False, body=None):
    payload = {} if bulk else {"filename": filename}
    return request(server, "/api/renders/delete-old" if bulk else "/api/renders/delete",
                   method="POST", body=json.dumps(payload if body is None else body),
                   headers={"Content-Type": "application/json"})


def test_delete_latest_promotes_newest_remaining_and_cleans_labels(app, server, saved_renders):
    status, _, body = delete_request(server, filename="newest.mp4")
    result = json.loads(body)
    assert status == 200 and result["deleted"] == ["newest.mp4"]
    assert not (app.output_dir / "newest.mp4").exists()
    assert [r["filename"] for r in result["history"]] == ["older.mp4"]
    assert result["latest"]["filename"] == app.latest == "older.mp4"
    assert app.labels == {"older.mp4": "Friendly older.mp4"}
    assert app.status()["latest"] == result["latest"]
    status, _, body = delete_request(server, filename="older.mp4")
    result = json.loads(body)
    assert status == 200 and result["history"] == [] and result["latest"] is None
    assert app.latest is None and not app.labels


def test_deleting_older_render_preserves_latest(app, server, saved_renders):
    status, _, body = delete_request(server, filename="older.mp4")
    assert status == 200 and json.loads(body)["latest"]["filename"] == "newest.mp4"
    assert (app.output_dir / "newest.mp4").exists()


def test_external_renderer_directory_is_never_a_deletion_target(
        app, renderer_checkout):
    outputs = renderer_checkout / "outputs"
    external = web.Dashboard(app.repo, app.roots, app.skin_roots, outputs,
                             renderer_repo=renderer_checkout)
    saved = outputs / "preserve.mp4"
    saved.write_bytes(b"external checkout")
    for filename in (saved.name, None):
        with pytest.raises(web.APIError) as error:
            external.delete_renders(filename)
        assert error.value.status == 403
        assert saved.read_bytes() == b"external checkout"


def test_manual_deletion_does_not_change_automatic_pruning(app, saved_renders):
    app.delete_renders("older.mp4")
    app.history_limit = 1
    generated = []
    for index in range(2):
        path = app.output_dir / f"r3d-web-20261007-000000-{index:012x}.mp4"
        path.write_bytes(b"generated")
        os.utime(path, (300 + index, 300 + index))
        generated.append(path)
    app.refresh_history(prune=True)
    assert not generated[0].exists() and generated[1].exists()
    assert (app.output_dir / "newest.mp4").exists()  # Manual filename survives pruning.


@pytest.mark.parametrize("filename,status", [
    ("../outside.mp4", 404), ("/tmp/outside.mp4", 404), ("a\\outside.mp4", 404),
    ("old.txt", 404), ("", 404), ("a\0.mp4", 404), (123, 400), (["old.mp4"], 400),
])
def test_delete_rejects_paths_and_malformed_names(app, server, tmp_path, filename, status):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    unrelated = app.output_dir / "old.txt"
    unrelated.write_bytes(b"unrelated")
    assert delete_request(server, filename=filename)[0] == status
    assert outside.read_bytes() == b"outside" and unrelated.read_bytes() == b"unrelated"


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_delete_rejects_nonregular_files(app, server, tmp_path, kind):
    path = app.output_dir / "unsafe.mp4"
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"preserve")
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    assert delete_request(server, filename=path.name)[0] == 404
    assert path.exists() and outside.read_bytes() == b"preserve"


def test_delete_missing_render_returns_friendly_404(app, server):
    status, _, body = delete_request(server, filename="gone.mp4")
    assert status == 404 and "not found" in json.loads(body)["error"].lower()
    assert not app.history


def test_active_output_is_protected_from_single_and_bulk_deletion(
        app, server, processes, saved_renders):
    app.start(selection(app))
    # The active slot is protected even before ffmpeg has created its file.
    assert delete_request(server, filename=app.job.filename)[0] == 409
    active_output = app.output_path(app.job.filename)
    active_output.write_bytes(b"active ffmpeg output")
    status, _, body = delete_request(server, filename=app.job.filename)
    assert status == 409 and "Cancel" in json.loads(body)["error"]
    assert active_output.read_bytes() == b"active ffmpeg output"
    status, _, body = delete_request(server, bulk=True)
    result = json.loads(body)
    assert status == 200 and set(result["deleted"]) == {"older.mp4", "newest.mp4"}
    assert result["history"] == [] and result["latest"] is None
    assert active_output.exists() and app.status()["state"] == "rendering"
    processes[0][0].complete(0)
    joined(app, "done")
    assert app.status()["latest"]["filename"] == active_output.name


def test_bulk_deletion_covers_files_beyond_history_limit_and_preserves_everything_else(
        app, server, tmp_path, saved_renders):
    app.history_limit = 1
    extra = app.output_dir / "r3d-web-20261007-000000-111111111111.mp4"
    extra.write_bytes(b"generated")
    unrelated = app.output_dir / "audit.md"
    unrelated.write_bytes(b"preserve audit")
    outside = tmp_path / "external-renderer.mp4"
    outside.write_bytes(b"preserve external renderer")
    link = app.output_dir / "link.mp4"
    link.symlink_to(outside)
    folder = app.output_dir / "directory.mp4"
    folder.mkdir()
    (folder / "nested.mp4").write_bytes(b"preserve nested")
    os.mkfifo(app.output_dir / "pipe.mp4")
    app.refresh_history()
    assert len(app.history) == 1
    status, _, body = delete_request(server, bulk=True)
    result = json.loads(body)
    assert status == 200 and set(result["deleted"]) == {"older.mp4", "newest.mp4", extra.name}
    assert result["history"] == [] and result["latest"] is None and not app.labels
    assert link.is_symlink() and outside.read_bytes() == b"preserve external renderer"
    assert unrelated.read_bytes() == b"preserve audit"
    assert (folder / "nested.mp4").read_bytes() == b"preserve nested"
    assert (app.output_dir / "pipe.mp4").exists()
    assert delete_request(server, bulk=True)[0] == 200  # Empty bulk deletion is harmless.


@pytest.mark.parametrize("bulk,body", [
    (False, {}), (False, {"path": "/tmp/video.mp4"}),
    (False, {"filename": "old.mp4", "renderer_repo": "/tmp/target"}),
    (False, {"filename": "old.mp4", "paths": ["/tmp/video.mp4"]}),
    (True, {"filenames": ["old.mp4"]}), (True, {"path": "/tmp"}),
])
def test_deletion_api_rejects_arbitrary_paths_lists_and_extra_fields(
        app, server, saved_renders, bulk, body):
    assert delete_request(server, bulk=bulk, body=body)[0] == 400
    assert (app.output_dir / "older.mp4").exists() and (app.output_dir / "newest.mp4").exists()


def test_delete_controls_confirm_and_clear_or_replace_playing_video(run_page_js):
    run_page_js("""
const old={filename:'old.mp4',label:'Old',url:'/renders/old.mp4',timestamp:0,size:100};
const next={filename:'next.mp4',label:'Next',url:'/renders/next.mp4',timestamp:1,size:200};
const calls=[];let confirmation='',allow=false;
globalThis.confirm=message=>{confirmation=message;return allow};
globalThis.fetch=async(path,options)=>{calls.push([path,JSON.parse(options.body)]);
return {ok:true,json:async()=>path.endsWith('delete-old')?
{deleted:['next.mp4'],history:[],latest:null}:{deleted:['old.mp4'],history:[next],latest:next}}};
showHistory([old,next]);video(old);
const row=$('history').children[0],controls=row.children[1];
assert.deepEqual(controls.children.map(button=>button.textContent),['Play','Delete']);
assert(!$('delete-old').hidden);
await controls.children[1].onclick();assert.equal(confirmation,'Delete this render?');
assert.equal(calls.length,0);assert.equal($('player').src,old.url);
allow=true;await controls.children[1].onclick();
assert.deepEqual(calls[0],['/api/renders/delete',{filename:'old.mp4'}]);
assert.equal($('player').pauses,1);assert.equal($('player').loads,1);
assert.equal($('player').src,next.url);assert(!$('result').hidden);
assert.equal(lastLatest,'next.mp4');assert.equal(currentVideo,'next.mp4');
await $('delete-old').onclick();
assert.equal(confirmation,'Delete all saved renders from this dashboard?');
assert.deepEqual(calls[1],['/api/renders/delete-old',{}]);
assert.equal($('player').pauses,2);assert.equal($('player').loads,2);
assert.equal($('player').src,undefined);assert($('result').hidden);
assert.equal(currentVideo,'');assert.equal(lastLatest,'');assert($('delete-old').hidden);
""")


def test_pending_poll_cannot_restore_deleted_video(run_page_js):
    run_page_js("""
const old={filename:'old.mp4',label:'Old',url:'/renders/old.mp4',timestamp:0,size:100};
const calls=[];let release;
globalThis.setTimeout=()=>{};
globalThis.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:true,
json:async()=>({state:'idle',latest:old})})});
video(old);lastLatest=old.filename;
const pending=poll();
deletedRenders({deleted:[old.filename],history:[],latest:null});
release();await pending;
assert($('result').hidden);assert.equal($('player').src,undefined);
assert.equal(currentVideo,'');assert.equal(lastLatest,'');
""")
