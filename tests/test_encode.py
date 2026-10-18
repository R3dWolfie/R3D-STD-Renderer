"""Working encoder selection and errors from a child that closes its frame pipe."""
from __future__ import annotations

import io
import subprocess
import sys
import time
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from osu_std_renderer.record import encode


def _listing(*names):
    return SimpleNamespace(returncode=0, stderr="", stdout="\n".join(
        f" V....D {name} H.264 encoder" for name in names))


def _error(call, message):
    try:
        call()
    except encode.EncoderError as exc:
        assert message in str(exc), str(exc)
        return str(exc)
    raise AssertionError("Expected an encoder error")


def _auto(outcomes, device="/dev/dri/renderD128", device_exists=True):
    log = io.StringIO()
    with patch.object(encode.shutil, "which", return_value="/usr/bin/ffmpeg"), \
            patch.object(encode.subprocess, "run", return_value=_listing(
                "h264_nvenc", "h264_vaapi", "libx264")), \
            patch.object(encode.Path, "exists", return_value=device_exists), \
            patch.object(encode, "_encoder_usable", side_effect=outcomes) as probe, \
            redirect_stderr(log):
        result = encode.probe_encoder("auto", device)
        return result, probe.call_args_list, log.getvalue()


def test_auto_rejects_compiled_nvenc_without_driver_and_uses_vaapi():
    selected, calls, log = _auto([(False, "Cannot load libcuda.so.1"), (True, "")])
    assert selected == "h264_vaapi"
    assert [call.args[1] for call in calls] == ["h264_nvenc", "h264_vaapi"]
    assert calls[-1].args[2] == "/dev/dri/renderD128"
    assert "Cannot load libcuda.so.1" in log and "auto selected h264_vaapi" in log


def test_auto_falls_back_to_software_when_both_hardware_drivers_fail():
    selected, calls, _ = _auto([
        (False, "CUDA unavailable"), (False, "VAAPI unavailable"), (True, "")])
    assert selected == "libx264" and len(calls) == 3


def test_auto_keeps_working_nvenc_preference():
    selected, calls, _ = _auto([(True, "")])
    assert selected == "h264_nvenc" and len(calls) == 1


def test_auto_skips_vaapi_when_no_render_device_exists():
    selected, calls, _ = _auto([(False, "CUDA unavailable"), (True, "")],
                             device=None, device_exists=False)
    assert selected == "libx264"
    assert [call.args[1] for call in calls] == ["h264_nvenc", "libx264"]


def test_auto_uses_custom_device_even_when_default_device_is_absent():
    selected, calls, _ = _auto([(False, "CUDA unavailable"), (True, "")],
                             device="/dev/dri/renderD129", device_exists=False)
    assert selected == "h264_vaapi" and calls[-1].args[2] == "/dev/dri/renderD129"


def test_explicit_encoder_is_not_replaced_or_probed():
    with patch.object(encode.shutil, "which") as which, \
            patch.object(encode, "_encoder_usable") as probe:
        for name in ("h264_nvenc", "h264_vaapi", "libx264"):
            assert encode.probe_encoder(name) == name
        which.assert_not_called()
        probe.assert_not_called()


def test_missing_ffmpeg_is_reported_before_rendering():
    with patch.object(encode.shutil, "which", return_value=None):
        _error(encode.probe_encoder, "ffmpeg not found")


def test_auto_reports_all_encoder_failures():
    with patch.object(encode.shutil, "which", return_value="ffmpeg"), \
            patch.object(encode.Path, "exists", return_value=True), \
            patch.object(encode.subprocess, "run", return_value=_listing(
                "h264_nvenc", "h264_vaapi", "libx264")), \
            patch.object(encode, "_encoder_usable", side_effect=[
                (False, "CUDA unavailable"), (False, "VAAPI unavailable"),
                (False, "software unavailable")]), redirect_stderr(io.StringIO()):
        message = _error(encode.probe_encoder, "No usable H.264 encoder")
        assert all(reason in message for reason in (
            "CUDA unavailable", "VAAPI unavailable", "software unavailable"))


