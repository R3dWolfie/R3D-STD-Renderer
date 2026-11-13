"""AV1 capability probes must clear hardware minimum frame dimensions."""
from types import SimpleNamespace
from unittest.mock import patch

from osu_std_renderer import av1


def test_av1_probe_uses_safe_dimensions_and_verifies_codec():
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return SimpleNamespace(returncode=0, stderr=b"")

    with patch.object(av1, "_compiled_in", return_value=True), \
         patch.object(av1.Path, "exists", return_value=True), \
         patch.object(av1, "_ffmpeg_identity", return_value="test"), \
         patch.object(av1.subprocess, "run", side_effect=run), \
         patch.object(av1, "_ffprobe_codec", return_value="av1") as codec:
        assert av1.probe("av1_vaapi", "/dev/dri/renderD129", ffmpeg="ffmpeg", use_cache=False)[0]
        cmd = seen["cmd"]
        assert cmd[cmd.index("-s") + 1] == "320x240"
        assert cmd[cmd.index("-frames:v") + 1] == "5"
        assert cmd[cmd.index("-r") + 1] == "5"
        assert cmd[cmd.index("-vaapi_device") + 1] == "/dev/dri/renderD129"
        assert cmd[cmd.index("-c:v") + 1] == "av1_vaapi"
        assert len(seen["input"]) == 320 * 240 * 3 * 5
        assert seen["timeout"] == 20.0
        codec.assert_called_once()
        codec.return_value = "h264"
        ok, reason = av1.probe("av1_vaapi", "/dev/dri/renderD129", ffmpeg="ffmpeg", use_cache=False)
        assert not ok and "h264" in reason
