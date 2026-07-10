"""§5.4 camera + §5.2 fixed-timestep loop (no GL / no ffmpeg needed)."""
import numpy as np

from osu_std_renderer.record.pipeline import RecordPipeline
from osu_std_renderer.render.playfield import PlayfieldCamera


def test_camera_1080p():
    cam = PlayfieldCamera(1920, 1080)
    # baseScale = 1080/384 = 2.8125 (512/384 < 1920/1080 → height-fit)
    assert abs(cam.base_scale - 2.8125) < 1e-9
    assert abs(cam.scl - 2.25) < 1e-9      # * 0.8 inset
    assert cam.to_screen(256, 192) == (960.0, 540.0)
    assert cam.to_screen(0, 0) == (384.0, 108.0)
    assert cam.to_screen(512, 384) == (1536.0, 972.0)
    x, y = cam.to_osu(*cam.to_screen(123.4, 56.7))
    assert abs(x - 123.4) < 1e-9 and abs(y - 56.7) < 1e-9


def test_camera_width_limited():
    # 4:3 output: width becomes the limiting side
    cam = PlayfieldCamera(1024, 768)
    assert abs(cam.base_scale - 768 / 384) < 1e-9  # 512/384 == 1024/768 → height
    cam2 = PlayfieldCamera(1000, 900)              # narrower than 4:3
    assert abs(cam2.base_scale - 1000 / 512) < 1e-9


def test_osu_shift():
    cam = PlayfieldCamera(1920, 1080, osu_shift=True)
    _, y0 = PlayfieldCamera(1920, 1080).to_screen(256, 192)
    _, y1 = cam.to_screen(256, 192)
    assert abs((y1 - y0) - 8 * cam.scl) < 1e-9


class _FakePlayer:
    """Ends after exactly 1000 ms of simulated time."""

    def __init__(self):
        self.t = 0.0
        self.updates = 0
        self.draws = 0

    def update(self, delta):
        self.t += delta
        self.updates += 1
        return self.t >= 1000.0

    def draw(self):
        self.draws += 1
        return np.zeros((2, 2, 3), dtype=np.uint8)


def test_record_loop_determinism():
    frames = []
    audio_ticks = [0]

    def push(f):
        frames.append(f)

    def audio():
        audio_ticks[0] += 1

    pipe = RecordPipeline(fps=60, push_frame=push, push_audio_1ms=audio)
    player = _FakePlayer()
    n = pipe.run(player)
    # 1000 ms at 1000 Hz sim → 999 loop bodies (map ends on update #1000)
    assert player.updates == 1000
    assert audio_ticks[0] == 999
    # 999 ms at 60 fps → floor(999/16.667) = 59 frames, ±1 for accumulator
    assert n == len(frames) == player.draws
    assert 58 <= n <= 60

    # determinism: same run → identical counts
    p2 = _FakePlayer()
    frames2 = []
    n2 = RecordPipeline(fps=60, push_frame=frames2.append,
                        push_audio_1ms=audio).run(p2)
    assert n2 == n and p2.updates == player.updates


def test_sim_rate_never_below_1000hz():
    # fps 2000 → updateDelta = 0.5 ms, audio still every 1 ms
    audio_ticks = [0]

    def audio():
        audio_ticks[0] += 1

    pipe = RecordPipeline(fps=2000, push_frame=lambda f: None,
                          push_audio_1ms=audio)
    player = _FakePlayer()
    pipe.run(player)
    assert player.updates == 2000          # 0.5 ms steps
    assert audio_ticks[0] == 999


def test_record_loop_pipelined_player_preserves_order_and_count():
    """PBO-ring players return [] while their pipeline fills and drain()
    the tail at map end: the pushed stream must contain every frame
    exactly once, in display order."""

    class _LaggedPlayer:
        LAG = 2

        def __init__(self):
            self.t = 0.0
            self.seq = 0
            self.pending = []

        def update(self, delta):
            self.t += delta
            return self.t >= 1000.0

        def draw(self):
            self.pending.append(
                np.full((1, 1, 3), self.seq % 251, dtype=np.uint8))
            self.seq += 1
            if len(self.pending) < self.LAG + 1:
                return []
            return [self.pending.pop(0)]

        def drain(self):
            out, self.pending = self.pending, []
            return out

    frames = []
    n = RecordPipeline(fps=60, push_frame=frames.append).run(_LaggedPlayer())
    assert n == len(frames)
    assert 58 <= n <= 60
    vals = [int(f[0, 0, 0]) for f in frames]
    assert vals == [i % 251 for i in range(n)]