def test_encoder_query_failure_is_actionable():
    failures = [OSError("ffmpeg cannot start"), subprocess.TimeoutExpired("ffmpeg", 5)]
    for failure in failures:
        with patch.object(encode.shutil, "which", return_value="ffmpeg"), \
                patch.object(encode.subprocess, "run", side_effect=failure):
            _error(encode.probe_encoder, "Unable to query ffmpeg encoders")
    result = SimpleNamespace(returncode=1, stderr="ffmpeg setup failed", stdout="")
    with patch.object(encode.shutil, "which", return_value="ffmpeg"), \
            patch.object(encode.subprocess, "run", return_value=result):
        _error(encode.probe_encoder, "ffmpeg setup failed")


def test_encoder_probe_sends_a_frame_and_uploads_to_selected_vaapi_device():
    with patch.object(encode.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stderr=b"")) as run:
        assert encode._encoder_usable("ffmpeg", "h264_vaapi", "/dev/dri/renderD129") == (
            True, "")
    command = run.call_args.args[0]
    assert command[command.index("-vaapi_device") + 1] == "/dev/dri/renderD129"
    assert command[command.index("-vf") + 1] == "format=nv12,hwupload"
    assert len(run.call_args.kwargs["input"]) == 128 * 128 * 3
    assert run.call_args.kwargs["timeout"] == 5


def test_failed_encoder_probe_retains_driver_error_and_bounds_startup():
    result = SimpleNamespace(returncode=1, stderr=b"Cannot load libcuda.so.1\nOther details")
    with patch.object(encode.subprocess, "run", return_value=result):
        assert encode._encoder_usable("ffmpeg", "h264_nvenc", None) == (
            False, "Cannot load libcuda.so.1")
    for error in (OSError("driver unavailable"), subprocess.TimeoutExpired("ffmpeg", 5)):
        with patch.object(encode.subprocess, "run", side_effect=error):
            usable, reason = encode._encoder_usable("ffmpeg", "h264_nvenc", None)
            assert not usable and reason


def test_broken_frame_pipe_includes_child_error_and_exit_code():
    # A real child exits before reading a frame, reproducing missing hardware drivers.
    pipe = encode.FfmpegPipe([sys.executable, "-c",
                             "import sys; sys.stderr.write('encoder driver unavailable\\n'); "
                             "sys.exit(7)"])
    try:
        def render():
            with pipe:
                pipe.proc.wait(timeout=3)
                pipe.push(np.zeros((128, 128, 3), dtype=np.uint8))
                deadline = time.monotonic() + 3
                while pipe._werr is None and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert isinstance(pipe._werr, BrokenPipeError)
                pipe.push(np.zeros((128, 128, 3), dtype=np.uint8))

        message = _error(render, "ffmpeg exited 7")
        assert "encoder driver unavailable" in message
        assert not pipe._thread.is_alive() and pipe.proc.poll() == 7
    finally:
        if pipe.proc is not None and pipe.proc.stderr is not None:
            pipe.proc.stderr.close()


def test_unrelated_render_exception_is_preserved_with_child_diagnostic():
    pipe = encode.FfmpegPipe([sys.executable, "-c",
                             "import sys; sys.stderr.write('encoder driver unavailable\\n'); "
                             "sys.exit(7)"])
    log = io.StringIO()
    try:
        try:
            with redirect_stderr(log), pipe:
                pipe.proc.wait(timeout=3)
                raise ValueError("scene draw failed")
        except ValueError as exc:
            assert str(exc) == "scene draw failed"
        else:
            raise AssertionError("The scene error must be preserved")
        assert "ffmpeg exited 7" in log.getvalue()
        assert "encoder driver unavailable" in log.getvalue()
    finally:
        if pipe.proc is not None and pipe.proc.stderr is not None:
            pipe.proc.stderr.close()
