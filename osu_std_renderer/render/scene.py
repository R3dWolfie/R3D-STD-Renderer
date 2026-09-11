"""Object lifecycle + per-frame scene draw — the Phase-1 "first moving
render" pass.

RENDER_PLAN.md semantics implemented here:
  §2.5 circle fades   — fade-in over TimeFadeIn from startTime-Preempt,
                        full until the hit, then the hit-explosion: alpha
                        1→0 and scale 1→1.4 over HitFadeOut=240 ms (skin
                        v2+ shape). Combo number does the v2+ quick 60 ms
                        fade instead of scaling with the explosion.
  §2.5 approach       — ring scales 4→1 over [startTime-Preempt,
                        startTime], combo-coloured, alpha capped at 0.9,
                        gone at the hit.
  §5.3 draw order     — objects iterate REVERSED (newest under oldest);
                        a slider's body under its own end circle / ball /
                        head; approach circles on a top layer; cursor on
                        top of everything.
  §4.9 Snaking.In     — body grows over the first third of the preempt
                        (lazer SnakingSliderBody timing); the tail circle
                        rides the snake tip.

JUDGMENT PHASE (ruleset/ruleset.py drives this; pass judgments=SimResult):
  * explosions fire at the REAL hit time (click delta), not startTime;
    a missed circle/slider-head never explodes — it quick-fades out at
    its window close (stable's brief red-tint variant is SKIPPED — plain
    fade; noted per the plan).
  * judgment popups: procedural 300/100/50 numbers + red miss X at the
    object's popup position, ResultFadeIn=120 / ResultFadeOut=600 (§2.4);
    300 popups draw fainter so a clean run doesn't strobe.
  * sliderbreaks read as the ball detaching: the follow ball dims while
    the ruleset says tracking was lost (and there is no tail explosion —
    the phase-1 body fade already has none).
  * without a replay/judgments (--dump-frames debug, future --no-replay)
    every object falls back to the Phase-1 "perfect hit at startTime".

BACKGROUND + DIM PHASE (§4.10, render/background.py): the map background
draws FIRST (under everything), aspect-filled to the frame, tinted grey
by 1 - DimEnvelope.level(t) so only the background dims. No bg → the
dark-void clear, unchanged.

SKIN PHASE (render/skin_elements.py): when a SkinElements is attached,
each core element the skin provides replaces its procedural stand-in —
hitcircle (combo-tinted) + hitcircleoverlay (untinted, above the circle,
under the number unless HitCircleOverlayAboveNumber), approachcircle
(tinted), HitCirclePrefix combo digits (HitCircleOverlap layout), sliderb
(tint per AllowSliderBallTint/SliderBall), sliderfollowcircle (2.4× while
tracking), skin.ini SliderBorder/SliderTrackOverride body colours, skin
cursor/trail/middle (behind --skin-cursor, CursorCentre honored), hit0/
50/100/300 judgment sprites (a fully-transparent skin texture = draw
NOTHING — the classic empty hit300). Elements the skin lacks fall back
per-element to the procedural set (the §3.1 LOCAL source).

ARROWS + TICKS + FOLLOW POINTS PHASE (render/markers.py holds the pure
schedule/lifecycle math — see its docstring for the semantics):
  * reverse arrows on the active repeat end, rotated along the path
    tangent INTO the slider, white (never combo-tinted), beat-pulsed
    (§3.2 version gate: v<2 ±6° wobble, v2+ 1.3→1.0 scale), exploding
    on a hit repeat / vanishing on a sliderbreak.
  * slider ticks for the ACTIVE span only, snake-gated, popping when
    hit / vanishing when missed (per the ruleset's part outcomes).
  * follow points between same-combo neighbours (never spinners) on the
    lazer schedule; DrawFollowPoints honored (draw_follow_points).
  * slider heads/tails use sliderstartcircle/sliderendcircle(+overlays)
    when the skin ships them (§3.3 GetMostSpecific vs hitcircle).

CURSOR TRAIL (§3.3 two-mode rule, skin cursor only): skin HAS
cursormiddle (or ForceLongTrail) → the LONG CONNECTED trail, STRICTLY
DISTANCE-BASED (owner-directed 2026-07): the whole replay path is
resampled ONCE at init into points every LONG_TRAIL_SPACING_OSU osu!px
of cursor TRAVEL (build_distance_trail — interpolating between replay
frames, so spacing is uniform at ANY cursor speed: fast = a continuous
ribbon, never separated dots; slow = the same per-point brightness,
just a shorter ribbon — points are only emitted by MOVEMENT, so they
can never stack up and over-brighten in place). Per frame the points
inside the last LONG_TRAIL_WINDOW_MS are drawn (newest
LONG_TRAIL_MAX_POINTS≈stable's 2048 at most), each at a UNIFORM base
alpha scaled only by its age fade along the ribbon.
No cursormiddle → the classic sparse trail: one sprite dropped every
16.67 ms, each fading over TRAIL_SPRITE_LIFE_MS. The procedural (non-skin)
cursor keeps its shader-style glow trail.

SPINNER PHASE (render/spinner.py holds the pure math — see its docstring
for the lazer-legacy reference semantics):
  * §3.3 style auto-detect: skin has spinner-background → OLD style
    (background + metre bottom-up bar reveal + rotating spinner-circle);
    else new-style sprites → NEW (additive blue glow ramping with
    progress, bottom at rotation/3, top+middle2 at full rotation, middle
    fading white→red over the duration); no spinner sprites → the
    procedural Argon-ish spinner (outer ring + hub + orbiting marker +
    progress glow).
  * rotation tracks the replay cursor continuously (signed per-frame
    deltas while a key is held — the ruleset's accumulation, kept
    signed); --no-replay auto-spins at the §2.5 RPMS constant (477 RPM).
  * spinner-approachcircle shrinks 1.9→0.1 over the spin (§2.5);
    spinner-spin fades out ~0.5 s after the start; spinner-clear pops in
    when accumulated rotation meets the OD requirement; live RPM readout
    (spinner-rpm box + ScorePrefix skin digits, else HUD glyphs).
  * skin.ini SpinnerFadePlayfield dims everything under the spinner
    while it's alive; SpinnerNoBlink stills the metre's blinking bar.
  * the spinner judgment popup comes from the ruleset like every object.

HIT LIGHTING (§3.3 `lighting`): combo-tinted additive flash under each
non-miss judgment popup at its popup position, 400 ms lazer-ish
fade+expand; procedural fallback is the radial `glow` texture;
--no-hit-lighting (Gameplay.ShowHitLighting; R3D preset default ON).

SETTINGS-SURFACE PHASE (2026-07 — render/effects.py holds the pure math,
settings.py the preset mapping):
  * background: video (video_bg.py, fail-soft to image), blur-at-load,
    parallax (~1.02× sliding opposite the cursor), flash-to-beat
    brightness pulse, drifting triangles deco (dim-matched);
  * flow: real fade-to-black after the last object (audio fades with it
    in the CLI), seizure-warning card + lead-in pre-roll, the R3D "R"
    logo splash fading out exactly at the first approach;
  * §4.9: snaking OUT (snake_range — final span only, lazer semantics;
    the end cap rides the retracting tip, danser-style) and SliderMerge
    (_draw_merged_bodies → slider_body.build_merged: every visible body
    in ONE distance pass, unified colour = the oldest visible slider's,
    max alpha — accepted approximations, danser does the same);
  * cursor: trail scale, rainbow hue-cycle (procedural + skin tint),
    press-edge ripples;
  * break warning arrows (stable-style, blinking before the resume
    anchor); bloom post-pass applied AFTER cursor and BEFORE HUD (crisp
    HUD — owner call), strength pulsed to the beat when enabled.

REMAINING SIMPLIFICATIONS (each is a later-phase item):
  * slider bodies are rebuilt every frame (0.2 ms/body — fine at this
    phase; an FBO cache per static body is the known perf step).
  * skin gaps: see skin_elements.py's honest list (HUD fonts/scorebar
    stay procedural or absent; animations take frame 0 except
    sliderb/followpoint which cycle per AnimationFramerate; no cursor
    rotate/expand).
  * spinner approximations (spinner.py docstring): lazer's small
    Y-offset constants skipped (centred on the playfield centre); the
    metre blink is a deterministic 30 ms wave, not per-frame RNG;
    spinner hitsounds/bonus-spin ticks are the hitsound phase's.

The lifecycle math lives in module-level pure functions so tests need no
GL context.
"""
from __future__ import annotations

import bisect
import math
import os
from dataclasses import replace

import numpy as np

from ..beatmap.difficulty import (HIT_FADE_OUT, RESULT_FADE_IN,
                                  RESULT_FADE_OUT)
from ..beatmap.objects import Slider, Spinner
from ..replay.replay import cursor_at
from ..ruleset import JudgmentKind
from .background import PARALLAX_SCALE, flash_factor, parallax_offset
from .bloom import beat_strength
from .effects import (LOGO_UI_SIZE, SMOKE_DEFAULT_WIDTH_OSU,
                      SMOKE_INITIAL_FADE_MS, SMOKE_INITIAL_SCALE,
                      SMOKE_ROT_MS, SMOKE_SCALE_MS, break_resume_anchors,
                      fade_to_black_alpha, logo_alpha, logo_scale,
                      rainbow_rgb, ripple_events, ripple_states,
                      seizure_alpha, smoke_point_alpha, smoke_segments,
                      triangle_field, triangle_states, warning_arrow_alpha)
from .gl import Sprite
from .hud import layout_run
from .mods import (HIDDEN_FADE_IN_MULT, MOD_FLASHLIGHT, MOD_HIDDEN,
                   build_flashlight_timeline, flashlight_size_at,
                   hidden_circle_fade, hidden_slider_fade)
from .transform_mods import (DEFLATE, GROW, HIDES_APPROACH, IDENTITY, SPIN_IN,
                             TRANSFORM, WIGGLE, ObjTransform,
                             grow_deflate_scale, spin_in_circle,
                             spin_in_slider_scale, transform_appear_offset,
                             transform_offset_at, wiggle_events,
                             wiggle_offset_at)
from .appearance_mods import (approach_different_scale, freeze_frame_scale,
                              freeze_preempts)
from . import perf
from . import screen_mods as _sm
from . import repel_magnet as _rm
from .repel_magnet import MAGNETISED, REPEL
from .markers import (arrow_alpha_scale, arrow_pulse, arrow_rotation,
                      beat_phase, followpoint_dots, followpoint_eligible,
                      followpoint_state, reverse_arrow_schedule,
                      tick_alpha_scale, tick_schedule)
from .skin_elements import (FOLLOW_CIRCLE_SCALE, CURSOR_UI_HEIGHT,
                            circle_pixel_scale, layout_skin_digits)
from .slider_body import DEFAULT_COMBO_COLORS, BodyStyle, sub_path
from .textures import ARGON_DIGIT_CAP_SCALE, ARGON_GLYPH_CAP_SCALE
from .spinner import (CLEAR_OFFSET_OSU, GLOW_BLUE, SPIN_OFFSET_OSU,
                      SPINNER_CENTRE, SPRITE_SCALE, SpinnerTrack,
                      bonus_popup_state, bonus_spin_events, clear_alpha_scale,
                      detect_spinner_style, lighting_alpha_scale,
                      metre_bar_count, required_rotations,
                      spinner_approach_scale, spinner_bonus_thresholds,
                      spin_prompt_alpha, wants_lighting)


def ssaa_internal_size(width: int, height: int) -> tuple[int, int]:
    """Supersample resolution for the results outro (results-1080p-ssaa).

    The results card is laid out in a 1080-virtual space scaled by k=h/1080,
    so a sub-1080p output bakes/rasterises its text below native and it
    aliases. We render the results composite at a FIXED internal height of
    at least 1080 (width scaled to preserve the output aspect ratio) and
    downscale each results frame to the output res — crisp text at any
    resolution. A NO-OP at >=1080p (returns the output size unchanged): 4K
    and 1080p renders already carry native-or-better results detail.
    """
    w, h = int(width), int(height)
    ih = max(1080, h)
    if ih == h:
        return (w, h)
    iw = max(2, round(w * ih / h))
    return (iw, ih)


EXPLODE_SCALE = 1.5            # §2.5 hit-explosion end scale (skin v2+).
FAST_HIT_FADE_OUT = 80.0      # #38371 hit-animations OFF: quick fade-out, no explosion pop
                               # m-5: nudged 1.4→1.5 so the just-hit circle
                               # expands slightly larger, matching danser (the
                               # soft halo is the retained subtle hit lighting).
NUMBER_FADE_OUT = 60.0         # §3.2 v2+ combo-number quick fade (ms)
# Combo-number scale — 0.8 is BOTH the Red-approved live default
# (2026-07-11) AND stable's ground truth: lazer's LegacyMainCirclePiece
# draws hitCircleText at Scale(0.8f), which is why instafade skins bake
# their circle art into 160px digit canvases (160 × 0.8 = 128 logical px
# = exactly the CS circle diameter). A 2026-07-21 WIP snapshot (9e0f05f)
# leaked 0.72 here, shrinking instafade circles ~10% vs in-game — forum
# bug 11 item 2 (chitanda skin). Do NOT change without matching stable.
_COMBO_NUMBER_SCALE = float(os.environ.get("R3D_COMBO_NUMBER_SCALE", "0.8"))
MISS_FADE_OUT = 60.0           # missed circle: quick fade at window close
RESULT_HOLD = 250.0            # M-2: judgment popups hold full opacity this
                               # long after fade-in before ResultFadeOut
APPROACH_START_SCALE = 4.0     # §2.5 approach circle 4→1
APPROACH_MAX_ALPHA = 0.9       # stable caps the approach ring alpha
# MG/RP: a render step larger than this (ms) is treated as a discontinuity and
# the eased state is re-seeded from each object's spawn (keyframe/seek path);
# the monotonic render pass steps ~1000/fps << this, so it integrates inline.
_RM_MAX_STEP_MS = 200.0
SNAKE_IN_PORTION = 1.0 / 3.0   # lazer: snake-in completes after preempt/3
TRAIL_WINDOW_MS = 150.0
TRAIL_STEPS = 12
# skin-cursor trail (§3.3 two-mode rule; see module docstring)
TRAIL_SPRITE_INTERVAL_MS = 1000.0 / 60.0   # sparse mode: 16.67 ms drops
TRAIL_SPRITE_LIFE_MS = 150.0               # sparse sprite fade-out
LONG_TRAIL_SPACING_OSU = 2.0               # long mode: point every 2 osu!px
LONG_TRAIL_MAX_POINTS = 2048               # stable LongTrailLength scale
LONG_TRAIL_WINDOW_MS = 800.0               # long-mode age-out horizon
TICK_LOGICAL_PX = 16.0         # procedural slider-tick dot, logical skin px
FP_DOT_LOGICAL_PX = 16.0       # procedural followpoint dot, logical skin px
CURSOR_RADIUS_OSU = 14.0       # cursor core sizing base, osu!px
CURSOR_GLOW_COLOR = (1.0, 0.30, 0.35)   # R3D red
DIGIT_SPACING = 0.06           # of digit height, between combo digits
BALL_DETACHED_ALPHA = 0.35     # slider ball while tracking is lost
BALL_FADE_OUT_MS = 240.0       # DrawableSlider.FadeOut(240).Expire() at EndTime
FOLLOW_CIRCLE_IN_MS = 300.0    # DefaultFollowCircle tracking-gain: ScaleTo(FOLLOW_AREA,300,OutQuint) + FadeIn


def slider_ball_alpha(t: float, start: float, end: float,
                      active_alpha: float) -> float | None:
    """Slider-ball alpha over its lifetime, or None outside it.

    From `start` to `end` the ball shows at `active_alpha` (1.0 tracking,
    BALL_DETACHED_ALPHA when tracking is lost). At EndTime osu's
    DrawableSlider does FadeOut(240).Expire(); the ball, a child, fades
    linearly (Easing.None) from its current alpha to 0 over
    BALL_FADE_OUT_MS instead of hard-cutting. OsuModHidden's body fade
    never touches the ball. None before `start` or after the fade."""
    if t < start or t > end + BALL_FADE_OUT_MS:
        return None
    if t <= end:
        return active_alpha
    return active_alpha * max(0.0, 1.0 - (t - end) / BALL_FADE_OUT_MS)
POPUP_HEIGHT_FRAC = 0.62       # judgment number height / circle radius
POPUP_COLORS = {
    JudgmentKind.HIT300: (0.38, 0.72, 1.00),   # Argon-ish blue
    JudgmentKind.HIT100: (0.42, 0.88, 0.47),   # green
    JudgmentKind.HIT50: (0.95, 0.76, 0.36),    # orange
    JudgmentKind.MISS: (0.95, 0.25, 0.30),     # red X
}
POPUP_300_ALPHA = 0.45         # 300s draw fainter (anti-strobe; procedural only)
POPUP_SKIN_ELEMENT = {         # judgment kind → skin sprite element
    JudgmentKind.MISS: "hit0",
    JudgmentKind.HIT50: "hit50",
    JudgmentKind.HIT100: "hit100",
    JudgmentKind.HIT300: "hit300",
}
# classic miss animation (stable / lazer LegacyJudgementPieceOld): the
# hit0 popup falls + slightly rotates while fading (miss_fall_transform)
MISS_FALL_DISTANCE_OSU = 100.0     # MoveToOffset (0, 100) over the fade
MISS_FALL_ROT_RAD = math.radians(8.6)   # RotateTo(RNG ±8.6°) stand-in

# --- FAIL animation (port of ppy/osu FailAnimationContainer.cs, MIT) -----------------
# The whole sequence runs `duration = 2500` ms of WALL time from the death
# point; the CLI scales it by the rate mod into map-ms. Values quoted from
# the source (Start()/dropOffScreen()); see render_fail_frame().
FAIL_DURATION_MS = 2500.0          # const float duration = 2500
FAIL_FALL_OSU = 400.0              # MoveTo(originalPosition + (0, 400))
FAIL_ROT_RANGE_DEG = 90.0          # RNG.NextSingle(-90, 90)  (degrees)
FAIL_OBJ_SCALE_TO = 0.5            # ScaleTo(originalScale * 0.5f)
FAIL_CONTENT_SCALE_TO = 0.85       # Content.ScaleTo(0.85, OutQuart)
FAIL_CONTENT_ROT_DEG = 1.0         # Content.RotateTo(1, OutQuart)  (degrees)
FAIL_GRAY = 0.5                    # Content.FadeColour(Color4.Gray) → ×0.5 rgb
FAIL_BG_GRAY = 0.3                 # Background.FadeColour(OsuColour.Gray(0.3))
FAIL_BG_FADE_MS = 60.0             #   … over 60 ms
FAIL_OBJ_FADE_MS = FAIL_DURATION_MS / 2.0   # HitObjectContainer.FadeOut(dur/2)
FAIL_RED_ALPHA = 0.6               # redFlashLayer Color4.Red.Opacity(0.6f)
FAIL_RED_FADE_MS = 1000.0          # redFlashLayer.FadeOutFromOne(1000)

# playfield borders (--playfield-borders none|edges|full): subtle
# low-alpha white outline of the playfield bounds
BORDER_THICKNESS_OSU = 1.6         # ~2 screen px at 1080p
BORDER_CORNER_LEN_OSU = 24.0       # "edges" corner-L stroke length
BORDER_ALPHA = 0.28

# --- spinner draw constants (render/spinner.py has the lifecycle math) ---------
SPINNER_DIM_ALPHA = 0.85       # SpinnerFadePlayfield backdrop strength
PROC_SPINNER_RING_OSU = 330.0  # procedural: outer ring diameter, osu!px
PROC_SPINNER_GLOW_OSU = 380.0  # procedural: progress glow diameter
PROC_SPINNER_HUB_OSU = 90.0    # procedural: centre hub diameter
PROC_MARKER_OSU = 26.0         # procedural: orbiting marker dot
PROC_MARKER_RADIUS_OSU = 132.0
PROMPT_TEXT_UI = 44.0          # procedural SPIN!/CLEAR! height (768-space)
RPM_TEXT_UI = 30.0             # procedural RPM readout height (768-space)
RPM_BOTTOM_MARGIN_UI = 84.0    # RPM bottom offset (768-space) — raised off
                               # the true bottom edge so it clears the HUD's
                               # hit-error/UR block (stable overlaps them)
RPM_DIGIT_FRAC = 0.62          # skin digits: height / rpm-box height
RPM_RIGHT_PAD_FRAC = 0.06      # skin digits: right inset / box width
LIGHTING_LOGICAL_PX = 155.0    # procedural lighting glow size (circle-tied).
                               # M-1: was 260 — a ~2× circle cloud that, added
                               # over dense streams, dominated the frame vs
                               # danser's subtle default lighting. Tied nearer
                               # the circle diameter now (see _lighting_sprites
                               # alpha too).
LIGHTING_PROC_ALPHA = 0.34     # M-1: procedural-glow additive peak (was 0.85)
# ArgonSpinner.bonusCounter: OsuFont.Default size 28 Bold, Y=-100 above centre;
# FlashColour("FC618F") on MAX. Argon-league only (custom skins unchanged).
SPINNER_BONUS_FONT_OSU = 28.0  # OsuFont.Default size 28
SPINNER_BONUS_OFFSET_OSU = 100.0   # bonusCounter Y = -100 (osu!px above centre)
SPINNER_BONUS_FLASH_MS = 400.0     # MAX FlashColour(FC618F, 400)
MIDDLE_RED = (1.0, 0.0, 0.0)   # spinner-middle fade target (white→red)
WARN_SPRITE_OSU = 160.0        # arrow-warning canvas edge in osu!px (≈240 px
                               # at 720p → the ~96 px white diamond danser draws)

# --- Argon league (skinless) gameplay constants -------------------------------
# All applied ONLY when self.skin is None (the "Argon league"). ppy/osu master
# (MIT) Argon* ruleset pieces; the custom-skin path is byte-for-byte unchanged.
ARGON_OUTER_GRAD_R = 50.0 / 58.0      # ArgonSliderBody path radius / OBJECT_RADIUS
ARGON_BALL_DIAM_FRAC = 50.0 / 58.0    # ArgonSliderBall dia / OBJECT_DIMENSIONS
ARGON_GRAD_FRAC = 10.0 / 58.0         # GRADIENT_THICKNESS / OBJECT_RADIUS
ARGON_SLIDER_BORDER_WIDTH = 1.815     # border_portion(1.815)=0.2 → GRADIENT_THICKNESS
ARGON_SLIDER_BODY_ALPHA = 0.98        # ArgonSliderBody BodyAlpha (non-pro)
ARGON_FOLLOW_AREA = 2.4               # DrawableSliderBall.FOLLOW_AREA
ARGON_TICK_FRAC = 12.0 / 118.0        # ArgonSliderScorePoint SIZE / OBJECT_DIMENSIONS
ARGON_SPIN_GLOW = (0xFC / 255.0, 0x61 / 255.0, 0x8F / 255.0)   # spinner fill glow
ARGON_CURSOR_TRAIL = (1.0, 1.0, 1.0)       # ArgonCursorTrail sets NO Colour
                                           # override → drawn WHITE (osu.Game.
                                           # Rulesets.Osu/Skinning/Argon/
                                           # ArgonCursorTrail.cs). NOT pink.
ARGON_CURSOR_CGLOW = (171 / 255.0, 1.0, 1.0)   # centre EdgeEffect (cyan)
ARGON_FP_LOGICAL_PX = 30.0        # ArgonFollowPoint chevron box (logical px)
# ArgonCursorTrail: additive WHITE, IntervalMultiplier 0.4 (tight), FadeExponent
# 4 (non-linear fade along the ribbon). The base CursorTrail (UI/Cursor/
# CursorTrail.cs) fades each part over FadeDuration=300 ms as
# alpha = baseAlpha * (1 - age/FadeDuration)^FadeExponent — our long_trail_points
# strength = 1 - age/window, then the scene raises it to ARGON_TRAIL_FADE_EXP and
# scales by ARGON_TRAIL_ALPHA, so the WINDOW must be the full 300 ms FadeDuration
# for the falloff shape to match. Pass-4: owner flagged the trail as "off" — it
# was 120 ms, which truncated the ribbon at ~52 ms of path (vs lazer's ~131 ms
# visible), a stubby blob. 300 ms restores the smooth continuous ribbon.
ARGON_TRAIL_WINDOW_MS = 300.0     # == base CursorTrail FadeDuration
ARGON_TRAIL_SPACING_OSU = 3.0     # dense distance-resample → continuous line
ARGON_TRAIL_WIDTH_OSU = 14.0      # ArgonCursorTrail Scale 0.8 of the soft
                                  # cursortrail blob ≈ the cursor RADIUS — a
                                  # soft trail, not a 7px hairline (fidelity
                                  # pass 3: pass 2's wire-thin line read wrong;
                                  # FadeExponent 4 still keeps the tail short)
ARGON_TRAIL_FADE_EXP = 4.0        # ArgonCursorTrail.FadeExponent
ARGON_TRAIL_ALPHA = 0.8           # ArgonCursorTrail Alpha
# ArgonJudgementPiece.RingExplosion (kiai "bubbles"): additive RingPiece bits,
# coloured OsuColour.ForHitResult, scattering out on a random direction.
ARGON_RING_THICKNESS = 4.0 / 128.0     # RingPiece thickness / OBJECT_DIMENSIONS
ARGON_RING_SMALL_OSU = 9.0             # small bubble diameter (osu!px @ CS auth)
ARGON_RING_LARGE_OSU = 14.0            # large bubble diameter
ARGON_RING_TRAVEL_OSU = 52.0           # base scatter distance
ARGON_RING_MOVE_MS = 600.0             # MoveTo over 600 (OutQuint)
ARGON_RING_FADE_MS = 1000.0            # FadeOutFromOne(1000, OutQuint)
# ArgonMainCirclePiece flash: outerGradient FadeColour(White,80).Then FadeOut,
# FlashPiece FadeTo(1,150).Then FadeOut(150) — a brief pale bloom.
ARGON_FLASH_IN_MS = 150.0
ARGON_FLASH_LIFE_MS = 300.0            # in 150 + out 150 (non-hit-lighting)
ARGON_FLASH_CORE_ALPHA = 0.55         # white outerGradient bloom
ARGON_FLASH_GLOW_ALPHA = 0.5          # accent FlashPiece glow
# ArgonMainCirclePiece.kiaiContainer child = KiaiFlash (osu.Game.Rulesets.Osu/
# Skinning/Default/KiaiFlash.cs): an additive white Box masked to the circle,
# tinted by the accent (combo) colour, that BeatSyncedContainer flashes each
# red-line beat DURING kiai — FadeTo(0.25, 80, OutQuint) up to the beat (fired
# EarlyActivationMilliseconds=80 before it), then FadeOut(max(80, beatLength−80),
# OutSine). A subtle combo-tinted pulse on the circle while it's on screen.
KIAI_FLASH_OPACITY = 0.25             # KiaiFlash.flash_opacity
KIAI_FLASH_EARLY_MS = 80.0            # KiaiFlash EarlyActivationMilliseconds
                                      # (== fade_length, the FadeTo duration)
# ArgonJudgementPiece: OsuColour.ForHitResult + uppercase result text
ARGON_JUDGE_TEXT = {
    JudgmentKind.HIT300: "GREAT",
    JudgmentKind.HIT100: "OK",
    JudgmentKind.HIT50: "MEH",
    JudgmentKind.MISS: "MISS",
}
ARGON_JUDGE_COLOR = {                             # OsuColour hex → linear-ish rgb
    JudgmentKind.HIT300: (0x66 / 255.0, 0xCC / 255.0, 0xFF / 255.0),   # Blue
    JudgmentKind.HIT100: (0x88 / 255.0, 0xB3 / 255.0, 0x00 / 255.0),   # Green
    JudgmentKind.HIT50: (0xFF / 255.0, 0xCC / 255.0, 0x22 / 255.0),    # Yellow
    JudgmentKind.MISS: (0xED / 255.0, 0x11 / 255.0, 0x21 / 255.0),     # Red
}
# ArgonJudgementPiece.CreateJudgementText (ppy/osu master,
# osu.Game.Rulesets.Osu/Skinning/Argon/ArgonJudgementPiece.cs):
#   Font = OsuFont.Default.With(size: 20, weight: FontWeight.Bold)
#   Spacing = new Vector2(5, 0)
# Text now renders in the bundled Argon font (Nunito) whose caps fill ~0.7025
# of the glyph sprite. The run scales its height by ARGON_GLYPH_CAP_SCALE
# (DejaVu 0.7154 / Nunito 0.7025) so a size-20 cell keeps the SAME ~14.4-osu!px
# visible cap the DejaVu tuning had — matching lazer's Torus size-20 cap
# (~0.72·20 = 14.4). (Both were once inflated — 25/7 "for legibility" — which
# read ~27% too big vs lazer.)
ARGON_JUDGE_FONT_OSU = 20.0     # OsuSpriteText size 20
ARGON_JUDGE_SPACING_OSU = 5.0   # Spacing (5, 0) at size 20
ARGON_JUDGE_LIFE_MS = 800.0     # FadeOutFromOne(800)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# --- easing (pure; ArgonJudgementPiece transforms) ----------------------------

def _ease_out_quint(p: float) -> float:
    return 1.0 - (1.0 - _clamp01(p)) ** 5


def _ease_in_quint(p: float) -> float:
    return _clamp01(p) ** 5


def _ease_in_quad(p: float) -> float:
    return _clamp01(p) ** 2


def _ease_out_sine(p: float) -> float:
    return math.sin(_clamp01(p) * (math.pi / 2.0))


def argon_kiai_flash_strength(t: float, spawn: float, timings) -> float:
    """KiaiFlash additive opacity (0..0.25) on the Argon circle at map time t,
    for a circle piece that spawned (approached) at `spawn`.

    osu.Game.Rulesets.Osu/Skinning/Default/KiaiFlash.cs — a BeatSyncedContainer
    child of ArgonMainCirclePiece.kiaiContainer. OnNewBeat, only while
    effectPoint.KiaiMode, it FadeTo(0.25, 80, OutQuint) then FadeOut(max(80,
    beatLength-80), OutSine); EarlyActivationMilliseconds=80 fires the beat 80 ms
    early, so the ramp peaks exactly ON the (red-line) beat. Only beats whose
    activation (beat-80) lands at/after the piece is alive contribute. The two
    nearest beats (the one we're rising toward, the one we're falling from) are
    considered; their envelopes meet at ~0, so the max reads as one value."""
    red = timings.get_original_point_at(t)
    bl = red.beat_length_base
    if not (bl > 0.0) or math.isnan(bl):
        return 0.0
    n = math.floor((t - red.time) / bl)
    strength = 0.0
    for k in (n, n + 1):
        beat = red.time + k * bl
        if beat - KIAI_FLASH_EARLY_MS < spawn:      # not alive when it'd fire
            continue
        if not timings.get_point_at(beat).kiai:      # beat not in kiai
            continue
        if t < beat - KIAI_FLASH_EARLY_MS:
            continue
        if t < beat:                                 # FadeTo(0.25, 80, OutQuint)
            s = KIAI_FLASH_OPACITY * _ease_out_quint(
                (t - (beat - KIAI_FLASH_EARLY_MS)) / KIAI_FLASH_EARLY_MS)
        else:                                        # FadeOut(dur, OutSine)
            dur = max(KIAI_FLASH_EARLY_MS, bl - KIAI_FLASH_EARLY_MS)
            age = t - beat
            if age >= dur:
                continue
            s = KIAI_FLASH_OPACITY * (1.0 - _ease_out_sine(age / dur))
        if s > strength:
            strength = s
    return strength


def argon_judgment_transform(kind, age_ms: float):
    """ArgonJudgementPiece.PlayAnimation → (alpha, scale, dy_osu, rot_rad) or
    None once expired. Hits: whole piece FadeOutFromOne(800) × text
    FadeInFromZero(300, OutQuint), text ScaleTo 1→1.2 over 1800 (OutQuint).
    Miss: FadeOutFromOne(800), ScaleTo 1.6→1 over 100 (In), MoveToOffset
    (0,100) over 800 (InQuint), RotateTo 40° over 800 (InQuint)."""
    if age_ms < 0.0 or age_ms >= ARGON_JUDGE_LIFE_MS:
        return None
    life = ARGON_JUDGE_LIFE_MS
    if kind is JudgmentKind.MISS:
        alpha = 1.0 - age_ms / life
        scale = (1.6 + (1.0 - 1.6) * _ease_in_quad(age_ms / 100.0)
                 if age_ms < 100.0 else 1.0)
        pm = _ease_in_quint(age_ms / life)
        return alpha, scale, 100.0 * pm, math.radians(40.0) * pm
    alpha = (1.0 - age_ms / life) * _ease_out_quint(min(age_ms, 300.0) / 300.0)
    scale = 1.0 + 0.2 * _ease_out_quint(min(age_ms, 1800.0) / 1800.0)
    return alpha, scale, 0.0, 0.0


# --- lifecycle math (pure; unit-tested) ---------------------------------------

def fade_in_alpha(t: float, start_time: float, preempt: float,
                  time_fade_in: float) -> float:
    """§2.5: 0→1 over TimeFadeIn starting at startTime - Preempt."""
    t0 = start_time - preempt
    if t < t0:
        return 0.0
    if time_fade_in <= 0:
        return 1.0
    return _clamp01((t - t0) / time_fade_in)


def circle_alpha_scale(t: float, start_time: float, preempt: float,
                       time_fade_in: float,
                       hit_time: float | None = None,
                       fade_out: float = HIT_FADE_OUT,
                       explode_scale: float = EXPLODE_SCALE) -> tuple[float, float]:
    """(alpha, scale) for a hit-circle disc+ring. Phase 1 assumes the hit
    lands exactly at startTime (pass hit_time when the ruleset exists)."""
    hit = start_time if hit_time is None else hit_time
    if t < start_time - preempt:
        return 0.0, 1.0
    if t <= hit:
        return fade_in_alpha(t, start_time, preempt, time_fade_in), 1.0
    p = (t - hit) / fade_out
    if p >= 1.0:
        return 0.0, explode_scale
    return 1.0 - p, 1.0 + (explode_scale - 1.0) * p


def approach_scale_alpha(t: float, start_time: float, preempt: float,
                         time_fade_in: float) -> tuple[float, float] | None:
    """(scale, alpha) for the approach ring, or None outside its life
    ([startTime - Preempt, startTime); it vanishes at the hit)."""
    if t >= start_time or t < start_time - preempt:
        return None
    w = _clamp01((t - (start_time - preempt)) / preempt)
    scale = APPROACH_START_SCALE - (APPROACH_START_SCALE - 1.0) * w
    alpha = min(fade_in_alpha(t, start_time, preempt, time_fade_in),
                APPROACH_MAX_ALPHA)
    return scale, alpha


def number_alpha(t: float, start_time: float, preempt: float,
                 time_fade_in: float, hit_time: float | None = None) -> float:
    """Combo number: rides the fade-in, then the v2+ quick 60 ms fade."""
    hit = start_time if hit_time is None else hit_time
    if t <= hit:
        return fade_in_alpha(t, start_time, preempt, time_fade_in)
    return _clamp01(1.0 - (t - hit) / NUMBER_FADE_OUT)


def miss_fade_alpha(t: float, start_time: float, preempt: float,
                    time_fade_in: float, deadline: float,
                    classic: bool = False, ok: float | None = None) -> float:
    """Missed circle/slider-head: normal fade-in, full until the miss
    window closes, then a quick fade out (NO explosion, no scale). Stable's
    brief red tint is skipped (plain fade) — noted in the module docstring.

    OsuModClassic.FadeHitCircleEarly (Classic mod): the circle instead
    fades out INTO the miss — starting at start+ok (the 100 window) and
    reaching zero at start+meh (= deadline) — rather than holding full
    until the window close and quick-fading after it. Ports
    applyEarlyFading's Delay(okWindow).FadeOut(mehWindow - okWindow)."""
    if classic and ok is not None:
        fade_start = start_time + ok
        if t <= fade_start:
            return fade_in_alpha(t, start_time, preempt, time_fade_in)
        span = max(deadline - fade_start, 1e-6)
        return _clamp01(1.0 - (t - fade_start) / span)
    if t <= deadline:
        return fade_in_alpha(t, start_time, preempt, time_fade_in)
    return _clamp01(1.0 - (t - deadline) / MISS_FADE_OUT)


def popup_alpha_scale(t: float, popup_time: float) -> tuple[float, float] | None:
    """(alpha, scale) of a judgment popup, or None outside its life.
    §2.4 ResultFadeIn=120, then a HOLD at full, then ResultFadeOut=600.

    M-2: the popup used to start fading the instant the fade-in finished, so
    two closely-spaced judgments read as one crisp + one washed-out vs the
    reference's two crisp popups. Stable/lazer's legacy judgement holds full
    opacity briefly before the fade-out (the number stays legible while the
    next one appears), so we hold RESULT_HOLD ms at 1.0 first."""
    age = t - popup_time
    if age < 0:
        return None
    if age <= RESULT_FADE_IN:
        w = age / RESULT_FADE_IN
        return w, 0.85 + 0.15 * w
    held = age - RESULT_FADE_IN
    if held <= RESULT_HOLD:
        return 1.0, 1.0
    p = (held - RESULT_HOLD) / RESULT_FADE_OUT
    if p >= 1.0:
        return None
    return 1.0 - p, 1.0


def miss_fall_transform(age_ms: float,
                        seed: int = 0) -> tuple[float, float]:
    """(dy_osu, rotation_rad) of the FALLING miss popup at popup age
    `age_ms` — stable's classic hit0 animation (lazer's
    LegacyJudgementPieceOld miss branch: MoveToOffset((0, 100)) +
    RotateTo(±8.6°), both Easing.In over the popup's remaining life):
    the sprite starts at the normal popup position and slowly drifts
    DOWN (gravity-ish, quad-in over MISS_FALL_DISTANCE_OSU) with a
    slight rotation while the popup alpha fades. `seed` (the object id)
    picks a deterministic rotation direction/amount standing in for
    lazer's RNG.NextSingle(-8.6, 8.6) — same replay, same render."""
    dur = RESULT_FADE_IN + RESULT_HOLD + RESULT_FADE_OUT
    p = _clamp01(age_ms / dur)
    ease = p * p                       # Easing.In (quad)
    frac = (seed * 0.618033988749895) % 1.0     # golden-ratio hash → [0,1)
    rot_final = MISS_FALL_ROT_RAD * (2.0 * frac - 1.0)
    return MISS_FALL_DISTANCE_OSU * ease, rot_final * ease


# --- FAIL animation transforms (pure; FailAnimationContainer.cs port) ----------------

def _ease_out_quart(p: float) -> float:
    return 1.0 - (1.0 - _clamp01(p)) ** 4


def fail_progress(age_ms: float, duration_ms: float = FAIL_DURATION_MS) -> float:
    """[0,1] progress of the fail animation, `age_ms` after the death point."""
    if duration_ms <= 0.0:
        return 1.0
    return _clamp01(age_ms / duration_ms)


def fail_object_alpha(age_ms: float,
                      fade_ms: float = FAIL_OBJ_FADE_MS) -> float:
    """HitObjectContainer.FadeOut(duration / 2): objects fade 1→0 over the
    first half of the sequence, then stay gone."""
    return 1.0 - _clamp01(age_ms / fade_ms) if fade_ms > 0 else 0.0


def fail_red_alpha(age_ms: float) -> float:
    """redFlashLayer (Color4.Red.Opacity(0.6f), additive) FadeOutFromOne
    (1000): effective additive-red alpha = 0.6 × (1→0 over 1000 ms)."""
    return FAIL_RED_ALPHA * (1.0 - _clamp01(age_ms / FAIL_RED_FADE_MS))


def fail_gray_factor(prog: float) -> float:
    """Content.FadeColour(Color4.Gray) over the duration: the playfield's
    vertex colour lerps white(1)→gray(0.5) linearly → an rgb multiplier."""
    return 1.0 - (1.0 - FAIL_GRAY) * _clamp01(prog)


def fail_bg_gray(age_ms: float) -> float:
    """Background.FadeColour(OsuColour.Gray(0.3f), 60): the bg brightness
    multiplier drops 1→0.3 over 60 ms."""
    return 1.0 - (1.0 - FAIL_BG_GRAY) * _clamp01(age_ms / FAIL_BG_FADE_MS)


def fail_object_scale(prog: float) -> float:
    """ScaleTo(originalScale * 0.5f, duration): 1→0.5 linear."""
    return 1.0 - (1.0 - FAIL_OBJ_SCALE_TO) * _clamp01(prog)


def fail_content_scale(prog: float) -> float:
    """Content.ScaleTo(0.85f, duration, Easing.OutQuart)."""
    return 1.0 - (1.0 - FAIL_CONTENT_SCALE_TO) * _ease_out_quart(prog)


def fail_content_rotation(prog: float) -> float:
    """Content.RotateTo(1, duration, Easing.OutQuart) → radians."""
    return math.radians(FAIL_CONTENT_ROT_DEG) * _ease_out_quart(prog)


def fail_rotation_deg(seed: float) -> float:
    """Deterministic stand-in for RNG.NextSingle(-90, 90): a golden-ratio
    hash of a per-object seed (its start time) so the same replay drops the
    same way every render. osu re-seeds per play; we key on the object."""
    frac = (seed * 0.618033988749895) % 1.0
    return (2.0 * frac - 1.0) * FAIL_ROT_RANGE_DEG


def _rot2(dx: float, dy: float, a: float) -> tuple[float, float]:
    c, s = math.cos(a), math.sin(a)
    return dx * c - dy * s, dx * s + dy * c


def fail_transform_point(x: float, y: float, pivot: tuple[float, float],
                         center: tuple[float, float], obj_scale: float,
                         obj_rot: float, fall_px: float, content_scale: float,
                         content_rot: float) -> tuple[float, float]:
    """Rigid FailAnimation transform of a screen point: the object scales +
    rotates about its own anchor `pivot`, drops `fall_px` down, then the
    whole playfield Content scales + rotates about `center`."""
    dx, dy = (x - pivot[0]) * obj_scale, (y - pivot[1]) * obj_scale
    dx, dy = _rot2(dx, dy, obj_rot)
    x, y = pivot[0] + dx, pivot[1] + dy + fall_px
    dx, dy = (x - center[0]) * content_scale, (y - center[1]) * content_scale
    dx, dy = _rot2(dx, dy, content_rot)
    return center[0] + dx, center[1] + dy


def fail_transform_sprite(sp: Sprite, *, pivot, center, obj_scale, obj_rot,
                          fall_px, content_scale, content_rot, gray,
                          obj_alpha) -> Sprite:
    """Apply the FailAnimation transform to one sprite: rigid fall + gray
    tint (Content.FadeColour) + object fade (HitObjectContainer.FadeOut)."""
    x, y = fail_transform_point(sp.x, sp.y, pivot, center, obj_scale, obj_rot,
                                fall_px, content_scale, content_rot)
    s = obj_scale * content_scale
    r, g, b, a = sp.color
    return replace(sp, x=x, y=y, w=sp.w * s, h=sp.h * s,
                   rotation=sp.rotation + obj_rot + content_rot,
                   color=(r * gray, g * gray, b * gray, a * obj_alpha))


def playfield_border_rects(x0: float, y0: float, x1: float, y1: float,
                           mode: str, thickness: float,
                           corner_len: float) -> list[tuple[float, float,
                                                            float, float]]:
    """[(cx, cy, w, h)] solid rects outlining the playfield bounds
    (screen px, INSIDE the box):
      "full"  → the four thin edges of the border box
      "edges" → corner markers only: an L pair (corner_len long) at each
                of the four corners
      anything else → [] (borders off)."""
    t = thickness
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return []
    if mode == "full":
        return [
            (x0 + w / 2.0, y0 + t / 2.0, w, t),            # top
            (x0 + w / 2.0, y1 - t / 2.0, w, t),            # bottom
            (x0 + t / 2.0, y0 + h / 2.0, t, h - 2.0 * t),  # left
            (x1 - t / 2.0, y0 + h / 2.0, t, h - 2.0 * t),  # right
        ]
    if mode == "edges":
        c = min(corner_len, w / 2.0, h / 2.0)
        out: list[tuple[float, float, float, float]] = []
        for cx, sx in ((x0, 1.0), (x1, -1.0)):
            for cy, sy in ((y0, 1.0), (y1, -1.0)):
                # horizontal stroke then vertical stroke of the corner L
                out.append((cx + sx * c / 2.0, cy + sy * t / 2.0, c, t))
                out.append((cx + sx * t / 2.0, cy + sy * (t + (c - t) / 2.0),
                            t, c - t))
        return out
    return []


def snake_end_fraction(t: float, start_time: float, preempt: float,
                       snaking_in: bool = True) -> float:
    """§4.9 Snaking.In (lazer timing): 0→1 over the first preempt/3."""
    if not snaking_in:
        return 1.0
    return _clamp01((t - (start_time - preempt))
                    / (preempt * SNAKE_IN_PORTION))


def snake_range(t: float, start_time: float, part_len: float,
                repeat_count: int, preempt: float,
                snaking_in: bool = True,
                snaking_out: bool = True) -> tuple[float, float]:
    """(start, end) fractions of the path visible at t — §4.9 Snaking
    In AND Out (lazer SnakingSliderBody semantics):

      in   the end edge grows 0→1 over the first preempt/3 (always done
           well before the hit — snake-in finishes at start−2·preempt/3);
      out  ONLY during the FINAL span the body retracts behind the ball:
           a head→tail final span (even 0-based span index) advances the
           START edge u→1; a tail→head final span pulls the END edge
           1→1−u. Earlier spans keep the full body (repeats re-trace it).

    At u=1 the range degenerates to the ball's end point — the post-end
    body fade then fades a dot, not a full corpse (lazer: a fully snaked-
    out body leaves nothing behind)."""
    b = snake_end_fraction(t, start_time, preempt, snaking_in)
    a = 0.0
    if snaking_out and part_len > 0 and repeat_count >= 1:
        final_start = start_time + (repeat_count - 1) * part_len
        if t > final_start:
            u = _clamp01((t - final_start) / part_len)
            if (repeat_count - 1) % 2 == 0:     # final span runs head→tail
                a = u
            else:                                # runs tail→head
                b = min(b, 1.0 - u)
    return a, b


def body_alpha(t: float, start_time: float, end_time: float, preempt: float,
               time_fade_in: float) -> float:
    """Slider body/end-circle fade: in with the head, solid through the
    slide, out over HitFadeOut after endTime."""
    if t <= start_time:
        return fade_in_alpha(t, start_time, preempt, time_fade_in)
    if t <= end_time:
        return 1.0
    return _clamp01(1.0 - (t - end_time) / HIT_FADE_OUT)


def visible_window(obj, preempt: float) -> tuple[float, float]:
    """[spawn, retire] for drawing: startTime-Preempt .. endTime+HitFadeOut."""
    return (obj.get_start_time() - preempt,
            obj.get_end_time() + HIT_FADE_OUT)


def layout_digits(number: int, aspects: dict[str, float], height: float,
                  spacing: float = DIGIT_SPACING) -> list[tuple[str, float, float]]:
    """[(char, dx_of_center, width)] for the digits of `number`, centred
    on dx=0 at the given digit height."""
    s = str(max(0, int(number)))
    widths = [aspects.get(ch, 0.6) * height for ch in s]
    gap = spacing * height
    total = sum(widths) + gap * (len(s) - 1)
    out: list[tuple[str, float, float]] = []
    x = -total / 2.0
    for ch, w in zip(s, widths):
        out.append((ch, x + w / 2.0, w))
        x += w + gap
    return out


def trail_times(t: float, window_ms: float = TRAIL_WINDOW_MS,
                steps: int = TRAIL_STEPS) -> list[tuple[float, float]]:
    """[(sample_time, strength 0..1)] oldest→newest for the PROCEDURAL
    cursor's glow trail (the non-skin cursor keeps this look)."""
    step = window_ms / steps
    return [(t - i * step, 1.0 - i / (steps + 1.0))
            for i in range(steps, 0, -1)]


def skin_trail_is_long(has_cursormiddle: bool, force_long: bool) -> bool:
    """§3.3: long connected trail when `cursormiddle` exists or
    ForceLongTrail; else the sparse 16.67 ms sprite trail."""
    return has_cursormiddle or force_long


def sparse_trail_times(t: float,
                       interval_ms: float = TRAIL_SPRITE_INTERVAL_MS,
                       life_ms: float = TRAIL_SPRITE_LIFE_MS,
                       ) -> list[tuple[float, float]]:
    """[(drop_time, strength 0..1)] oldest→newest for the sparse skin
    trail: sprites drop on a fixed 16.67 ms grid (quantized to absolute
    time so drops don't slide between frames) and fade linearly over
    life_ms."""
    newest = math.floor(t / interval_ms) * interval_ms
    out: list[tuple[float, float]] = []
    k = 0
    while True:
        ti = newest - k * interval_ms
        strength = 1.0 - (t - ti) / life_ms
        if strength <= 0.0:
            break
        out.append((ti, strength))
        k += 1
    out.reverse()
    return out


def build_distance_trail(frames, spacing_osu: float = LONG_TRAIL_SPACING_OSU,
                         ) -> list[tuple[float, float, float]]:
    """[(x_osu, y_osu, pass_time_ms)] — the ENTIRE replay's cursor path
    resampled by TRAVEL DISTANCE: one point every spacing_osu osu!px of
    cursor movement, positions AND pass times linearly interpolated
    INSIDE replay-frame segments so the spacing stays uniform at any
    cursor speed (a 400 px flick between two frames still yields a point
    every spacing_osu px). A stationary cursor emits nothing — points
    can never pile up in place. Built once; the per-frame draw bisects
    into it (long_trail_points)."""
    out: list[tuple[float, float, float]] = []
    if not frames:
        return out
    px, py = float(frames[0].x), float(frames[0].y)
    pt = float(frames[0].time_ms)
    since = 0.0                       # distance travelled since last point
    for f in frames[1:]:
        x, y, ft = float(f.x), float(f.y), float(f.time_ms)
        dx, dy, dt = x - px, y - py, ft - pt
        seg = math.hypot(dx, dy)
        if seg > 1e-9:
            d = spacing_osu - since
            while d <= seg:
                u = d / seg
                out.append((px + dx * u, py + dy * u, pt + dt * u))
                d += spacing_osu
            since = seg - (d - spacing_osu)
        px, py, pt = x, y, ft
    return out


def long_trail_points(trail: list[tuple[float, float, float]],
                      trail_times: list[float], t: float, *,
                      window_ms: float = LONG_TRAIL_WINDOW_MS,
                      max_points: int = LONG_TRAIL_MAX_POINTS,
                      ) -> list[tuple[float, float, float]]:
    """[(x_osu, y_osu, strength 0..1)] oldest→newest for the LONG
    connected trail at time t: the build_distance_trail points passed in
    the last window_ms (newest max_points at most), strength = the age
    fade along the ribbon (1 at the cursor → 0 at the window edge).
    Strength is the ONLY per-point alpha factor — the base alpha is
    uniform, so a slow cursor draws a shorter ribbon at the SAME
    brightness, never a brighter blob."""
    hi = bisect.bisect_right(trail_times, t)
    lo = bisect.bisect_left(trail_times, t - window_ms, 0, hi)
    lo = max(lo, hi - max_points)
    out: list[tuple[float, float, float]] = []
    for i in range(lo, hi):
        x, y, ti = trail[i]
        out.append((x, y, 1.0 - (t - ti) / window_ms))
    return out


# --- the scene -----------------------------------------------------------------

class StdScene:
    """Draws one frame of gameplay at time t into a SpriteRenderer's FBO.

    Frame times must be non-decreasing (the record loop guarantees it);
    spawn/retire advance a pointer over the time-sorted object list.
    """

    def __init__(self, beatmap, frames, camera, sprites, bodies, bank, *,
                 combo_colors=DEFAULT_COMBO_COLORS,
                 combo_colors_from_beatmap: bool = False,
                 snaking_in: bool = True,
                 snaking_out: bool = True,
                 slider_merge: bool = False,
                 draw_approach_circles: bool = True,
                 draw_combo_numbers: bool = True,
                 draw_follow_points: bool = True,
                 hit_animations: bool = True,
                 draw_cursor: bool = True,
                 cursor_scale: float = 1.0,
                 cursor_trail_scale: float = 1.0,
                 cursor_rainbow: bool = False,
                 cursor_ripples: bool = False,
                 force_long_trail: bool = False,
                 background: tuple[float, float, float] = (0.043, 0.043, 0.055),
                 judgments=None,
                 draw_judgment_popups: bool = True,
                 draw_hit_lighting: bool = True,
                 spinner_fade_playfield: bool = True,
                 spinner_no_blink: bool = False,
                 hud=None,
                 skin_elems=None,
                 use_skin_cursor: bool = False,
                 border_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 track_override: tuple[float, float, float] | None = None,
                 bg_key: str | None = None,
                 bg_draw_size: tuple[float, float] | None = None,
                 dim_envelope=None,
                 video_bg=None,
                 bg_parallax: bool = False,
                 bg_triangles: bool = False,
                 flash_to_beat: bool = False,
                 show_warning_arrows: bool = True,
                 fade_start_ms: float | None = None,
                 fade_len_ms: float = 0.0,
                 logo_start_ms: float | None = None,
                 seizure_start_ms: float | None = None,
                 bloom_pass=None,
                 bloom_to_beat: bool = True,
                 miss_fall: bool = True,
                 playfield_borders: str = "none",
                 results=None,
                 results_start_ms: float | None = None,
                 results_ssaa=None,
                 mods: int = 0,
                 transform_mod: str = "",
                 transform_start_scale: float = 1.0,
                 transform_strength: float = 1.0,
                 freeze_frame: bool = False,
                 traceable: bool = False,
                 approach_scale: float | None = None,
                 approach_style: str = "",
                 barrel_roll=None,
                 bloom=None,
                 synesthesia: bool = False,
                 blinds: bool = False,
                 no_scope=None,
                 depth=None,
                 bubbles: bool = False,
                 repel_magnet: str = "",
                 repel_magnet_strength: float = 0.5,
                 health=None,
                 fail_time_ms: float | None = None,
                 fail_anim_len_ms: float = FAIL_DURATION_MS,
                 storyboard=None,
                 merge_cursors=None,
                 merge_board=None,
                 merge_misses=None):
        self.merge_cursors = merge_cursors or []   # showdown!mrgd
        self.merge_board = merge_board             # showdown!mrgd board or None
        self.merge_misses = merge_misses or []     # showdown!mrgd [(misses,color)]
        self._merge_trail_cache = {}               # id(frames) -> (pts, times)
        self._merge_smoke_cache = {}               # id(frames) -> [SmokeSeg]
        self.beatmap = beatmap
        self.diff = beatmap.diff
        self.storyboard = storyboard   # render/storyboard_render.StoryboardRenderer | None
        self.frames = frames
        self.cam = camera
        self.spr = sprites
        self.bodies = bodies
        self.bank = bank
        self.combo_colors = [tuple(c) for c in combo_colors]
        self.combo_colors_from_beatmap = combo_colors_from_beatmap
        self.snaking_in = snaking_in
        self.snaking_out = snaking_out
        self.slider_merge = slider_merge
        self.draw_approach_circles = draw_approach_circles
        self.draw_combo_numbers = draw_combo_numbers
        self.draw_follow_points = draw_follow_points
        self._hit_fade = HIT_FADE_OUT if hit_animations else FAST_HIT_FADE_OUT
        self._hit_explode = EXPLODE_SCALE if hit_animations else 1.0
        self.draw_cursor = draw_cursor
        self.cursor_scale = cursor_scale
        self.trail_scale = cursor_trail_scale
        self.cursor_rainbow = cursor_rainbow
        self.background = background

        self.judgments = judgments        # ruleset SimResult | None
        self.draw_judgment_popups = draw_judgment_popups
        self.draw_hit_lighting = draw_hit_lighting
        self.spinner_fade_playfield = spinner_fade_playfield
        self.spinner_no_blink = spinner_no_blink
        self.hud = hud                    # hud.StdHud | None (§5.3: topmost)

        self.skin = skin_elems            # skin_elements.SkinElements | None
        self.use_skin_cursor = bool(use_skin_cursor and skin_elems is not None
                                    and skin_elems.has("cursor"))
        # §3.3 trail mode: cursormiddle present (or forced) → long trail;
        # the long trail is DISTANCE-resampled once from the full replay
        self.long_trail = skin_trail_is_long(
            skin_elems is not None and skin_elems.has("cursormiddle"),
            force_long_trail)
        self._trail_pts: list[tuple[float, float, float]] = []
        self._trail_times: list[float] = []
        if self.long_trail and frames:
            self._trail_pts = build_distance_trail(frames)
            self._trail_times = [p[2] for p in self._trail_pts]
        # Argon league cursor: the thin white ArgonCursorTrail (a tight
        # distance-resampled line, drawn separately from the skin trail).
        self._argon_trail_pts: list[tuple[float, float, float]] = []
        self._argon_trail_times: list[float] = []
        if self.skin is None and frames:
            self._argon_trail_pts = build_distance_trail(
                frames, spacing_osu=ARGON_TRAIL_SPACING_OSU)
            self._argon_trail_times = [p[2] for p in self._argon_trail_pts]
        self.border_color = tuple(border_color)
        self.track_override = (tuple(track_override)
                               if track_override is not None else None)
        self.bg_key = bg_key              # §4.10 background layer
        self.bg_draw_size = bg_draw_size  # cover-fit (w, h), frame-centered
        self.dim = dim_envelope           # background.DimEnvelope | None
        self.miss_fall = miss_fall        # classic falling hit0 (owner: ON)
        self.results = results            # results.ResultsScreen | None
        self.results_start_ms = results_start_ms
        # SSAA: a high-res SpriteRenderer the results card is bound to (its
        # w/h/k are the internal supersample size). None → no supersampling
        # (output already >=1080p, or no results). See ssaa_internal_size /
        # _frame_rgb_ssaa. The card draws into THIS renderer, not self.spr.
        self.results_ssaa = results_ssaa
        # FAIL sequence: freeze gameplay at fail_time_ms, run the
        # FailAnimationContainer fall for fail_anim_len_ms, then results (F).
        self.fail_time_ms = fail_time_ms
        self.fail_anim_len_ms = max(float(fail_anim_len_ms), 1.0)
        # per-object screen-point transform for the slider BODY pass (which
        # bypasses spr.post_xform); set/cleared per object in a fail frame.
        self._fail_body_xform = None
        self._fail_body_alpha = 1.0

        # --- mod visuals: OsuModHidden fades + OsuModFlashlight overlay -----
        self.mods = mods
        self.hidden = bool(mods & MOD_HIDDEN)
        self.flashlight = bool(mods & MOD_FLASHLIGHT)
        # FlashlightSize is combo-driven; precompute the change schedule and
        # a quad big enough to keep the black covering every corner (the
        # cursor can sit at a screen corner, so a full-diagonal half-size).
        self._fl_timeline = (build_flashlight_timeline(judgments.events)
                             if self.flashlight and judgments is not None
                             else [])
        self._fl_diag = math.hypot(float(camera.screen_w),
                                   float(camera.screen_h))

        # --- transform-family "fun" mods (GR/DF/SI/WG/TR): per-object visual
        # entrance animation. Purely visual — the beatmap geometry, the cursor
        # and the judgement/reconcile are untouched. GR/DF/SI drive scale (+ SI
        # rotation) about the object origin and hide the approach circle; WG/TR
        # drive a position OFFSET only (the approach circle moves along). No mod
        # does both, so one affine post-transform serves the whole family. The
        # per-object state (WG's seeded keyframes, TR's accumulated theta) is
        # precomputed below once self.objects exists.
        self.tmod = (transform_mod or "").upper()
        self.t_start_scale = float(transform_start_scale)
        self.t_strength = float(transform_strength)
        self._wiggle_kf: dict[int, list] = {}
        self._tr_offset: dict[int, tuple] = {}
        # per-object transform scratch (set before drawing each object, cleared
        # after the object loop): the approach-ring offset + hide/opaque gates.
        self._t_off: tuple[float, float] = (0.0, 0.0)
        self._t_hide_approach = False
        self._t_force_opaque = False
        self._t_body_xform = None
        # DP: per-object depth scale + depth-repositioned centre (osu!px), read
        # by _head_sprites so the approach ring rides the depth transform too.
        self._dp_scale = 1.0
        self._dp_center_osu: tuple[float, float] | None = None

        # --- approach/circle-appearance mods (FR/AD/TC): purely VISUAL. The
        # beatmap difficulty (diff.preempt) is NEVER mutated, so the judgement/
        # reconcile is untouched; these only change the DRAW.
        #   FR (freeze_frame): every non-spinner object's preempt is EXTENDED so
        #     a whole combo's objects appear together at the combo start and
        #     stay frozen; the approach ring starts larger + shrinks over the
        #     longer window (self._fr_preempt, precomputed once objects exist).
        #   AD (approach_scale/style): the approach ring's custom initial size +
        #     shrink-easing (nothing else changes).
        #   TC (traceable): a hit circle draws ONLY its approach circle (fill +
        #     ring + number hidden); sliders become outline-only. A draw gate.
        # FR/AD/TC are mutually incompatible (and incompatible with the
        # approach-hiding transform mods), so the branches never co-occur.
        self.freeze_frame = bool(freeze_frame)
        self.traceable = bool(traceable)
        self.ad_scale = (float(approach_scale)
                         if approach_scale is not None else None)
        self.ad_style = (approach_style or "").strip().lower()
        self._fr_preempt: dict[int, float] = {}

        # --- screen / cursor-effect visual mods (BR/BM/SY/BL/NS/DP/BU) ------
        # All purely VISUAL (the beatmap geometry, the replay cursor and the
        # judgement/reconcile are untouched — the ruleset already simulated the
        # real positions before the scene draws). See render/screen_mods.py.
        self.barrel = barrel_roll         # lazer_mods.BarrelRoll | None (BR)
        self.bloom_mod = bloom            # lazer_mods.Bloom | None (BM)
        self.synesthesia = bool(synesthesia)               # (SY)
        self.blinds = bool(blinds)                          # (BL)
        self.no_scope = no_scope          # lazer_mods.NoScope | None (NS)
        self.depth_mod = depth            # lazer_mods.Depth | None (DP)
        self.bubbles = bool(bubbles)                        # (BU)
        self.health = health              # ruleset.HealthTimeline | None (BL)
        # BR: the global playfield spin+scale is installed as spr.post_xform /
        # body xform per frame; these hold the current affines so the per-object
        # transform mods (GR/DF/... , DP) can COMPOSE with the spin.
        self._barrel_sprite = None
        self._barrel_point = None
        if self.barrel is not None:
            self._br_scale = _sm.barrel_playfield_scale(
                camera.screen_w, camera.screen_h)
            self._br_center = (camera.screen_w / 2.0, camera.screen_h / 2.0)
        # DP hides follow points (lazer: "won't make any sense"); its approach
        # rings obey ShowApproachCircles.
        if self.depth_mod is not None:
            self.draw_follow_points = False

        # --- cursor-driven object movement: Magnetised (MG) / Repel (RP) ----
        # A STATEFUL per-object easeTo (render/repel_magnet.py): each frame every
        # alive object's drawn position eases toward (MG) / away from (RP) the
        # recorded cursor. Purely visual — the beatmap geometry, the replay
        # cursor and the judgement/reconcile are UNTOUCHED (the object's own
        # drawable rides the eased offset via spr.post_xform + the body xform +
        # the _t_off approach ring; the judgement popup stays at the original
        # position, exactly as lazer leaves it). Both MG and RP hide follow
        # points ("won't make any sense" once objects move). The per-object
        # state (current eased osu!px position) + the slider movement bounds are
        # precomputed below once self.objects exists.
        self.rm_mod = (repel_magnet or "").upper()
        if self.rm_mod not in (MAGNETISED, REPEL):
            self.rm_mod = ""
        self.rm_strength = float(repel_magnet_strength)
        if self.rm_mod:
            self.draw_follow_points = False   # OsuModMagnetised/Repel hide them
        # id(obj) -> current eased head position (osu!px); _rm_last_t is the map
        # time already integrated (None = cold); _rm_bounds is the per-slider
        # movement-clamp box (RP only).
        self._rm_pos: dict[int, tuple[float, float]] = {}
        self._rm_last_t: float | None = None
        self._rm_bounds: dict[int, tuple[float, float, float, float]] = {}
        self._rm_frame_times: list[float] = []

        # --- settings-surface phase (§4.10/§4.6/§4.8 additions) ------------
        self.video = video_bg             # video_bg.VideoBackground | None
        self.bg_parallax = bg_parallax
        self.flash_to_beat = flash_to_beat
        self.fade_start_ms = fade_start_ms
        self.fade_len_ms = fade_len_ms
        self.logo_start_ms = logo_start_ms
        self.seizure_start_ms = seizure_start_ms
        self.bloom = bloom_pass           # bloom.BloomPass | None
        self.bloom_to_beat = bloom_to_beat
        self._tri_field = (triangle_field(seed=len(beatmap.hit_objects))
                           if bg_triangles else None)
        starts_all = [o.get_start_time() for o in beatmap.hit_objects]
        self.first_spawn_ms = (min(starts_all) - beatmap.diff.preempt
                               if starts_all else 0.0)
        self._warn_anchors: list[float] = []
        if show_warning_arrows and beatmap.pauses:
            self._warn_anchors = break_resume_anchors(
                beatmap.pauses, starts_all, beatmap.diff.preempt)
        self._ripple_evs: list[tuple[float, float, float]] = []
        self._ripple_times: list[float] = []
        if cursor_ripples and frames:
            self._ripple_evs = ripple_events(frames)
            self._ripple_times = [e[0] for e in self._ripple_evs]
        # replay Smoke (key bit 16): lazer SmokeContainer/SmokeSegment.
        # Self-gates on the bit: no smoke bit -> zero segments -> the
        # draw in _render_frame never fires (byte-identical render).
        if skin_elems is not None and skin_elems.has("cursor-smoke"):
            self._smoke_tex = skin_elems.key("cursor-smoke")
            self._smoke_width_osu = skin_elems.size["cursor-smoke"][0] * 0.165
        else:
            self._smoke_tex = "smoke"
            self._smoke_width_osu = SMOKE_DEFAULT_WIDTH_OSU
        self._smoke_segs = (smoke_segments(frames,
                                           self._smoke_width_osu * 7.0 / 8.0)
                            if frames else [])
        # playfield borders: precomputed subtle white rects (screen px)
        self._border_rects: list[tuple[float, float, float, float]] = []
        if playfield_borders in ("edges", "full"):
            bx0, by0 = camera.to_screen(0.0, 0.0)
            bx1, by1 = camera.to_screen(512.0, 384.0)
            self._border_rects = playfield_border_rects(
                bx0, by0, bx1, by1, playfield_borders,
                max(camera.len_to_screen(BORDER_THICKNESS_OSU), 1.0),
                camera.len_to_screen(BORDER_CORNER_LEN_OSU))

        self.objects = sorted(beatmap.hit_objects,
                              key=lambda o: o.get_start_time())
        if self.tmod == WIGGLE:
            # OsuModWiggle: per object a seeded MoveTo keyframe chain across
            # TimePreempt (and, for sliders/spinners, their Duration too).
            for o in self.objects:
                st = o.get_start_time()
                dur = (o.get_end_time() - st
                       if isinstance(o, (Slider, Spinner)) else 0.0)
                self._wiggle_kf[id(o)] = wiggle_events(
                    st, self.diff.preempt, dur, self.t_strength)
        elif self.tmod == TRANSFORM:
            # OsuModTransform: theta accumulates +TimeFadeIn/1000 per object in
            # spawn order (spinners advance theta but aren't visually moved —
            # they draw on their own path). appearDistance/appear timing are
            # constant for a constant AR; the per-object appearOffset rotates.
            theta = 0.0
            preempt, fade_in = self.diff.preempt, self.diff.time_fade_in
            for o in self.objects:
                ox0, oy0 = transform_appear_offset(theta, preempt, fade_in)
                st = o.get_start_time()
                if not isinstance(o, Spinner):
                    self._tr_offset[id(o)] = (st - preempt - 1.0,
                                              preempt + 1.0, ox0, oy0)
                theta += fade_in / 1000.0
        if self.freeze_frame:
            # OsuModFreezeFrame.ApplyToBeatmap: extend every non-spinner
            # object's preempt by StartTime-lastNewComboTime (objects sorted by
            # start time; new_combo marks each combo's first object).
            self._fr_preempt = freeze_preempts(
                self.objects, self.diff.preempt,
                lambda o: isinstance(o, Spinner))
        self.radius_px = camera.len_to_screen(self.diff.circle_radius)
        self.circle_k = circle_pixel_scale(self.radius_px)

        # --- MG/RP precompute (needs objects + circle radius) ---------------
        # RP clamps each slider's fleeing destination so the whole slider stays
        # on the playfield (CalculatePossibleMovementBounds); the box is static
        # per slider. Also cache the replay frame times for the cold-start /
        # keyframe seek (integrating from an object's spawn over the frames).
        if self.rm_mod:
            r_osu = self.diff.circle_radius
            for o in self.objects:
                if not isinstance(o, Slider):
                    continue
                hx, hy = o.get_stacked_start_position(self.diff)
                rel = [(mx - hx, my - hy) for mx, my in
                       (o.modify_position(p, self.diff)
                        for p in o.multi_curve.path)]
                self._rm_bounds[id(o)] = _rm.slider_movement_bounds(rel, r_osu)
            self._rm_frame_times = [f.time_ms for f in frames]

        # --- screen-mod precompute (needs objects + radius + judgments) ------
        # SY: each object's combo colour comes from its beat-snap divisor at
        # StartTime (BindableBeatDivisor.GetColourFor). Precompute id(obj)->rgb.
        self._sy_colour: dict[int, tuple[float, float, float]] = {}
        if self.synesthesia:
            for o in self.objects:
                st = o.get_start_time()
                tp = beatmap.timings.get_original_point_at(st)
                div = _sm.closest_beat_divisor(st, tp.time, tp.beat_length_base)
                self._sy_colour[id(o)] = _sm.snap_colour(div)
        # break intervals (BM resets cursor size / NS forces cursor visible
        # during breaks; BL widens the blinds) — from the beatmap's pause spans.
        self._breaks = [(p.start_time, p.end_time)
                        for p in (getattr(beatmap, "pauses", []) or [])]
        # BM: combo-driven cursor-scale timeline (100 ms settle, =1 in breaks).
        self._bm_timeline: list[tuple[float, float, float]] = []
        if self.bloom_mod is not None and judgments is not None:
            bm = self.bloom_mod
            self._bm_timeline = _sm.build_combo_timeline(
                judgments.events,
                lambda c: _sm.bloom_cursor_size(c, bm.max_size_combo_count,
                                                bm.max_cursor_size))
        # NS: combo-driven cursor-alpha timeline (=1 in breaks + during
        # spinners: OsuModNoScope's spinnerPeriods [start-100, end]).
        self._ns_timeline: list[tuple[float, float, float]] = []
        self._ns_spinner: list[tuple[float, float]] = []
        if self.no_scope is not None and judgments is not None:
            hcc = self.no_scope.hidden_combo_count
            self._ns_timeline = _sm.build_combo_timeline(
                judgments.events, lambda c: _sm.no_scope_alpha(c, hcc))
            self._ns_spinner = [
                (o.get_start_time() - _sm.NO_SCOPE_TRANSITION_MS,
                 o.get_end_time())
                for o in self.objects if isinstance(o, Spinner)]
        # BL: the two black boxes' playfield span (screen px) + the
        # targetBreakMultiplier schedule.
        if self.blinds:
            self._bl_start = camera.to_screen(0.0, 192.0)[0]
            self._bl_end = camera.to_screen(512.0, 192.0)[0]
            fs = min(starts_all) if starts_all else 0.0
            self._bl_break = _sm.build_blinds_break_timeline(
                fs, self.diff.preempt, self._breaks)
        # BU: one expanding bubble per hit object (circle / slider head /
        # spinner), spawned at its head position tinted by its combo colour.
        self._bu_events: list[tuple] = []
        self._bu_times: list[float] = []
        self._bu_duration = 0.0
        self._bu_size_px = 0.0
        if self.bubbles and judgments is not None:
            self._bu_duration = _sm.bubble_duration(
                _sm.bubble_fade_time(self.diff.preempt))
            self._bu_size_px = camera.len_to_screen(
                _sm.bubble_initial_size_osu(self.diff.circle_radius))
            # JudgmentEvent.object_id is the sim's start-order index; both the
            # ruleset and self.objects sort by start time, so it indexes here.
            for ev in sorted(judgments.events, key=lambda e: e.time_ms):
                if not (0 <= ev.object_id < len(self.objects)):
                    continue
                o = self.objects[ev.object_id]
                px, py = o.get_stacked_start_position(self.diff)
                sx, sy = camera.to_screen(px, py)
                was_hit = str(ev.kind).lower().find("miss") < 0
                # BubbleDrawable.PrepareForUse: a hit bubble is the accent
                # colour darkened 0.1 (Colour White * colourBox.Darken); a miss
                # is Colour4.Black (barely visible), matching lazer.
                col = (tuple(c * 0.9 for c in self._color(o)) if was_hit
                       else (0.0, 0.0, 0.0))
                self._bu_events.append(
                    (ev.time_ms, sx, sy, col, was_hit,
                     _sm.bubble_max_size(ev.combo_after)))
            self._bu_events.sort(key=lambda e: e[0])
            self._bu_times = [e[0] for e in self._bu_events]
        self._spawn_idx = 0
        self._active: list = []
        self._last_t = -math.inf
        self._slider_paths: dict[int, list[tuple[float, float]]] = {}
        self._slider_marks: dict[int, tuple[list, list]] = {}
        # spinners: rotation tracks (replay-driven, or the §2.5 auto-spin
        # when there are no cursor frames), built at spawn
        self._spinner_tracks: dict[int, SpinnerTrack] = {}
        # spinner bonus popups (Argon league only): (time, value, is_max) per
        # completed bonus spin, built with the track at spawn.
        self._spinner_bonus: dict[int, list[tuple[float, int, bool]]] = {}
        self.spinner_style = detect_spinner_style(
            skin_elems.loaded if skin_elems is not None else set())
        self.spin_k = camera.len_to_screen(SPRITE_SCALE)  # px per logical px
        # judgment popups: time-sorted events, pointer + active window
        self._popups = (sorted(judgments.events, key=lambda e: e.time_ms)
                        if judgments is not None else [])
        self._popup_idx = 0
        self._popup_active: list = []
        # hit lighting: (time, x, y) per non-miss judgment + combo tint
        self._lightings: list = [e for e in self._popups
                                 if wants_lighting(e.kind)]
        self._lighting_idx = 0
        self._lighting_active: list = []
        # follow points: precomputed dot schedule, pointer + active window
        self._fp_dots: list = []
        if self.draw_follow_points:
            for prev, nxt in zip(self.objects, self.objects[1:]):
                if not followpoint_eligible(prev, nxt):
                    continue
                self._fp_dots.extend(followpoint_dots(
                    prev.get_stacked_end_position(self.diff),
                    prev.get_end_time(),
                    nxt.get_stacked_start_position(self.diff),
                    nxt.get_start_time(),
                    self.diff.preempt_u))   # lazer start.TimePreempt
            self._fp_dots.sort(key=lambda d: d.fade_in)
        self._fp_idx = 0
        self._fp_active: list = []

    # --- transform-family mod visuals (GR/DF/SI/WG/TR) --------------------------

    def _obj_transform(self, obj, t: float) -> ObjTransform:
        """The per-object visual modifier at time t for the active transform
        mod. Spinners (and non-transform frames) return IDENTITY."""
        tmod = self.tmod
        if not tmod or isinstance(obj, Spinner):
            return IDENTITY
        preempt = self.diff.preempt
        spawn = obj.get_start_time() - preempt
        is_slider = isinstance(obj, Slider)
        if tmod == GROW or tmod == DEFLATE:
            s = grow_deflate_scale(t, spawn, preempt, self.t_start_scale)
            return ObjTransform(scale_x=s, scale_y=s)
        if tmod == SPIN_IN:
            if is_slider:
                s = spin_in_slider_scale(t, spawn, preempt)
                return ObjTransform(scale_x=s, scale_y=s, force_opaque=True)
            sx, sy, rot = spin_in_circle(t, spawn, preempt)
            return ObjTransform(scale_x=sx, scale_y=sy, rotation=rot,
                                force_opaque=True)
        if tmod == WIGGLE:
            ev = self._wiggle_kf.get(id(obj))
            ox, oy = wiggle_offset_at(ev, t) if ev else (0.0, 0.0)
            return ObjTransform(off_x=ox, off_y=oy)
        if tmod == TRANSFORM:
            d = self._tr_offset.get(id(obj))
            if d is None:
                return IDENTITY
            appear_time, move_dur, ox0, oy0 = d
            ox, oy = transform_offset_at(t, appear_time, move_dur, ox0, oy0)
            return ObjTransform(off_x=ox, off_y=oy)
        return IDENTITY

    @staticmethod
    def _transform_sprite_xform(pivot, sx, sy, rot, ox_scr, oy_scr):
        """A Sprite->Sprite affine: scale (sx, sy) + rotate (rot) about `pivot`
        (screen px) then translate by (ox_scr, oy_scr). Uniform scale commutes
        with a sprite's own rotation, so GR/DF/TR/WG are exact for every sprite;
        SI's non-uniform scale only ever hits rotation-0 circle/number sprites
        (SI sliders scale uniformly), so it is exact too."""
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        px, py = pivot

        def xf(sp):
            dx = (sp.x - px) * sx
            dy = (sp.y - py) * sy
            rx = dx * cos_r - dy * sin_r
            ry = dx * sin_r + dy * cos_r
            return replace(sp, x=px + rx + ox_scr, y=py + ry + oy_scr,
                           w=sp.w * sx, h=sp.h * sy, rotation=sp.rotation + rot)
        return xf

    @staticmethod
    def _transform_point_xform(pivot, sx, sy, rot, ox_scr, oy_scr):
        """The same affine on a bare screen point (for the slider body pass,
        which bypasses spr.post_xform)."""
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        px, py = pivot

        def xf(p):
            dx = (p[0] - px) * sx
            dy = (p[1] - py) * sy
            rx = dx * cos_r - dy * sin_r
            ry = dx * sin_r + dy * cos_r
            return (px + rx + ox_scr, py + ry + oy_scr)
        return xf

    def _compose_barrel(self, sprite_xf, point_xf):
        """Compose a per-object affine with the active BR playfield spin (which
        rotates the whole layer about the screen centre). The object-local
        transform runs first (screen-space), then the global barrel."""
        bs, bp = self._barrel_sprite, self._barrel_point
        if bs is None:
            self.spr.post_xform = sprite_xf
            self._t_body_xform = point_xf
            return
        if sprite_xf is None:
            self.spr.post_xform = bs
            self._t_body_xform = bp
            return
        self.spr.post_xform = lambda sp: bs(sprite_xf(sp))
        self._t_body_xform = lambda p: bp(point_xf(p))

    def _apply_obj_transform(self, obj, t: float) -> None:
        """Install the per-object transform for the sprites drawn next: the
        approach-ring offset + hide/opaque gates (read by _head_sprites) plus
        the spr.post_xform / body-xform affine (transform-family scale/rotation
        or WG/TR offset, or the DP depth scale+reposition), composed with the
        BR playfield spin when active."""
        self._dp_scale = 1.0
        self._dp_center_osu = None
        if self.depth_mod is not None:
            self._apply_depth_transform(obj, t)
            return
        if self.rm_mod:
            self._apply_rm_transform(obj)
            return
        ot = self._obj_transform(obj, t)
        self._t_hide_approach = self.tmod in HIDES_APPROACH
        self._t_force_opaque = ot.force_opaque
        self._t_off = (ot.off_x, ot.off_y)
        if isinstance(obj, Spinner):
            self._compose_barrel(None, None)
            return
        pivot = self.cam.to_screen(*obj.get_stacked_start_position(self.diff))
        scl = self.cam.scl
        ox_scr, oy_scr = ot.off_x * scl, ot.off_y * scl
        self._compose_barrel(
            self._transform_sprite_xform(
                pivot, ot.scale_x, ot.scale_y, ot.rotation, ox_scr, oy_scr),
            self._transform_point_xform(
                pivot, ot.scale_x, ot.scale_y, ot.rotation, ox_scr, oy_scr))

    def _apply_depth_transform(self, obj, t: float) -> None:
        """OsuModDepth: scale the object by its depth and reposition it toward
        the playfield-centre vanishing point. Uniform scale about the real head
        pivot + a screen offset (depthPos-realPos) is algebraically the depth
        scale about the vanishing point, so one affine serves circle body,
        slider body/ball and the approach ring."""
        self._t_off = (0.0, 0.0)
        self._t_force_opaque = False
        # ShowApproachCircles gate (approach ring hidden when off).
        self._t_hide_approach = not self.depth_mod.show_approach_circles
        if isinstance(obj, Spinner):
            self._compose_barrel(None, None)
            return
        start = obj.get_start_time()
        preempt = self._preempt_for(obj)
        max_depth = self.depth_mod.max_depth
        if isinstance(obj, Slider):
            z = _sm.depth_z_slider(t, start, obj.get_end_time() - start,
                                   preempt, max_depth)
        else:
            z = _sm.depth_z_circle(t, start, preempt, max_depth)
        scale = _sm.depth_scale_for(z)
        real = obj.get_stacked_start_position(self.diff)
        dcx, dcy = _sm.depth_position(real[0], real[1], scale)
        self._dp_scale = scale
        self._dp_center_osu = (dcx, dcy)
        pivot = self.cam.to_screen(*real)
        dsx, dsy = self.cam.to_screen(dcx, dcy)
        ox_scr, oy_scr = dsx - pivot[0], dsy - pivot[1]
        self._compose_barrel(
            self._transform_sprite_xform(pivot, scale, scale, 0.0,
                                         ox_scr, oy_scr),
            self._transform_point_xform(pivot, scale, scale, 0.0,
                                        ox_scr, oy_scr))

    def _clear_obj_transform(self) -> None:
        # restore the BR playfield spin base (or None) so the deferred approach
        # pass + cursor still ride the spin.
        self.spr.post_xform = self._barrel_sprite
        self._t_body_xform = self._barrel_point
        self._t_off = (0.0, 0.0)
        self._t_hide_approach = False
        self._t_force_opaque = False
        self._dp_scale = 1.0
        self._dp_center_osu = None

    # --- Magnetised / Repel: stateful per-frame easeTo -------------------------

    def _rm_ball_offset(self, obj, sample_t: float) -> tuple[float, float]:
        """The slider ball's offset from the head at ``sample_t`` (osu!px), or
        (0, 0) until the head has a result. Once the head is judged
        (OsuModMagnetised/Repel: ``slider.HeadCircle.Result.HasResult``) the
        destination targets the BALL onto the cursor instead of the head, so the
        head eases to ``destination - Ball.DrawPosition``; this offset is that
        ``Ball.DrawPosition`` (ball position relative to the real head)."""
        if not isinstance(obj, Slider):
            return (0.0, 0.0)
        v = self._verdict(obj)
        if v is None:
            return (0.0, 0.0)
        resolved = ((v.hit_time is not None and sample_t >= v.hit_time)
                    or (v.hit_time is None and sample_t >= v.deadline))
        if not resolved:
            return (0.0, 0.0)
        hx, hy = obj.get_stacked_start_position(self.diff)
        bx, by = obj.get_stacked_position_at(sample_t, self.diff)
        return (bx - hx, by - hy)

    def _rm_step_one(self, obj, pos: tuple[float, float], sample_t: float,
                     elapsed: float) -> tuple[float, float]:
        """One OsuModMagnetised/Repel ``easeTo`` step for a single object:
        DampContinuously ``pos`` (osu!px) toward its per-mod destination using
        the cursor at ``sample_t`` and this step's ``elapsed`` ms."""
        px, py = pos
        cx, cy, _ = (cursor_at(self.frames, sample_t) if self.frames
                     else (256.0, 192.0, 0))
        boffx, boffy = self._rm_ball_offset(obj, sample_t)
        if self.rm_mod == MAGNETISED:
            dest_x, dest_y = _rm.magnet_destination(cx, cy, boffx, boffy)
            damp = _rm.magnet_damp_length(self.rm_strength)
        else:                                          # REPEL
            dx, dy = _rm.repel_destination(px, py, cx, cy)
            if isinstance(obj, Slider):
                b = self._rm_bounds.get(id(obj))
                if b is not None:
                    dx, dy = _rm.clamp_to_slider_bounds(dx, dy, b)
            dest_x, dest_y = dx - boffx, dy - boffy
            damp = _rm.repel_damp_length(px, py, cx, cy, self.rm_strength)
        return _rm.ease_step(px, py, dest_x, dest_y, damp, elapsed)

    def _rm_seek(self, t: float) -> None:
        """Cold-start / keyframe seek: integrate every alive object from its
        spawn to ``t`` over the replay frames (the natural per-input timestep),
        so a single-shot frame_rgb(t) shows the correct eased positions without
        a prior monotonic pass. O(frames-in-preempt) per alive object."""
        self._rm_pos = {}
        ft = self._rm_frame_times
        frames = self.frames
        n = len(frames)
        for obj in self._active:
            if isinstance(obj, Spinner):
                continue
            pos = obj.get_stacked_start_position(self.diff)
            spawn = obj.get_start_time() - self._preempt_for(obj)
            prev = spawn
            i = bisect.bisect_right(ft, spawn) if ft else n
            while i < n and frames[i].time_ms < t:
                st = frames[i].time_ms
                pos = self._rm_step_one(obj, pos, st, st - prev)
                prev = st
                i += 1
            pos = self._rm_step_one(obj, pos, t, t - prev)   # partial step to t
            self._rm_pos[id(obj)] = pos

    def _rm_advance(self, t: float) -> None:
        """Integrate the per-object easeTo one render step (the MG/RP analogue
        of the ruleset's per-frame Update). During the monotonic render pass the
        step is small and integrated incrementally; a cold start or a
        discontinuous jump (--dump-frames keyframes) reseeds from each object's
        spawn over the replay frames."""
        last = self._rm_last_t
        if last is None or t < last or (t - last) > _RM_MAX_STEP_MS:
            self._rm_seek(t)
        else:
            elapsed = t - last
            live = {id(o) for o in self._active
                    if not isinstance(o, Spinner)}
            if len(self._rm_pos) != len(live):
                self._rm_pos = {k: v for k, v in self._rm_pos.items()
                                if k in live}
            for obj in self._active:
                if isinstance(obj, Spinner):
                    continue
                oid = id(obj)
                pos = self._rm_pos.get(oid)
                if pos is None:            # newly alive → starts at real position
                    pos = obj.get_stacked_start_position(self.diff)
                self._rm_pos[oid] = self._rm_step_one(obj, pos, t, elapsed)
        self._rm_last_t = t

    def _apply_rm_transform(self, obj) -> None:
        """Install the MG/RP per-object offset for the sprites drawn next: a
        pure translation (eased position − real head) applied as spr.post_xform
        (head/number/ball/flash), the body point-xform (slider body) and _t_off
        (the deferred approach ring), composed with any BR spin. MG/RP never
        hide the approach ring and never force-opaque, so those gates stay off."""
        self._t_hide_approach = False
        self._t_force_opaque = False
        if isinstance(obj, Spinner):
            self._t_off = (0.0, 0.0)
            self._compose_barrel(None, None)
            return
        real = obj.get_stacked_start_position(self.diff)
        px, py = self._rm_pos.get(id(obj), real)
        ox, oy = px - real[0], py - real[1]
        self._t_off = (ox, oy)
        pivot = self.cam.to_screen(*real)
        scl = self.cam.scl
        ox_scr, oy_scr = ox * scl, oy * scl
        self._compose_barrel(
            self._transform_sprite_xform(pivot, 1.0, 1.0, 0.0, ox_scr, oy_scr),
            self._transform_point_xform(pivot, 1.0, 1.0, 0.0, ox_scr, oy_scr))

    # --- Freeze Frame per-object preempt --------------------------------------

    def _preempt_for(self, obj) -> float:
        """The object's effective TimePreempt: the extended Freeze Frame value
        (so its whole combo appears together) when FR is active and the object
        is non-spinner, else the beatmap's ``diff.preempt``. TimeFadeIn is
        unchanged either way (FR leaves TimeFadeIn alone)."""
        if self.freeze_frame:
            p = self._fr_preempt.get(id(obj))
            if p is not None:
                return p
        return self.diff.preempt

    def _approach_geometry(self, obj, t: float, start: float, preempt: float,
                           fade_in: float) -> "tuple[float, float] | None":
        """(scale, alpha) for a hit-circle/slider-head approach ring, or None
        outside its life. Alpha is the normal fade-in (capped 0.9); the SCALE
        curve is FR's (bigger start, linear over the extended preempt), AD's
        (custom initial size + per-style easing) or the §2.5 default 4→1."""
        if t >= start or t < start - preempt:
            return None
        alpha = min(fade_in_alpha(t, start, preempt, fade_in),
                    APPROACH_MAX_ALPHA)
        if self.freeze_frame:
            s = freeze_frame_scale(t, start, preempt, self.diff.preempt)
        elif self.ad_scale is not None:
            s = approach_different_scale(t, start, preempt,
                                         self.ad_scale, self.ad_style)
        else:
            w = _clamp01((t - (start - preempt)) / preempt)
            s = APPROACH_START_SCALE - (APPROACH_START_SCALE - 1.0) * w
        if s is None:
            return None
        return s, alpha

    # --- lifecycle management ---------------------------------------------------

    def _advance(self, t: float) -> None:
        if t < self._last_t:
            raise ValueError(f"scene time went backwards: {t} < {self._last_t}")
        self._last_t = t
        # spawn when the object first appears. Normally start-preempt; under
        # Freeze Frame the extended preempt makes a combo's objects appear
        # together at the combo start. Appear times stay non-decreasing in
        # object order (a combo's members share the combo-start appear time),
        # so the single forward pointer remains valid.
        while (self._spawn_idx < len(self.objects)
               and (self.objects[self._spawn_idx].get_start_time()
                    - self._preempt_for(self.objects[self._spawn_idx])) <= t):
            obj = self.objects[self._spawn_idx]
            self._spawn_idx += 1
            self._active.append(obj)
            if isinstance(obj, Slider):
                # presented path: HR flip + stack offset per vertex, → screen px
                self._slider_paths[id(obj)] = [
                    self.cam.to_screen(*obj.modify_position(p, self.diff))
                    for p in obj.multi_curve.path]
                self._slider_marks[id(obj)] = self._build_marks(obj)
            elif isinstance(obj, Spinner):
                track = self._build_spinner_track(obj)
                self._spinner_tracks[id(obj)] = track
                if self.skin is None:      # Argon league bonus popups only
                    self._spinner_bonus[id(obj)] = self._build_spinner_bonus(
                        obj, track)
        keep = []
        for obj in self._active:
            if t <= obj.get_end_time() + HIT_FADE_OUT:
                keep.append(obj)
            else:
                self._slider_paths.pop(id(obj), None)
                self._slider_marks.pop(id(obj), None)
                self._spinner_tracks.pop(id(obj), None)
                self._spinner_bonus.pop(id(obj), None)
        self._active = keep

    def _build_spinner_track(self, obj) -> SpinnerTrack:
        start, end = obj.get_start_time(), obj.get_end_time()
        spins = required_rotations(
            self.diff.spinner_ratio, end - start,
            lazer=bool(getattr(self.judgments, "lazer", False)),
            lz_min_rps=self.diff.lz_spinner_min_rps)
        if self.frames:
            return SpinnerTrack.from_frames(self.frames, start, end, spins)
        return SpinnerTrack.auto(start, end, spins)   # --no-replay perfect play

    def _build_spinner_bonus(self, obj,
                             track: SpinnerTrack
                             ) -> list[tuple[float, int, bool]]:
        """Bonus popup events for one spinner (ArgonSpinner.bonusCounter): the
        lazer spin thresholds (difficulty.py lz_spinner_min/max_rps) → each
        completed bonus spin's crossing time + cumulative value."""
        req_for_bonus, max_bonus = spinner_bonus_thresholds(
            self.diff.lz_spinner_min_rps, self.diff.lz_spinner_max_rps,
            obj.get_end_time() - obj.get_start_time())
        return bonus_spin_events(track, req_for_bonus, max_bonus)

    def _build_marks(self, obj) -> tuple[list, list]:
        """(ticks, arrows) draw records for one spawning slider — screen
        positions + per-part HIT flags from the ruleset (no judgments →
        the Phase-1 perfect-play fallback: everything hit).

        ticks:  (TickMark, screen_x, screen_y, span_start, hit)
        arrows: (ReverseArrow, screen_x, screen_y, rotation, hit)
        """
        v = self._verdict(obj)
        outcomes: dict[tuple[str, float], bool] = {}
        if v is not None:
            for p in v.parts:
                if p.kind in ("tick", "repeat"):
                    outcomes[(p.kind, round(p.time, 2))] = p.hit
        pts = self._slider_paths[id(obj)]
        ticks = []
        for tm in tick_schedule(obj):
            sx, sy = self.cam.to_screen(*obj.modify_position(tm.pos, self.diff))
            ticks.append((tm, sx, sy,
                          obj.start_time + tm.span * obj.part_len,
                          outcomes.get(("tick", round(tm.time, 2)), True)))
        arrows = []
        spawn = obj.get_start_time() - self._preempt_for(obj)
        # Hidden does not alter reverse-arrow lifetime: a pending reverse
        # arrow is visible BEFORE its repeat, regardless of the head hit.
        schedule = reverse_arrow_schedule(obj.get_start_time(), obj.part_len,
                                          obj.repeat_count, spawn)
        markers = obj.tick_reverse
        for arrow in schedule:
            marker = markers[arrow.r - 1] if arrow.r - 1 < len(markers) else None
            if marker is not None:
                sx, sy = self.cam.to_screen(
                    *obj.modify_position(marker.pos, self.diff))
                hit = outcomes.get(("repeat", round(marker.time, 2)), True)
            else:   # tick walk suppressed (degenerate) — fall back to path end
                sx, sy = pts[-1] if arrow.at_tail else pts[0]
                hit = True
            arrows.append((arrow, sx, sy,
                           arrow_rotation(pts, arrow.at_tail), hit))
        return ticks, arrows

    # --- frame draw ---------------------------------------------------------------

    def render_frame(self, t: float, skip_results: bool = False) -> None:
        # skip_results: draw the scene-behind WITHOUT the results card. The
        # SSAA outro path (_frame_rgb_ssaa) renders this at output res, then
        # composites the card on top at the supersample resolution.
        with perf.T("frame_render"):
            self._render_frame(t, skip_results)

    def _render_frame(self, t: float, skip_results: bool = False) -> None:
        if self.fail_time_ms is not None and t >= self.fail_time_ms:
            self._render_fail_frame(t, skip_results=skip_results)
            return
        self._advance(t)
        if self.rm_mod:
            self._rm_advance(t)      # MG/RP: integrate the per-object easeTo
        self.spr.begin(clear=self.background)
        brightness = self._draw_background(t)
        if self._tri_field is not None:
            tri = self._triangle_sprites(t, brightness)
            if tri:
                self.spr.draw(tri)    # deco under the playfield
        if self._border_rects:
            self.spr.draw([Sprite(cx, cy, w, h, None,
                                  (1.0, 1.0, 1.0, BORDER_ALPHA))
                           for cx, cy, w, h in self._border_rects])
        # storyboard underlay (Background/Fail/Pass/Foreground): under the
        # playfield, above the map background (Player.createUnderlayComponents).
        if self.storyboard is not None:
            self.storyboard.draw_underlay(t, self._sb_dim(t))
        # BR: install the global playfield spin (+ scale) as the base
        # post_xform / body xform so objects, deferred approach rings, popups
        # and the cursor all ride it. Background/borders above stay fixed.
        self._install_barrel(t)
        if self._smoke_segs:
            smoke = self._smoke_sprites(t)
            if smoke:
                self.spr.draw(smoke)
        per_obj = (bool(self.tmod) or self.depth_mod is not None
                   or bool(self.rm_mod))
        if self.draw_follow_points and self._fp_dots:
            fps = self._followpoint_sprites(t)
            if fps:
                self.spr.draw(fps)    # §5.3: follow points under all objects
        if self.slider_merge:
            self._draw_merged_bodies(t)   # §4.9 SliderMerge: one union pass
        approach: list[Sprite] = []
        # §5.3: newest objects draw FIRST → end up under older ones
        for obj in reversed(self._active):
            if per_obj:
                self._apply_obj_transform(obj, t)
            if isinstance(obj, Spinner):
                self._draw_spinner(obj, t)
            elif isinstance(obj, Slider):
                self._draw_slider(obj, t, approach)
            else:
                self._draw_circle(obj, t, approach)
        if per_obj:
            self._clear_obj_transform()
        if approach:
            self.spr.draw(approach)
        if self.draw_hit_lighting and self._lightings:
            lightings = self._lighting_sprites(t)
            if lightings:
                self.spr.draw(lightings)   # §3.3: UNDER the judgment popups
        if self.draw_judgment_popups and self._popups:
            popups = self._popup_sprites(t)
            if popups:
                self.spr.draw(popups)
        if self._warn_anchors:
            arrows = self._warning_arrow_sprites(t)
            if arrows:
                self.spr.draw(arrows)      # over the playfield, under cursor
        if self._ripple_evs:
            ripples = self._ripple_sprites(t)
            if ripples:
                self.spr.draw(ripples)     # §4.8 ripples UNDER the cursor
        if self.bubbles:
            # BU: expanding hit-position bubbles over the objects (they obscure
            # judgements in lazer), under the cursor + HUD.
            bub = self._bubble_sprites(t)
            if bub:
                self.spr.draw(bub)
        # BR: the playfield spin ends here — the bloom post-pass, blinds,
        # flashlight, fade + HUD are all screen-space (must NOT rotate).
        self._uninstall_barrel()
        if self.bloom is not None:
            # §4.10 bloom: gameplay layer only — the HUD stays crisp above
            strength = beat_strength(beat_phase(t, self.beatmap.timings),
                                     self.bloom_to_beat)
            self.bloom.apply(self.spr.color_tex, self.spr.fbo, strength)
        if self.blinds:
            # BL: two health-driven black panels close in from the screen
            # edges, over the gameplay + under the HUD (lazer Overlays order).
            self.spr.draw(self._blinds_sprites(t))
        if self.flashlight and self.frames:
            # OsuModFlashlight: dark overlay + cursor-following cutout, OVER
            # the gameplay layer but UNDER the HUD (lazer draw order)
            self.spr.draw([self._flashlight_sprite(t)])
        if self.storyboard is not None:
            # Overlay storyboard layer: over the gameplay, under the HUD
            # (Player proxies it into createOverlayComponents).
            self.storyboard.draw_overlay(t, self._sb_dim(t))
        # Cursor: topmost gameplay element — ABOVE the storyboard Overlay
        # layer (osu draws the cursor over every SB layer), under the HUD.
        # Re-install the BR playfield spin so Barrel Roll still rotates it.
        if self.merge_cursors:
            self._install_barrel(t)
            for _mc_frames, _mc_color, _mc_end in self.merge_cursors:
                _sm = self._merge_smoke_cache.get(id(_mc_frames))
                if _sm is None:
                    _sm = smoke_segments(_mc_frames, self._smoke_width_osu * 7.0 / 8.0)
                    self._merge_smoke_cache[id(_mc_frames)] = _sm
                if _sm:
                    self.spr.draw(self._merge_smoke_sprites(_sm, _mc_color, t))
            for _mc_frames, _mc_color, _mc_end in self.merge_cursors:
                self.spr.draw(self._merge_cursor_sprites(_mc_frames, _mc_color, t, _mc_end))
            for _mm_misses, _mm_color in self.merge_misses:
                self.spr.draw(self._merge_miss_sprites(_mm_misses, _mm_color, t))
            self._uninstall_barrel()
            if self.merge_board is not None:
                _bw, _bh, _brgba = self.merge_board.render(t)
                self.spr.upload_texture("mrgd_board", _brgba)
                self.spr.draw([Sprite(_bw / 2.0, _bh / 2.0, _bw, _bh,
                                      "mrgd_board", (1.0, 1.0, 1.0, 1.0))])
        elif self.draw_cursor and self.frames:
            self._install_barrel(t)
            self.spr.draw(self._cursor_sprites(t))
            self._uninstall_barrel()
        if self.hud is not None:
            self.hud.draw(t)          # §5.3 draw order: … → cursors → HUD
        if self.fade_start_ms is not None and self.fade_len_ms > 0.0:
            fa = fade_to_black_alpha(t, self.fade_start_ms, self.fade_len_ms)
            if fa > 0.0:              # §4.10 FadeOutTime: over EVERYTHING
                self.spr.draw([self._full_frame_black(fa)])
        if not skip_results and self.results is not None \
                and self.results_start_ms is not None \
                and t >= self.results_start_ms:
            # Red's shared results card (render/results.py) — dims the
            # whole scene (HUD included, the mania draw order) under it
            self.results.draw(t - self.results_start_ms)
        if self.logo_start_ms is not None:
            self._draw_logo(t)        # intro splash over the idle scene
        if self.seizure_start_ms is not None:
            self._draw_seizure_card(t)     # topmost — it IS the pre-roll

    def _render_fail_frame(self, t: float, skip_results: bool = False) -> None:
        """Gameplay is FROZEN at the death frame; the FailAnimationContainer
        sequence (ppy/osu, MIT) plays over it: every alive hit object drops
        off-screen (per-object gravity + random rotation + shrink), the
        playfield fades toward gray, and an additive red flash pulses. After
        fail_anim_len_ms the results (F) screen takes over."""
        ft = self.fail_time_ms
        self._advance(ft)                 # spawn/retire frozen at death
        # wall-time age (the FailAnimation constants are WALL ms; the render
        # clock is map-ms, so divide out the rate mod folded into anim_len)
        age = max(0.0, t - ft)
        age_wall = age * FAIL_DURATION_MS / self.fail_anim_len_ms
        prog = fail_progress(age_wall)
        obj_alpha = fail_object_alpha(age_wall)
        gray = fail_gray_factor(prog)
        c_scale = fail_content_scale(prog)
        c_rot = fail_content_rotation(prog)
        o_scale = fail_object_scale(prog)
        fall_px = self.cam.len_to_screen(FAIL_FALL_OSU) * prog
        center = self.cam.to_screen(256.0, 192.0)   # playfield centre

        self.spr.begin(clear=self.background)
        self.spr.post_xform = None
        self._draw_background(ft)
        # Background.FadeColour(OsuColour.Gray(0.3f), 60): darken the bg fast
        bg_dark = 1.0 - fail_bg_gray(age_wall)
        if bg_dark > 0.0:
            self.spr.draw([self._full_frame_black(bg_dark)])

        # every alive object drops off-screen with its own gravity + rotation
        for obj in reversed(self._active):
            if isinstance(obj, Spinner):
                pivot = center
            else:
                pivot = self.cam.to_screen(
                    *obj.get_stacked_start_position(self.diff))
            o_rot = math.radians(fail_rotation_deg(obj.get_start_time()) * prog)
            self.spr.post_xform = self._fail_sprite_xform(
                pivot, center, o_scale, o_rot, fall_px, c_scale, c_rot,
                gray, obj_alpha)
            self._fail_body_xform = self._fail_point_xform(
                pivot, center, o_scale, o_rot, fall_px, c_scale, c_rot)
            self._fail_body_alpha = obj_alpha
            approach: list[Sprite] = []
            if isinstance(obj, Spinner):
                self._draw_spinner(obj, ft)
            elif isinstance(obj, Slider):
                self._draw_slider(obj, ft, approach)
            else:
                self._draw_circle(obj, ft, approach)
            if approach:
                self.spr.draw(approach)   # falls with this object's seed
        self._fail_body_xform = None

        # judgment popups: the last verdicts, carried by the whole-playfield
        # Content transform only (gray + shrink/tilt), no per-object gravity
        self.spr.post_xform = self._fail_sprite_xform(
            center, center, 1.0, 0.0, 0.0, c_scale, c_rot, gray, obj_alpha)
        if self.draw_judgment_popups and self._popups:
            popups = self._popup_sprites(ft)
            if popups:
                self.spr.draw(popups)
        self.spr.post_xform = None

        # cursor frozen at the death position (no fall — you died there)
        if self.draw_cursor and self.frames:
            self.spr.draw(self._cursor_sprites(ft))
        # HUD frozen at death → the score/combo/acc/hits AS THEY WERE
        if self.hud is not None:
            self.hud.draw(ft)
        # redFlashLayer (Color4.Red.Opacity(0.6), additive) FadeOutFromOne
        red_a = fail_red_alpha(age_wall)
        if red_a > 0.0:
            self.spr.draw([Sprite(self.cam.screen_w / 2.0,
                                  self.cam.screen_h / 2.0,
                                  float(self.cam.screen_w),
                                  float(self.cam.screen_h), None,
                                  (1.0, 0.0, 0.0, red_a), additive=True)])
        # results (grade F + frozen stats) once the fall completes
        if not skip_results and self.results is not None \
                and self.results_start_ms is not None \
                and t >= self.results_start_ms:
            self.results.draw(t - self.results_start_ms)

    @staticmethod
    def _fail_sprite_xform(pivot, center, o_scale, o_rot, fall_px,
                           c_scale, c_rot, gray, obj_alpha):
        return lambda sp: fail_transform_sprite(
            sp, pivot=pivot, center=center, obj_scale=o_scale, obj_rot=o_rot,
            fall_px=fall_px, content_scale=c_scale, content_rot=c_rot,
            gray=gray, obj_alpha=obj_alpha)

    @staticmethod
    def _fail_point_xform(pivot, center, o_scale, o_rot, fall_px,
                          c_scale, c_rot):
        return lambda p: fail_transform_point(
            p[0], p[1], pivot, center, o_scale, o_rot, fall_px,
            c_scale, c_rot)

    def frame_rgb_pipelined(self, t: float) -> list:
        """Record-loop variant of frame_rgb: returns the frames that are
        READY, oldest first (the PBO ring adds a fixed lag; every frame
        still arrives exactly once, in order). SSAA/results frames flush
        the ring first, then append their synchronously-composited frame,
        so ordering is preserved across the boundary. frame_rgb_drain()
        must be called after the loop."""
        if self.results_ssaa is not None and self.results is not None \
                and self.results_start_ms is not None \
                and t >= self.results_start_ms:
            out = self.spr.read_drain()
            out.append(self._frame_rgb_ssaa(t))
            return out
        self.render_frame(t)
        fr = self.spr.read_rgb_async()
        return [fr] if fr is not None else []

    def frame_rgb_drain(self) -> list:
        return self.spr.read_drain()

    def frame_rgb(self, t: float):
        if self.results_ssaa is not None and self.results is not None \
                and self.results_start_ms is not None \
                and t >= self.results_start_ms:
            return self._frame_rgb_ssaa(t)
        self.render_frame(t)
        return self.spr.read_rgb()

    def _frame_rgb_ssaa(self, t: float):
        """SSAA the results outro: render the scene-behind at OUTPUT res,
        composite the results card on top at the internal supersample res
        (self.results_ssaa's size), then downscale the whole frame back to
        output res with LANCZOS. Only results frames take this path — the
        gameplay portion (t < results_start_ms) renders byte-identically.

        The composite happens in GL exactly as at output res (same painter's
        order + straight-alpha blend), just with more pixels, so the card's
        text/panels rasterise crisp and survive the downscale. The scene
        behind (usually already faded to black by results_start_ms) is drawn
        once at output res and stretched up by the GPU as the backdrop, so
        no gameplay detail is invented — only the card gains resolution. The
        one CPU cost is the final LANCZOS downscale of the supersampled
        composite (the base upscale rides the GPU blit for free)."""
        from PIL import Image

        spr_hi = self.results_ssaa
        ow, oh = self.spr.width, self.spr.height
        iw, ih = spr_hi.width, spr_hi.height

        # 1) scene-behind (no card) at output res
        self.render_frame(t, skip_results=True)
        base = self.spr.read_rgb()                       # (oh, ow, 3)

        # 2) supersampled composite: blit the output-res base up as the
        #    backdrop (GPU LINEAR stretch — it sits under the card's dim
        #    wash / behind a fully-faded scene, so a mip chain is waste),
        #    then draw the card on top at the internal res
        spr_hi.begin(clear=(0.0, 0.0, 0.0))
        spr_hi.upload_texture("_ssaa_base", base, mipmaps=False)
        spr_hi.draw([Sprite(iw / 2.0, ih / 2.0, float(iw), float(ih),
                            "_ssaa_base", (1.0, 1.0, 1.0, 1.0))])
        self.results.draw(t - self.results_start_ms)     # into spr_hi
        hi = spr_hi.read_rgb()                            # (ih, iw, 3)

        # 3) downscale the supersampled frame to the output resolution
        if (iw, ih) == (ow, oh):
            return hi
        return np.asarray(
            Image.fromarray(hi).resize((ow, oh), Image.LANCZOS),
            dtype=np.uint8)

    # --- background / effect layers (settings-surface phase) ---------------------

    def _full_frame_black(self, alpha: float) -> Sprite:
        return Sprite(self.cam.screen_w / 2.0, self.cam.screen_h / 2.0,
                      float(self.cam.screen_w), float(self.cam.screen_h),
                      None, (0.0, 0.0, 0.0, alpha))

    # --- BR: barrel-roll playfield spin -----------------------------------------

    def _install_barrel(self, t: float) -> None:
        """Set the BR playfield-spin affines as the base post_xform / body
        xform for this frame's gameplay draws (rotate about the screen centre +
        scale by minSide/maxSide). No-op when BR is off."""
        if self.barrel is None:
            self._barrel_sprite = None
            self._barrel_point = None
            return
        rot = math.radians(_sm.barrel_rotation_deg(
            t, self.barrel.spin_speed, self.barrel.direction))
        s = self._br_scale
        self._barrel_sprite = self._transform_sprite_xform(
            self._br_center, s, s, rot, 0.0, 0.0)
        self._barrel_point = self._transform_point_xform(
            self._br_center, s, s, rot, 0.0, 0.0)
        self.spr.post_xform = self._barrel_sprite
        self._t_body_xform = self._barrel_point

    def _uninstall_barrel(self) -> None:
        if self.barrel is not None:
            self.spr.post_xform = None
            self._t_body_xform = None
            self._barrel_sprite = None
            self._barrel_point = None

    # --- BL: blinds panels ------------------------------------------------------

    def _blinds_sprites(self, t: float) -> list[Sprite]:
        """OsuModBlinds.DrawableOsuBlinds: two black boxes closing in from the
        left/right screen edges. Their covered width is health-driven (higher HP
        = MORE closed, matching lazer) with the start/break widening from the
        targetBreakMultiplier schedule."""
        closedness = self.health.hp_at(t) if self.health is not None else 1.0
        bmult = _sm.blinds_break_multiplier_at(self._bl_break, t)
        left_edge, right_edge = _sm.blinds_inner_edges(
            self._bl_start, self._bl_end, closedness, bmult)
        h = float(self.cam.screen_h)
        w = float(self.cam.screen_w)
        out: list[Sprite] = []
        if left_edge > 0.0:                # covers [0, left_edge]
            out.append(Sprite(left_edge / 2.0, h / 2.0, left_edge, h,
                              None, (0.0, 0.0, 0.0, 1.0)))
        if right_edge < w:                 # covers [right_edge, w]
            rw = w - right_edge
            out.append(Sprite(right_edge + rw / 2.0, h / 2.0, rw, h,
                              None, (0.0, 0.0, 0.0, 1.0)))
        return out

    # --- BU: hit-position bubbles -----------------------------------------------

    def _bubble_sprites(self, t: float) -> list[Sprite]:
        """OsuModBubbles: for each active bubble (spawned on a hit) an expanding,
        fading disc tinted by the object's combo colour. Scale 1->MaxSize over
        0.8·duration then pops to MaxSize·1.5 while fading out."""
        out: list[Sprite] = []
        if not self._bu_events:
            return out
        dur = self._bu_duration
        lo = bisect.bisect_right(self._bu_times, t) - 1
        # walk back over every bubble whose life window still contains t
        i = lo
        while i >= 0:
            spawn, sx, sy, col, was_hit, max_size = self._bu_events[i]
            if t - spawn >= dur:
                break                      # older bubbles have all expired
            age = t - spawn
            if 0.0 <= age < dur:
                scale = _sm.bubble_scale_at(age, dur, max_size)
                alpha = _sm.bubble_alpha_at(age, dur)
                if alpha > 0.0:
                    d = self._bu_size_px * scale
                    out.append(Sprite(sx, sy, d, d, "disc",
                                      (col[0], col[1], col[2], alpha)))
            i -= 1
        return out

    def _flashlight_sprite(self, t: float) -> Sprite:
        """OsuModFlashlight overlay quad: a screen-covering black square
        centred on the cursor, its uv scaled so the 'flashlight' texture's
        mid-edge (normalised distance 1) lands on the combo-driven radius.
        Clamp-to-edge sampling makes everything past the radius solid black."""
        cx_osu, cy_osu, _ = cursor_at(self.frames, t)
        cx, cy = self.cam.to_screen(cx_osu, cy_osu)
        size_osu = flashlight_size_at(self._fl_timeline, t)
        r_screen = max(self.cam.len_to_screen(size_osu), 1.0)
        half = self._fl_diag                  # half-quad, covers every corner
        k = half / r_screen                   # uv zoom: mid-edge → r_screen
        off = 0.5 * (1.0 - k)
        return Sprite(cx, cy, 2.0 * half, 2.0 * half, "flashlight",
                      (1.0, 1.0, 1.0, 1.0),
                      uv_off=(off, off), uv_scale=(k, k))

    def _sb_dim(self, t: float) -> float:
        """Storyboard brightness = 1 - background dim (DimmableStoryboard
        shares the §4.10 dim envelope; the beat-flash is bg-only)."""
        return 1.0 - (self.dim.level(t) if self.dim is not None else 0.0)

    def _draw_background(self, t: float) -> float:
        """§4.10 background: the map video frame when live (fail-soft to
        the image, then the dark void), dimmed by the envelope, flashed to
        the beat, parallax-shifted opposite the cursor. Returns the
        brightness so the triangles deco can match the dim."""
        with perf.T("background"):
            return self._draw_background_inner(t)

    def _draw_background_inner(self, t: float) -> float:
        b = 1.0 - (self.dim.level(t) if self.dim is not None else 0.0)
        if self.flash_to_beat:
            b = min(b * flash_factor(beat_phase(t, self.beatmap.timings)),
                    1.0)
        key, size = self.bg_key, self.bg_draw_size
        if self.video is not None and self.video.active_at(t):
            buf = self.video.frame_for(t)
            if buf is not None:
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(
                    self.cam.screen_h, self.cam.screen_w, 4)
                self.spr.upload_texture("bg_video", arr)
                key = "bg_video"
                size = (float(self.cam.screen_w), float(self.cam.screen_h))
        if key is None or size is None:
            return b
        cx = self.cam.screen_w / 2.0
        cy = self.cam.screen_h / 2.0
        bw, bh = size
        if self.bg_parallax and self.frames:
            x, y, _ = cursor_at(self.frames, t)
            sx, sy = self.cam.to_screen(x, y)
            nx = (sx - cx) / cx
            ny = (sy - cy) / cy
            dx, dy = parallax_offset(nx, ny, bw, bh,
                                     self.cam.screen_w, self.cam.screen_h)
            bw *= PARALLAX_SCALE
            bh *= PARALLAX_SCALE
            cx += dx
            cy += dy
        self.spr.draw([Sprite(cx, cy, bw, bh, key, (b, b, b, 1.0))])
        return b

    def _triangle_sprites(self, t: float, brightness: float) -> list[Sprite]:
        """The osu-triangles deco (§4.10 bg_triangles): greyscale-tinted,
        matched to the bg dim so it never outshines a dimmed background."""
        w = float(self.cam.screen_w)
        h = float(self.cam.screen_h)
        out: list[Sprite] = []
        for x, y, size, shade, alpha in triangle_states(self._tri_field, t):
            g = shade * brightness
            d = size * h
            out.append(Sprite(x * w, y * h, d, d, "triangle_up",
                              (g, g, g, alpha)))
        return out

    def _warning_arrow_sprites(self, t: float) -> list[Sprite]:
        """§4.6 ShowWarningArrows: the break-end resume warning at the FOUR
        playfield corners (stable/danser layout), blinking on the
        effects.WARN_BLINK_MS square during the last second of a break.

        CRITICAL-3: uses the skin's `arrow-warning` diamond+"!" sprite when it
        ships one (BTMC does) — untinted, all four corners — instead of the
        old two procedural red arrows at the left/right edge midpoints. The
        procedural fallback (skinless) is also corner-positioned, pointing
        inward. Corners: 59 osu!px OUTSIDE each side edge, 52 osu!px INSIDE
        from the top/bottom (measured off danser)."""
        a = warning_arrow_alpha(t, self._warn_anchors)
        if a <= 0.0:
            return []
        corners_osu = ((-59.0, 52.0), (571.0, 52.0),
                       (-59.0, 332.0), (571.0, 332.0))
        corners = [self.cam.to_screen(x, y) for x, y in corners_osu]
        if self._skinned("arrow-warning"):
            w0, h0 = self.skin.size["arrow-warning"]
            w = self.cam.len_to_screen(WARN_SPRITE_OSU)
            h = w * (h0 / w0)
            return [Sprite(sx, sy, w, h, "sk_arrow-warning",
                           (1.0, 1.0, 1.0, a)) for sx, sy in corners]
        # procedural fallback: inward-pointing arrows at the corners
        d = self.cam.len_to_screen(72.0)
        cx = self.cam.screen_w / 2.0
        cy = self.cam.screen_h / 2.0
        color = (0.92, 0.22, 0.28)
        return [Sprite(sx, sy, d, d, "arrow", (*color, a),
                       rotation=math.atan2(cy - sy, cx - sx))
                for sx, sy in corners]

    def _ripple_sprites(self, t: float) -> list[Sprite]:
        """§4.8 CursorRipples: an expanding ring at each press edge."""
        out: list[Sprite] = []
        d0 = 2.3 * self.radius_px
        for x, y, scale, alpha in ripple_states(self._ripple_evs,
                                                self._ripple_times, t):
            sx, sy = self.cam.to_screen(x, y)
            d = d0 * scale
            out.append(Sprite(sx, sy, d, d, "approach",
                              (1.0, 1.0, 1.0, alpha)))
        return out

    def _draw_logo(self, t: float) -> None:
        """show_logo: the R3D 'R' tile splash during the intro, fading out
        exactly as the first approach begins (effects.logo_alpha)."""
        la = logo_alpha(t, self.logo_start_ms, self.first_spawn_ms)
        if la is None:
            return
        k_ui = self.cam.screen_h / 1080.0
        d = LOGO_UI_SIZE * k_ui * logo_scale(t, self.logo_start_ms)
        cx = self.cam.screen_w / 2.0
        cy = self.cam.screen_h * 0.44
        self.spr.draw([
            Sprite(cx, cy, d * 1.9, d * 1.9, "glow",
                   (0.95, 0.28, 0.30, 0.45 * la), additive=True),
            Sprite(cx, cy, d, d, "logo_tile", (1.0, 1.0, 1.0, la)),
        ])

    def _draw_seizure_card(self, t: float) -> None:
        """§4.10 SeizureWarning: danser-style dark card at render start —
        opaque black + warning text, fading into the scene at the end."""
        sa = seizure_alpha(t, self.seizure_start_ms)
        if sa is None:
            return
        self.spr.draw([self._full_frame_black(sa)])
        out: list[Sprite] = []
        ui_k = self.cam.screen_h / 1080.0
        cx = self.cam.screen_w / 2.0
        cy = self.cam.screen_h * 0.46
        self._glyph_run(out, "WARNING", cx, cy - 34.0 * ui_k, 64.0 * ui_k,
                        (0.95, 0.28, 0.30), sa)
        self._glyph_run(out, "THIS BEATMAP CONTAINS FLASHING LIGHTS",
                        cx, cy + 34.0 * ui_k, 26.0 * ui_k,
                        (0.92, 0.92, 0.95), 0.95 * sa)
        self.spr.draw(out)

    def _draw_merged_bodies(self, t: float) -> None:
        """§4.9 SliderMerge: every visible body in ONE distance pass under
        all objects (danser's merged look — shared borders, no stacking).
        The merged pass necessarily unifies colour and fade: it takes the
        OLDEST visible slider's combo colour (danser does the same) and
        the max body alpha (bodies mid-fade merge at the brighter value —
        an accepted approximation, documented)."""
        fade_in = self.diff.time_fade_in
        items = []
        alpha = 0.0
        color = None
        border = None
        for obj in self._active:                 # oldest first
            if not isinstance(obj, Slider):
                continue
            pts = self._slider_paths.get(id(obj))
            if not pts:
                continue
            preempt = self._preempt_for(obj)     # FR extends per object
            b_alpha = body_alpha(t, obj.get_start_time(), obj.get_end_time(),
                                 preempt, fade_in)
            if b_alpha <= 0.0:
                continue
            items.append((pts, snake_range(
                t, obj.get_start_time(), obj.part_len, obj.repeat_count,
                preempt, self.snaking_in, self.snaking_out)))
            alpha = max(alpha, b_alpha)
            if color is None:
                oc = self._color(obj)
                color = (self.track_override if self.track_override
                         is not None else oc)
                # TC: outline-only merged body (transparent fill, accent border)
                border = oc if self.traceable else self.border_color
        if not items:
            return
        if self.traceable:
            style = BodyStyle(body_color=color, border_color=border,
                              inner_alpha=0.0, outer_alpha=0.0)
        else:
            style = BodyStyle(body_color=color, border_color=border)
        body = self.bodies.build_merged(items, self.radius_px, style)
        self.bodies.draw_body(body, self.spr.fbo, alpha=alpha)

    # --- per-object draws -----------------------------------------------------------

    def _color(self, obj) -> tuple[float, float, float]:
        # osu!(lazer) legacy combo-colour indexing. The first combo lands on
        # the SECOND colour, not the first: IHasComboInformation.UpdateComboInformation
        # sets ComboIndex/ComboIndexWithOffsets to 1 for the opening combo
        # (index starts 0, then `index++` on the first NewCombo), and the skin
        # lookup is ComboColours[index % Count] with no further offset
        # (LegacySkin.GetComboColour / ArgonSkin.getComboColour). Our parser
        # keeps a 0-based combo_set/combo_set_hax, so we add +1 to match.
        # Beatmap [Colours] use ComboIndexWithOffsets (LegacyBeatmapSkin
        # override → combo_set_hax); skin/default colours use ComboIndex
        # (→ combo_set).
        if getattr(self, "synesthesia", False):
            # OsuModSynesthesia overrides AccentColour from the beat-snap
            # divisor; precomputed per object in __init__.
            c = self._sy_colour.get(id(obj))
            if c is not None:
                return c
        idx = (obj.combo_set_hax if self.combo_colors_from_beatmap
               else obj.combo_set) + 1
        return self.combo_colors[idx % len(self.combo_colors)]

    def _plain_circle_sprites(self, x: float, y: float, color, alpha: float,
                              scale: float = 1.0,
                              role: str = "hit") -> list[Sprite]:
        """Hit-circle visual without a number: skin circle (tinted) +
        overlay (untinted, osu semantics) when the skin provides them,
        else the procedural disc + ring. `role` picks the §3.3
        specialisation — slider heads/tails prefer sliderstartcircle/
        sliderendcircle(+overlays) via GetMostSpecific. A skin-blanked
        element (fully transparent) draws nothing."""
        sk = self.skin
        if sk is None:                    # Argon league (ArgonMainCirclePiece)
            if role == "slider_end":
                # ArgonSliderTail draws NOTHING: DrawableSliderTail.CirclePiece
                # is a SkinnableDrawable(SliderTailHitCircle, _ => Empty()) —
                # "no default for this; only visible in legacy skins" — and
                # OsuArgonSkinTransformer has NO SliderTailHitCircle case, so it
                # falls through to that Empty() fallback. Real Argon's slider
                # end is just the ArgonSliderBody's rounded snake cap (the body
                # "just ends"); no accent disc / white ring there. The tail
                # reverse arrow (ArgonReverseArrow) is emitted separately.
                return []
            return self._argon_circle_sprites(x, y, color, alpha, scale)
        if sk is not None:
            circle_el, overlay_el = sk.circle_elements(role)
            if circle_el is not None:
                k = self.circle_k
                out: list[Sprite] = []
                if circle_el not in sk.empty:
                    w, h = sk.size[circle_el]
                    out.append(Sprite(x, y, w * k * scale, h * k * scale,
                                      f"sk_{circle_el}", (*color, alpha)))
                if overlay_el is not None and overlay_el not in sk.empty:
                    ow, oh = sk.size[overlay_el]
                    out.append(Sprite(x, y, ow * k * scale, oh * k * scale,
                                      f"sk_{overlay_el}",
                                      (1.0, 1.0, 1.0, alpha)))
                return out
        d = 2.0 * self.radius_px * scale
        return [
            Sprite(x, y, d, d, "disc", (*color, alpha)),
            Sprite(x, y, d, d, "ring", (1.0, 1.0, 1.0, alpha)),
        ]

    def _argon_circle_sprites(self, x: float, y: float, color, alpha: float,
                              scale: float = 1.0) -> list[Sprite]:
        """ArgonMainCirclePiece (skinless): the layered accent disc
        (bake_argon_circle: outer/inner gradient + dark fills, combo-tinted)
        under the bold white RingPiece border (bake_argon_border, untinted)."""
        d = 2.0 * self.radius_px * scale
        return [
            Sprite(x, y, d, d, "argon_circle", (*color, alpha)),
            Sprite(x, y, d, d, "argon_border", (1.0, 1.0, 1.0, alpha)),
        ]

    def _argon_hit_flash(self, out: list[Sprite], x: float, y: float,
                         color, age: float) -> None:
        """ArgonMainCirclePiece hit flash: the outerGradient flashing white
        plus the additive FlashPiece bloom, up over 150 ms then out — a pale
        accent bloom (append additive over the fading circle)."""
        if age < 0.0 or age >= ARGON_FLASH_LIFE_MS:
            return
        if age < ARGON_FLASH_IN_MS:
            env = _ease_out_quint(age / ARGON_FLASH_IN_MS)
        else:
            env = 1.0 - _ease_out_quint(
                (age - ARGON_FLASH_IN_MS)
                / (ARGON_FLASH_LIFE_MS - ARGON_FLASH_IN_MS))
        if env <= 0.0:
            return
        d = 2.0 * self.radius_px
        # accent FlashPiece glow (Radius OBJECT_RADIUS*0.6 → ~1.6× the circle)
        out.append(Sprite(x, y, d * 1.6, d * 1.6, "glow",
                          (*color, ARGON_FLASH_GLOW_ALPHA * env),
                          additive=True))
        # outerGradient flashing white (the OUTER_GRADIENT_SIZE bloom)
        out.append(Sprite(x, y, d * 0.86, d * 0.86, "argon_circle",
                          (1.0, 1.0, 1.0, ARGON_FLASH_CORE_ALPHA * env),
                          additive=True))

    def _argon_ring_explosion(self, out: list[Sprite], ev, age: float) -> None:
        """ArgonJudgementPiece.RingExplosion: additive ring "bubbles" that
        scatter outward from the hit on a random direction (4 small for
        Ok/Meh, +4 large for Great), coloured OsuColour.ForHitResult, over
        600 ms (OutQuint move) while the group fades out over 1000 ms."""
        if age < 0.0 or age >= ARGON_RING_FADE_MS:
            return
        kind = ev.kind
        if kind is JudgmentKind.MISS:
            return
        if kind is JudgmentKind.HIT50:
            n_small, n_large, tmul = 3, 0, 0.3
        elif kind is JudgmentKind.HIT100:
            n_small, n_large, tmul = 4, 0, 0.6
        else:                              # HIT300 (Great/Perfect)
            n_small, n_large, tmul = 4, 4, 1.0
        travel = ARGON_RING_TRAVEL_OSU * tmul
        color = ARGON_JUDGE_COLOR[kind]
        move = _ease_out_quint(min(age, ARGON_RING_MOVE_MS)
                               / ARGON_RING_MOVE_MS)
        alpha = 1.0 - _ease_out_quint(age / ARGON_RING_FADE_MS)
        if alpha <= 0.0:
            return
        seed = int(getattr(ev, "object_id", 0))
        bx, by = ev.x, ev.y

        def det(i: int, salt: int) -> float:
            h = (seed * 2654435761 + i * 40503 + salt * 2246822519) & 0xFFFFFFFF
            h ^= h >> 13
            h = (h * 1274126177) & 0xFFFFFFFF
            h ^= h >> 16
            return (h & 0xFFFFFFFF) / 4294967295.0

        bubbles = [(ARGON_RING_SMALL_OSU, i) for i in range(n_small)] + \
                  [(ARGON_RING_LARGE_OSU, 100 + i) for i in range(n_large)]
        for diam_osu, i in bubbles:
            ang = det(i, 0) * 2.0 * math.pi
            dist = travel * (0.5 + 0.5 * det(i, 1))
            r = dist * (0.3 + 0.7 * move)
            ox = math.cos(ang) * r
            oy = math.sin(ang) * r
            sx, sy = self.cam.to_screen(bx + ox, by + oy)
            s = self.cam.len_to_screen(diam_osu)
            out.append(Sprite(sx, sy, s, s, "argon_bubble",
                              (*color, alpha), additive=True))

    def _number_sprites(self, x: float, y: float, number: int,
                        num_alpha: float) -> list[Sprite]:
        """Combo number: skin HitCirclePrefix digits (HitCircleOverlap
        layout, native size at circle scale) or the procedural digits."""
        sk = self.skin
        out: list[Sprite] = []
        if sk is not None and sk.has("digits"):
            k = self.circle_k * _COMBO_NUMBER_SCALE
            for ch, dx, w, h in layout_skin_digits(number, sk.digit_sizes,
                                                   sk.info.hit_circle_overlap):
                out.append(Sprite(x + dx * k, y, w * k, h * k,
                                  f"sk_digit_{ch}",
                                  (1.0, 1.0, 1.0, num_alpha)))
            return out
        if self.skin is None:               # Argon league → bundled font
            h = self.radius_px * ARGON_DIGIT_CAP_SCALE * _COMBO_NUMBER_SCALE
            for ch, dx, w in layout_digits(number, self.bank.argon_digit_aspect,
                                           h):
                out.append(Sprite(x + dx, y, w, h, f"adigit_{ch}",
                                  (1.0, 1.0, 1.0, num_alpha)))
            return out
        h = self.radius_px * _COMBO_NUMBER_SCALE  # digit height = half the circle diameter (x held scale)
        for ch, dx, w in layout_digits(number, self.bank.digit_aspect, h):
            out.append(Sprite(x + dx, y, w, h, f"digit_{ch}",
                              (1.0, 1.0, 1.0, num_alpha)))
        return out

    def _circle_sprites(self, x: float, y: float, color, alpha: float,
                        scale: float, number: int | None,
                        num_alpha: float, role: str = "hit") -> list[Sprite]:
        plain = self._plain_circle_sprites(x, y, color, alpha, scale, role)
        nums: list[Sprite] = []
        if (number is not None and self.draw_combo_numbers
                and num_alpha > 0.0):
            nums = self._number_sprites(x, y, number, num_alpha)
        sk = self.skin
        if (nums and sk is not None
                and sk.info.hit_circle_overlay_above_number):
            circle_el, overlay_el = sk.circle_elements(role)
            if (circle_el is not None and overlay_el is not None
                    and overlay_el not in sk.empty and plain):
                # skin.ini flag: circle → number → overlay (the overlay is
                # always the last plain sprite when it drew)
                return plain[:-1] + nums + plain[-1:]
        return plain + nums  # default: circle → overlay → number

    def _verdict(self, obj):
        return self.judgments.verdict_for(obj) if self.judgments else None

    def _head_sprites(self, obj, t: float, pos_osu, approach_out,
                      role: str = "hit") -> list[Sprite]:
        """Hit-circle lifecycle sprites (shared by circles and slider heads).
        With judgments: the explosion anchors on the REAL click time; a
        missed head quick-fades at its window close instead of exploding.
        Without judgments: the Phase-1 perfect-hit-at-startTime fallback."""
        color = self._color(obj)
        # Freeze Frame extends this object's preempt (whole combo appears at the
        # combo start); TimeFadeIn is unchanged. All fade/approach math below
        # keys off this per-object preempt.
        preempt, fade_in = self._preempt_for(obj), self.diff.time_fade_in
        if self.hidden:
            # OsuModHidden.applyFadeInAdjustment (ApplyToBeatmap): circles
            # and slider HEADS get TimeFadeIn = TimePreempt*0.4. This speeds
            # the fade-in AND — since the HD fade-out starts at StartTime-
            # TimePreempt+TimeFadeIn — pulls the fade-out forward so the
            # object is gone ~0.3*preempt BEFORE the hit instead of lingering
            # past it. Sliders keep default TimeFadeIn (that is the body pass).
            fade_in = self.diff.preempt * HIDDEN_FADE_IN_MULT
        start = obj.get_start_time()
        v = self._verdict(obj)
        x, y = self.cam.to_screen(*pos_osu)
        hit_for_flash = None
        if v is not None and v.hit_time is None:      # head missed
            classic = bool(getattr(self.judgments, "classic", False))
            alpha = miss_fade_alpha(t, start, preempt, fade_in, v.deadline,
                                    classic=classic,
                                    ok=(self.diff.hit100 if classic else None))
            scale = 1.0
            na = alpha
        else:
            hit_time = v.hit_time if v is not None else None
            alpha, scale = circle_alpha_scale(t, start, preempt, fade_in,
                                              hit_time=hit_time,
                                              fade_out=self._hit_fade,
                                              explode_scale=self._hit_explode)
            na = number_alpha(t, start, preempt, fade_in, hit_time=hit_time)
            hit_for_flash = hit_time if hit_time is not None else start
        if self._t_force_opaque:           # OsuModSpinIn.FadeIn(): opaque from spawn
            anchor = (v.deadline if (v is not None and v.hit_time is None)
                      else (hit_for_flash if hit_for_flash is not None else start))
            if t <= anchor:
                alpha = 1.0
                na = 1.0
        if self.hidden:                    # OsuModHidden: circle/number fade out
            hf = hidden_circle_fade(t, start, preempt, fade_in)
            alpha *= hf
            na *= hf
        sprites: list[Sprite] = []
        # OsuModTraceable: hide the whole CirclePiece (fill + ring + number) and
        # its hit flash — "we only want to see the approach circle". The
        # approach ring below still draws (TC : IRequiresApproachCircles).
        if not self.traceable:
            if alpha > 0.0:
                sprites = self._circle_sprites(x, y, color, alpha, scale,
                                               obj.combo_number, na, role)
                # Argon league: KiaiFlash — the kiaiContainer's additive
                # combo-tinted disc pulsing on each red-line beat during kiai
                # (visible only while the circle is on screen; rides its alpha).
                if self.skin is None:
                    ks = argon_kiai_flash_strength(t, start - preempt,
                                                   self.beatmap.timings)
                    if ks > 0.0:
                        d = 2.0 * self.radius_px * scale
                        sprites.append(Sprite(x, y, d, d, "disc",
                                              (*color, ks * alpha),
                                              additive=True))
            # Argon league: ArgonMainCirclePiece hit flash (outerGradient→white
            # + the additive FlashPiece bloom) — the pale accent bloom on hit.
            if self.skin is None and hit_for_flash is not None:
                self._argon_hit_flash(sprites, x, y, color, t - hit_for_flash)
        # OsuModHidden / GR / DF / SI : IHidesApproachCircles — no ring.
        # WG / TR do NOT hide it: the approach circle rides the object's offset.
        if (self.draw_approach_circles and not self.hidden
                and not self._t_hide_approach):
            asa = self._approach_geometry(obj, t, start, preempt, fade_in)
            if asa is not None:
                a_scale, a_alpha = asa
                ax, ay = x, y
                if self._dp_center_osu is not None:
                    # DP: the ring rides the object's depth centre + scale (the
                    # deferred approach pass bypasses the per-object post_xform).
                    ax, ay = self.cam.to_screen(*self._dp_center_osu)
                    a_scale *= self._dp_scale
                elif self._t_off != (0.0, 0.0):
                    ax, ay = self.cam.to_screen(pos_osu[0] + self._t_off[0],
                                                pos_osu[1] + self._t_off[1])
                sk = self.skin
                if sk is not None and sk.has("approachcircle"):
                    aw, ah = sk.size["approachcircle"]
                    k = self.circle_k
                    approach_out.append(Sprite(
                        ax, ay, aw * k * a_scale, ah * k * a_scale,
                        "sk_approachcircle", (*color, a_alpha)))
                elif sk is None:               # Argon league approach ring
                    ad = 2.0 * self.radius_px * a_scale
                    approach_out.append(Sprite(ax, ay, ad, ad, "argon_approach",
                                               (*color, a_alpha)))
                else:
                    ad = 2.0 * self.radius_px * a_scale
                    approach_out.append(Sprite(ax, ay, ad, ad, "approach",
                                               (*color, a_alpha)))
        return sprites

    def _draw_circle(self, obj, t: float, approach_out) -> None:
        sprites = self._head_sprites(
            obj, t, obj.get_stacked_start_position(self.diff), approach_out)
        if sprites:
            self.spr.draw(sprites)

    def _draw_slider(self, obj, t: float, approach_out) -> None:
        color = self._color(obj)
        # Freeze Frame extends the slider's preempt too (its body/snake/head all
        # appear at the combo start); TimeFadeIn unchanged.
        preempt, fade_in = self._preempt_for(obj), self.diff.time_fade_in
        start, end = obj.get_start_time(), obj.get_end_time()
        spawn = start - preempt
        b_alpha = body_alpha(t, start, end, preempt, fade_in)
        if self.hidden:                    # OsuModHidden: body fades (Easing.Out)
            b_alpha *= hidden_slider_fade(t, start, end, preempt, fade_in)
        pts = self._slider_paths.get(id(obj))
        ticks, arrows = self._slider_marks.get(id(obj), ([], []))
        snake_a, snake = snake_range(t, start, obj.part_len,
                                     obj.repeat_count, preempt,
                                     self.snaking_in, self.snaking_out)
        sprites: list[Sprite] = []
        tip = None
        # FAIL: the body pass bypasses spr.post_xform, so rigid-transform the
        # body polyline here (same per-object fall) and fade with the object.
        body_pts = pts
        body_fade = 1.0
        if pts and self._fail_body_xform is not None:
            body_pts = [self._fail_body_xform(p) for p in pts]
            body_fade = self._fail_body_alpha
        elif pts and self._t_body_xform is not None:
            # GR/DF/SI scale (+ SI grow) / WG/TR offset the whole slider body
            body_pts = [self._t_body_xform(p) for p in pts]
        if b_alpha > 0.0 and pts:
            if not self.slider_merge:      # merged bodies drew already
                if self.traceable:
                    # OsuModTraceable: outline-only body — AccentColour.Opacity(0)
                    # (transparent fill) + BorderColour = AccentColour. Same look
                    # skinned or skinless (standard border width, path radius).
                    body = self.bodies.build_body(
                        body_pts, self.radius_px,
                        BodyStyle(body_color=color, border_color=color,
                                  inner_alpha=0.0, outer_alpha=0.0),
                        snake=(snake_a, snake))
                    self.bodies.draw_body(body, self.spr.fbo,
                                          alpha=b_alpha * body_fade)
                elif self.skin is None:    # Argon league (ArgonSliderBody)
                    body = self.bodies.build_body(
                        body_pts, ARGON_OUTER_GRAD_R * self.radius_px,
                        self._argon_body_style(color, b_alpha),
                        snake=(snake_a, snake))
                    self.bodies.draw_body(body, self.spr.fbo, alpha=body_fade)
                else:
                    body_base = (self.track_override
                                 if self.track_override is not None
                                 else color)   # skin.ini SliderTrackOverride
                    body = self.bodies.build_body(
                        body_pts, self.radius_px,
                        BodyStyle(body_color=body_base,
                                  border_color=self.border_color),
                        snake=(snake_a, snake))
                    self.bodies.draw_body(body, self.spr.fbo,
                                          alpha=b_alpha * body_fade)
            # ticks of the ACTIVE span: above the body, under the circles
            sprites.extend(self._tick_sprites(t, ticks, obj, spawn, fade_in,
                                              snake_a, snake))
            # tail end circle rides the visible body's far end: the snake
            # tip during snake-in; the RETRACTING tip when a tail→head
            # final span snakes out (danser: the cap follows the shrink).
            # OsuModTraceable hides the DrawableSliderTail entirely (no cap).
            tip = sub_path(pts, snake_a, snake)[-1]
            if not self.traceable:
                sprites.extend(self._plain_circle_sprites(tip[0], tip[1],
                                                          color, b_alpha,
                                                          role="slider_end"))
        # slider ball following PositionAt(t) (repeats included — scorePath
        # handles the back-and-forth). While the ruleset says tracking was
        # lost the ball dims — the visible sliderbreak cue (there is no
        # tail explosion to lose in this phase).
        if start <= t <= end + BALL_FADE_OUT_MS:
            v = self._verdict(obj)
            # tracking is sampled at min(t, end): during the post-end fade
            # the ball keeps whatever alpha it had when the slider expired.
            tracked = v is None or v.tracked_at(min(t, end))
            active_ball_alpha = 1.0 if tracked else BALL_DETACHED_ALPHA
            ball_alpha = slider_ball_alpha(t, start, end, active_ball_alpha)
            bx, by = self.cam.to_screen(
                *obj.get_stacked_position_at(min(t, end), self.diff))
            sk = self.skin
            # Follow circle EXPAND on tracking-gain (lazer DefaultFollowCircle):
            # scale 1x -> FOLLOW_AREA and fade 0 -> 1 over FOLLOW_CIRCLE_IN_MS
            # (OutQuint) instead of popping to full size. Anchored to the slider
            # start (tracked-from-head, the common case); mid-slider re-acquire
            # re-expand and release/end scale-down are the V3 transition model.
            follow_f = _ease_out_quint((t - start) / FOLLOW_CIRCLE_IN_MS)
            if sk is None:                 # Argon league ball + follow circle
                sprites.extend(self._argon_ball_sprites(bx, by, color,
                                                        ball_alpha, tracked,
                                                        follow_f))
            else:
                if tracked and sk.has("sliderfollowcircle"):
                    # follow circle grows to 2.4x the circle diameter over the
                    # tracking-gain ramp; it fades with the ball post-end.
                    fw, fh = sk.size["sliderfollowcircle"]
                    area = 1.0 + (FOLLOW_CIRCLE_SCALE - 1.0) * follow_f
                    m = area * 2.0 * self.radius_px / max(fw, fh)
                    sprites.append(Sprite(bx, by, fw * m, fh * m,
                                          "sk_sliderfollowcircle",
                                          (1.0, 1.0, 1.0, ball_alpha * follow_f)))
                if sk.has("sliderb"):
                    bw, bh = sk.size["sliderb"]
                    k = self.circle_k
                    sprites.append(Sprite(bx, by, bw * k, bh * k,
                                          sk.frame_key("sliderb", min(t, end)),
                                          (*self._ball_tint(color), ball_alpha)))
                else:
                    d = 2.0 * self.radius_px
                    sprites.append(Sprite(bx, by, d, d, "disc",
                                          (*color, ball_alpha)))
        # head circle (+ its approach ring) on top of body/ball
        sprites.extend(self._head_sprites(
            obj, t, obj.get_stacked_start_position(self.diff), approach_out,
            role="slider_head"))
        if pts:
            # reverse arrows ON TOP of the head circle. The TAIL arrow must
            # also draw after the head disc/number: on a SHORT single-repeat
            # slider its pinned end position overlaps the head circle and
            # would otherwise be occluded by it (normal-length sliders are
            # unaffected — the two ends don't overlap). tip/ride_tip is
            # vestigial (arrows are pinned at marker.pos), so None is correct.
            sprites.extend(self._arrow_sprites(t, arrows, True, spawn,
                                               fade_in, pts, snake, None))
            sprites.extend(self._arrow_sprites(t, arrows, False, spawn,
                                               fade_in, pts, snake, None))
        if sprites:
            self.spr.draw(sprites)

    def _argon_body_style(self, color, b_alpha: float) -> BodyStyle:
        """ArgonSliderBody (skinless): accent border, interior = accent
        .Darken(4) (=×0.2 via shade2 offset −4), border width tuned so the
        border portion == GRADIENT_THICKNESS on the 0.862·R path radius."""
        return BodyStyle(body_color=color, border_color=color,
                         inner_offset=-4.0, outer_offset=-4.0,
                         inner_alpha=1.0, outer_alpha=1.0,
                         border_width=ARGON_SLIDER_BORDER_WIDTH,
                         alpha=b_alpha * ARGON_SLIDER_BODY_ALPHA)

    def _argon_ball_sprites(self, bx: float, by: float, color, ball_alpha,
                            tracked: bool, follow_f: float = 1.0) -> list[Sprite]:
        """ArgonSliderBall (accent gradient fill + dark '>' + white ring) with
        the additive ArgonFollowCircle while tracking. follow_f (0..1) is the
        tracking-gain expand ramp: the circle grows 1x -> FOLLOW_AREA + fades in."""
        out: list[Sprite] = []
        ball_d = ARGON_BALL_DIAM_FRAC * 2.0 * self.radius_px
        if tracked:
            area = 1.0 + (ARGON_FOLLOW_AREA - 1.0) * follow_f
            fd = area * ball_d
            out.append(Sprite(bx, by, fd, fd, "argon_follow",
                              (*color, 0.9 * follow_f), additive=True))
        out.append(Sprite(bx, by, ball_d, ball_d, "argon_ball",
                          (*color, ball_alpha)))
        out.append(Sprite(bx, by, ball_d, ball_d, "argon_ball_ring",
                          (1.0, 1.0, 1.0, ball_alpha)))
        return out

    def _tick_sprites(self, t: float, ticks, obj, spawn: float,
                      fade_in: float, snake_a: float,
                      snake: float) -> list[Sprite]:
        """sliderscorepoint sprites for the ACTIVE span only — untinted,
        gated to the VISIBLE body range (snake-in growth AND snake-out
        retraction), pop-on-hit / vanish-on-miss."""
        if not ticks or obj.part_len <= 0:
            return []
        start = obj.get_start_time()
        active_span = (0 if t < start else
                       min(int((t - start) // obj.part_len),
                           obj.repeat_count - 1))
        sk = self.skin
        use_skin = sk is not None and sk.has("sliderscorepoint")
        if use_skin and "sliderscorepoint" in sk.empty:
            return []          # skin explicitly blanks ticks
        argon = sk is None     # Argon league (ArgonSliderScorePoint)
        tint = self._color(obj) if argon else (1.0, 1.0, 1.0)
        out: list[Sprite] = []
        k = self.circle_k
        for tm, sx, sy, span_start, hit in ticks:
            if tm.span != active_span or not (snake_a <= tm.progress
                                              <= snake):
                continue
            asa = tick_alpha_scale(t, tm.time, tm.span, span_start, spawn,
                                   fade_in, hit)
            if asa is None:
                continue
            alpha, scale = asa
            if use_skin:
                w, h = sk.size["sliderscorepoint"]
                out.append(Sprite(sx, sy, w * k * scale, h * k * scale,
                                  "sk_sliderscorepoint",
                                  (1.0, 1.0, 1.0, alpha)))
            elif argon:
                d = ARGON_TICK_FRAC * 2.0 * self.radius_px * scale
                out.append(Sprite(sx, sy, d, d, "argon_tick",
                                  (*tint, alpha)))
            else:
                d = TICK_LOGICAL_PX * k * scale
                out.append(Sprite(sx, sy, d, d, "dot",
                                  (1.0, 1.0, 1.0, alpha)))
        return out

    def _arrow_sprites(self, t: float, arrows, at_tail: bool, spawn: float,
                       fade_in: float, pts, snake: float,
                       tip) -> list[Sprite]:
        """reversearrow sprites for one end — WHITE (never combo-tinted),
        beat-pulsed per the §3.2 version gate, pointing along the path
        tangent INTO the slider. The tail arrow rides the snake tip while
        the body grows."""
        sel = [a for a in arrows if a[0].at_tail == at_tail]
        if not sel:
            return []
        sk = self.skin
        use_skin = sk is not None and sk.has("reversearrow")
        if use_skin and "reversearrow" in sk.empty:
            return []
        legacy = sk.info.legacy_v1_behavior if sk is not None else False
        out: list[Sprite] = []
        k = self.circle_k
        for arrow, sx, sy, rot, hit in sel:
            asa = arrow_alpha_scale(t, arrow, spawn, fade_in, hit)
            if asa is None:
                continue
            alpha, pop_scale = asa
            # M-4: the reverse arrow is PINNED at the slider end (marker.pos),
            # matching danser/lazer (DrawableSliderRepeat fades in at its fixed
            # position during the preempt). Previously the tail arrow rode the
            # snake-in tip (`tip`/`snake`), which floated the chevron above the
            # end circle for slow sliders and dropped a stray crescent near the
            # head early in snake-in — both diffed against the reference.
            p_scale, p_rot = arrow_pulse(beat_phase(t, self.beatmap.timings),
                                         legacy)
            scale = pop_scale * p_scale
            if use_skin:
                w, h = sk.size["reversearrow"]
                out.append(Sprite(sx, sy, w * k * scale, h * k * scale,
                                  "sk_reversearrow", (1.0, 1.0, 1.0, alpha),
                                  rotation=rot + p_rot))
            elif sk is None:               # Argon league (pill + '>>' chevron)
                d = 2.0 * self.radius_px * scale
                out.append(Sprite(sx, sy, d, d, "argon_reverse",
                                  (1.0, 1.0, 1.0, alpha),
                                  rotation=rot + p_rot))
            else:
                d = 2.0 * self.radius_px * scale
                out.append(Sprite(sx, sy, d, d, "arrow",
                                  (1.0, 1.0, 1.0, alpha),
                                  rotation=rot + p_rot))
        return out

    def _ball_tint(self, combo_color) -> tuple[float, float, float]:
        """osu semantics: sliderb is combo-tinted only when the skin allows
        it (AllowSliderBallTint), else the skin.ini SliderBall colour, else
        white (untinted)."""
        info = self.skin.info
        if info.slider_ball_tint:
            return combo_color
        if info.slider_ball is not None:
            return tuple(c / 255.0 for c in info.slider_ball)
        return (1.0, 1.0, 1.0)

    # --- spinner ------------------------------------------------------------------------

    def _skinned(self, name: str) -> bool:
        """Skin provides `name` and doesn't blank it."""
        sk = self.skin
        return sk is not None and sk.has(name) and name not in sk.empty

    def _sk_sprite(self, name: str, x: float, y: float, alpha: float,
                   scale: float = 1.0, rotation: float = 0.0,
                   color=(1.0, 1.0, 1.0), additive: bool = False) -> Sprite:
        """One spinner-space skin sprite: logical px × SPRITE_SCALE →
        osu!px → screen (LegacySpinner sizing)."""
        w, h = self.skin.size[name]
        k = self.spin_k
        return Sprite(x, y, w * k * scale, h * k * scale, f"sk_{name}",
                      (*color, alpha), rotation=rotation, additive=additive)

    def _glyph_run(self, out: list[Sprite], text: str, center_x: float,
                   center_y: float, h_px: float, color, alpha: float) -> None:
        """Centred HUD-glyph text (the procedural spinner prompts/RPM). Argon
        league → bundled font (aglyph_, cap-scaled); legacy → DejaVu glyph_."""
        if self.skin is None:
            aspects, prefix = self.bank.argon_glyph_aspect, "aglyph_"
            h_px = h_px * ARGON_GLYPH_CAP_SCALE
        else:
            aspects, prefix = self.bank.glyph_aspect, "glyph_"
        entries, total = layout_run(text, aspects, h_px)
        x0 = center_x - total / 2.0
        for ch, cxo, w in entries:
            out.append(Sprite(x0 + cxo, center_y, w, h_px, f"{prefix}{ch}",
                              (*color, alpha)))

    def _draw_spinner(self, obj, t: float) -> None:
        start, end = obj.get_start_time(), obj.get_end_time()
        preempt, fade_in = self.diff.preempt, self.diff.time_fade_in
        alpha = body_alpha(t, start, end, preempt, fade_in)
        if alpha <= 0.0:
            return
        track = self._spinner_tracks[id(obj)]
        rot = track.rotation(t)
        prog = track.progress(t)
        cx, cy = self.cam.to_screen(*SPINNER_CENTRE)
        osu = self.cam.len_to_screen  # osu!px → screen px

        # §3.2 SpinnerFadePlayfield: dim everything under the spinner
        if self.spinner_fade_playfield:
            self.spr.draw([Sprite(self.cam.screen_w / 2.0,
                                  self.cam.screen_h / 2.0,
                                  float(self.cam.screen_w),
                                  float(self.cam.screen_h), None,
                                  (0.0, 0.0, 0.0,
                                   SPINNER_DIM_ALPHA * alpha))])

        style = self.spinner_style
        sprites: list[Sprite] = []
        if style == "old":
            self._old_style_sprites(sprites, t, cx, cy, rot, prog, alpha)
        elif style == "new":
            # additive glow halo first, in its own pass, so the discs
            # composite OVER it (gl.draw defers additive within a call)
            if self._skinned("spinner-glow") and prog > 0.0:
                self.spr.draw([self._sk_sprite(
                    "spinner-glow", cx, cy, min(prog, 1.0) * alpha,
                    color=GLOW_BLUE, additive=True)])
            self._new_style_sprites(sprites, t, start, end, cx, cy, rot,
                                    alpha)
        else:
            if prog > 0.0:
                d = osu(PROC_SPINNER_GLOW_OSU)
                gcol = ARGON_SPIN_GLOW if self.skin is None else GLOW_BLUE
                self.spr.draw([Sprite(cx, cy, d, d, "glow",
                                      (*gcol,
                                       0.85 * min(prog, 1.0) * alpha),
                                      additive=True)])
            self._procedural_spinner_sprites(sprites, cx, cy, rot, alpha)

        self._spinner_overlay_sprites(sprites, t, track, start, end,
                                      cx, cy, alpha)
        if self.skin is None:                     # Argon league bonus popup
            self._spinner_bonus_sprites(sprites, id(obj), t, cx, cy, alpha)
        if sprites:
            self.spr.draw(sprites)

    def _spinner_bonus_sprites(self, out: list[Sprite], obj_id: int, t: float,
                               cx: float, cy: float, alpha: float) -> None:
        """ArgonSpinner.bonusCounter: the "+N" cumulative bonus number popping
        (ScaleTo 1.5→1 OutQuint, FadeOutFromOne) 100 osu!px above the centre on
        each completed bonus spin; "MAX" (bigger 1.5→2.8, pink FC618F flash,
        faster fade) once every bonus tick is claimed. Value/timing from
        DrawableSpinner + ArgonSpinner (bundled Argon font)."""
        events = self._spinner_bonus.get(obj_id)
        if not events:
            return
        # the last popup whose time has passed drives the display (a new pop
        # replaces the old — Text/alpha/scale reset)
        idx = bisect.bisect_right([e[0] for e in events], t) - 1
        if idx < 0:
            return
        evt_time, value, is_max = events[idx]
        st = bonus_popup_state(t - evt_time, is_max)
        if st is None:
            return
        p_alpha, scale = st
        p_alpha *= alpha
        if p_alpha <= 0.0:
            return
        text = "MAX" if is_max else str(value)
        color = (1.0, 1.0, 1.0)
        if is_max and (t - evt_time) < SPINNER_BONUS_FLASH_MS:
            # FlashColour(FC618F, 400): pink → white over 400 ms (OutQuint)
            f = _ease_out_quint((t - evt_time) / SPINNER_BONUS_FLASH_MS)
            color = tuple(ARGON_SPIN_GLOW[i] + (1.0 - ARGON_SPIN_GLOW[i]) * f
                          for i in range(3))
        by = cy - self.cam.len_to_screen(SPINNER_BONUS_OFFSET_OSU)
        h = self.cam.len_to_screen(SPINNER_BONUS_FONT_OSU) * scale
        self._glyph_run(out, text, cx, by, h, color, p_alpha)

    def _old_style_sprites(self, out: list[Sprite], t: float, cx: float,
                           cy: float, rot: float, prog: float,
                           alpha: float) -> None:
        """OLD style: background → metre (bottom-up bar reveal) →
        rotating spinner-circle."""
        k = self.spin_k
        bg_bottom = cy + 692.0 * k / 2.0        # nominal @1x background foot
        if self._skinned("spinner-background"):
            out.append(self._sk_sprite("spinner-background", cx, cy, alpha))
            bg_bottom = cy + self.skin.size["spinner-background"][1] * k / 2.0
        if self._skinned("spinner-metre"):
            bars = metre_bar_count(prog, self.spinner_no_blink, t)
            f = bars / 10.0
            if f > 0.0:
                mw, mh = self.skin.size["spinner-metre"]
                mw_px, mh_px = mw * k, mh * k
                out.append(Sprite(cx, bg_bottom - mh_px * f / 2.0,
                                  mw_px, mh_px * f, "sk_spinner-metre",
                                  (1.0, 1.0, 1.0, alpha),
                                  uv_off=(0.0, 1.0 - f),
                                  uv_scale=(1.0, f)))
        if self._skinned("spinner-circle"):
            out.append(self._sk_sprite("spinner-circle", cx, cy, alpha,
                                       rotation=rot))

    def _new_style_sprites(self, out: list[Sprite], t: float, start: float,
                           end: float, cx: float, cy: float, rot: float,
                           alpha: float) -> None:
        """NEW style (LegacyNewStyleSpinner): bottom at rot/3, top +
        middle2 at full rot, middle fading white→red over the duration.
        (The glow was already drawn additively under this stack.)"""
        if self._skinned("spinner-bottom"):
            out.append(self._sk_sprite("spinner-bottom", cx, cy, alpha,
                                       rotation=rot / 3.0))
        if self._skinned("spinner-top"):
            out.append(self._sk_sprite("spinner-top", cx, cy, alpha,
                                       rotation=rot))
        if self._skinned("spinner-middle"):
            w = _clamp01((t - start) / max(end - start, 1e-9))
            color = (1.0,
                     1.0 + (MIDDLE_RED[1] - 1.0) * w,
                     1.0 + (MIDDLE_RED[2] - 1.0) * w)
            out.append(self._sk_sprite("spinner-middle", cx, cy, alpha,
                                       color=color))
        if self._skinned("spinner-middle2"):
            out.append(self._sk_sprite("spinner-middle2", cx, cy, alpha,
                                       rotation=rot))

    def _procedural_spinner_sprites(self, out: list[Sprite], cx: float,
                                    cy: float, rot: float,
                                    alpha: float) -> None:
        """The Argon-ish fallback: outer ring + dark hub + an orbiting
        marker pair riding the accumulated rotation (progress lives in the
        glow behind and the RPM readout below)."""
        if self.skin is None:             # Argon league (ArgonSpinnerDisc)
            self._argon_spinner_sprites(out, cx, cy, rot, alpha)
            return
        osu = self.cam.len_to_screen
        ring_d = osu(PROC_SPINNER_RING_OSU)
        out.append(Sprite(cx, cy, ring_d, ring_d, "approach",
                          (1.0, 1.0, 1.0, 0.9 * alpha)))
        hub_d = osu(PROC_SPINNER_HUB_OSU)
        out.append(Sprite(cx, cy, hub_d, hub_d, "disc",
                          (0.16, 0.17, 0.22, alpha)))
        out.append(Sprite(cx, cy, hub_d, hub_d, "ring",
                          (1.0, 1.0, 1.0, alpha)))
        ang = rot - math.pi / 2.0               # marker starts at 12 o'clock
        r = osu(PROC_MARKER_RADIUS_OSU)
        mx, my = cx + r * math.cos(ang), cy + r * math.sin(ang)
        d = osu(PROC_MARKER_OSU)
        out.append(Sprite(mx, my, d, d, "dot", (1.0, 1.0, 1.0, alpha)))
        ox, oy = cx - r * math.cos(ang), cy - r * math.sin(ang)
        d2 = d * 0.7
        out.append(Sprite(ox, oy, d2, d2, "dot",
                          (1.0, 1.0, 1.0, 0.45 * alpha)))

    def _argon_spinner_sprites(self, out: list[Sprite], cx: float, cy: float,
                               rot: float, alpha: float) -> None:
        """ArgonSpinnerDisc (skinless): white outer ring (ArgonSpinnerRingArc),
        25 orbiting rounded ticks (ArgonSpinnerTicks) riding the rotation, and
        the centre double-ring. The pink fill glow is drawn behind by
        _draw_spinner (progress-scaled)."""
        osu = self.cam.len_to_screen
        ring_d = osu(PROC_SPINNER_RING_OSU)
        out.append(Sprite(cx, cy, ring_d, ring_d, "argon_spin_ring",
                          (1.0, 1.0, 1.0, 0.9 * alpha)))
        tick_r = ring_d * 0.5 * 0.75            # ticks at 0.75 of the radius
        tw, th = osu(34.0), osu(9.0)            # 30×5 rounded bar (readable)
        for i in range(25):
            a = rot + i / 25.0 * 2.0 * math.pi
            tx = cx + tick_r * math.cos(a)
            ty = cy + tick_r * math.sin(a)
            out.append(Sprite(tx, ty, tw, th, "pill",
                              (1.0, 1.0, 1.0, 0.55 * alpha), rotation=a))
        cd = osu(PROC_SPINNER_HUB_OSU)          # centre double-ring
        out.append(Sprite(cx, cy, cd, cd, "argon_spin_center",
                          (1.0, 1.0, 1.0, alpha)))

    def _spinner_overlay_sprites(self, out: list[Sprite], t: float, track,
                                 start: float, end: float, cx: float,
                                 cy: float, alpha: float) -> None:
        """Shared overlays: approach circle (1.9→0.1), spinner-spin,
        spinner-clear, RPM readout — each per-element skin/procedural."""
        osu = self.cam.len_to_screen
        ui_k = self.cam.screen_h / CURSOR_UI_HEIGHT

        asc = spinner_approach_scale(t, start, end)
        if self.draw_approach_circles and asc is not None:
            a_alpha = min(alpha, APPROACH_MAX_ALPHA)
            if self._skinned("spinner-approachcircle"):
                out.append(self._sk_sprite("spinner-approachcircle", cx, cy,
                                           a_alpha, scale=asc))
            else:
                d = osu(PROC_SPINNER_RING_OSU) * asc
                out.append(Sprite(cx, cy, d, d, "approach",
                                  (1.0, 1.0, 1.0, a_alpha)))

        spin_a = spin_prompt_alpha(t, start) * alpha
        if spin_a > 0.0:
            sy = cy + osu(SPIN_OFFSET_OSU)
            if self._skinned("spinner-spin"):
                out.append(self._sk_sprite("spinner-spin", cx, sy, spin_a))
            else:
                self._glyph_run(out, "SPIN!", cx, sy, PROMPT_TEXT_UI * ui_k,
                                (1.0, 1.0, 1.0), spin_a)

        cas = clear_alpha_scale(t, track.clear_time())
        if cas is not None:
            c_alpha, c_scale = cas
            c_alpha *= alpha
            ky = cy + osu(CLEAR_OFFSET_OSU)
            if self._skinned("spinner-clear"):
                out.append(self._sk_sprite("spinner-clear", cx, ky, c_alpha,
                                           scale=c_scale))
            else:
                self._glyph_run(out, "CLEAR!", cx, ky,
                                PROMPT_TEXT_UI * ui_k * c_scale,
                                (0.42, 0.88, 0.47), c_alpha)

        rpm = int(track.rpm(t))
        bottom = self.cam.screen_h - RPM_BOTTOM_MARGIN_UI * ui_k
        if self._skinned("spinner-rpm"):
            bw, bh = self.skin.size["spinner-rpm"]
            k = self.spin_k
            bx = self.cam.screen_w / 2.0
            by = bottom - bh * k / 2.0
            out.append(Sprite(bx, by, bw * k, bh * k, "sk_spinner-rpm",
                              (1.0, 1.0, 1.0, alpha)))
            right_x = bx + (bw / 2.0 - bw * RPM_RIGHT_PAD_FRAC) * k
            self._rpm_digit_sprites(out, rpm, right_x, by,
                                    bh * k * RPM_DIGIT_FRAC, alpha)
        else:
            h = RPM_TEXT_UI * ui_k
            self._glyph_run(out, f"{rpm} RPM", self.cam.screen_w / 2.0,
                            bottom - h / 2.0, h,
                            (0.86, 0.90, 1.0), 0.9 * alpha)

    def _rpm_digit_sprites(self, out: list[Sprite], value: int,
                           right_x: float, center_y: float,
                           target_h_px: float, alpha: float) -> None:
        """RPM digits, right-aligned at right_x: the skin's ScorePrefix
        font when complete (§3.3 rpm font — and the plumbing the HUD
        skin-font remake reuses), else the procedural HUD glyphs."""
        sk = self.skin
        if sk is not None and sk.has("score_digits"):
            entries = layout_skin_digits(value, sk.score_digit_sizes,
                                         sk.info.score_overlap)
            native_h = max(h for _, _, _, h in entries)
            m = target_h_px / native_h
            half = max(dx + w / 2.0 for _, dx, w, _ in entries)
            x0 = right_x - half * m
            for ch, dx, w, h in entries:
                out.append(Sprite(x0 + dx * m, center_y, w * m, h * m,
                                  f"sk_score_{ch}", (1.0, 1.0, 1.0, alpha)))
            return
        if self.skin is None:               # Argon league → bundled font
            aspects, prefix = self.bank.argon_glyph_aspect, "aglyph_"
            mono = self.bank.argon_glyph_mono_advance
            target_h_px = target_h_px * ARGON_GLYPH_CAP_SCALE
        else:
            aspects, prefix = self.bank.glyph_aspect, "glyph_"
            mono = self.bank.glyph_mono_advance
        entries, total = layout_run(str(value), aspects, target_h_px,
                                    mono_advance=mono)
        x0 = right_x - total
        for ch, cxo, w in entries:
            out.append(Sprite(x0 + cxo, center_y, w, target_h_px,
                              f"{prefix}{ch}", (1.0, 1.0, 1.0, alpha)))

    # --- hit lighting -------------------------------------------------------------------

    def _lighting_sprites(self, t: float) -> list[Sprite]:
        """§3.3 `lighting`: combo-tinted additive flash at the popup
        position of every non-miss judgment, 400 ms fade + quint-out
        expand (spinner.lighting_alpha_scale) — drawn UNDER the popups."""
        while (self._lighting_idx < len(self._lightings)
               and self._lightings[self._lighting_idx].time_ms <= t):
            self._lighting_active.append(self._lightings[self._lighting_idx])
            self._lighting_idx += 1
        out: list[Sprite] = []
        keep: list = []
        for ev in self._lighting_active:
            asa = lighting_alpha_scale(t - ev.time_ms)
            if asa is None:
                if t >= ev.time_ms:
                    continue             # expired
                keep.append(ev)
                continue
            keep.append(ev)
            alpha, scale = asa
            color = self._color(self.objects[ev.object_id])
            x, y = self.cam.to_screen(ev.x, ev.y)
            k = self.circle_k
            if self._skinned("lighting"):
                w, h = self.skin.size["lighting"]
                out.append(Sprite(x, y, w * k * scale, h * k * scale,
                                  "sk_lighting", (*color, alpha),
                                  additive=True))
            else:
                d = LIGHTING_LOGICAL_PX * k * scale
                out.append(Sprite(x, y, d, d, "glow",
                                  (*color, LIGHTING_PROC_ALPHA * alpha),
                                  additive=True))
        self._lighting_active = keep
        return out

    # --- judgment popups ---------------------------------------------------------------

    def _popup_sprites(self, t: float) -> list[Sprite]:
        """Procedural judgment indicators (the ruleset phase's sprites):
        300/100/50 as tinted digit runs, miss as the red X — at the
        object's popup position, ResultFadeIn/Out lifecycle."""
        while (self._popup_idx < len(self._popups)
               and self._popups[self._popup_idx].time_ms <= t):
            self._popup_active.append(self._popups[self._popup_idx])
            self._popup_idx += 1
        out: list[Sprite] = []
        keep: list = []
        for ev in self._popup_active:
            if self.skin is None:        # Argon league (ArgonJudgementPiece)
                age = t - ev.time_ms
                res = argon_judgment_transform(ev.kind, age)
                ring_alive = 0.0 <= age < ARGON_RING_FADE_MS
                if res is None and not ring_alive:
                    if t < ev.time_ms:
                        keep.append(ev)
                    continue
                keep.append(ev)
                x, y = self.cam.to_screen(ev.x, ev.y)
                # RingExplosion "bubbles" scatter under the GREAT text
                self._argon_ring_explosion(out, ev, age)
                if res is not None:
                    a2, sc2, dy_osu, rot2 = res
                    dyp = self.cam.len_to_screen(dy_osu)
                    self._argon_judgment_run(out, ARGON_JUDGE_TEXT[ev.kind],
                                             x, y + dyp, sc2,
                                             ARGON_JUDGE_COLOR[ev.kind], a2,
                                             rot2)
                continue
            asa = popup_alpha_scale(t, ev.time_ms)
            if asa is None:
                if t >= ev.time_ms:      # expired
                    continue
                keep.append(ev)
                continue
            keep.append(ev)
            alpha, scale = asa
            # classic miss animation: hit0 falls + rotates while fading
            dy_px, rot = 0.0, 0.0
            if ev.kind is JudgmentKind.MISS and self.miss_fall:
                dy_osu, rot = miss_fall_transform(t - ev.time_ms,
                                                  seed=ev.object_id)
                dy_px = self.cam.len_to_screen(dy_osu)
            sk = self.skin
            el = POPUP_SKIN_ELEMENT[ev.kind]
            if sk is not None and sk.has(el):
                if el in sk.empty:
                    continue   # skin blanks this judgment (empty hit300)
                x, y = self.cam.to_screen(ev.x, ev.y)
                w, h = sk.size[el]
                k = self.circle_k
                out.append(Sprite(x, y + dy_px, w * k * scale,
                                  h * k * scale, f"sk_{el}",
                                  (1.0, 1.0, 1.0, alpha), rotation=rot))
                continue       # skin sprites draw as-authored, untinted
            color = POPUP_COLORS[ev.kind]
            x, y = self.cam.to_screen(ev.x, ev.y)
            if ev.kind is JudgmentKind.MISS:
                d = 2.2 * self.radius_px * scale
                out.append(Sprite(x, y + dy_px, d, d, "miss_x",
                                  (*color, alpha), rotation=rot))
                continue
            if ev.kind is JudgmentKind.HIT300:
                alpha *= POPUP_300_ALPHA
            h = 2.0 * self.radius_px * POPUP_HEIGHT_FRAC * scale
            for ch, dx, w in layout_digits(int(ev.kind.value),
                                           self.bank.digit_aspect, h):
                out.append(Sprite(x + dx, y, w, h, f"digit_{ch}",
                                  (*color, alpha)))
        self._popup_active = keep
        return out

    def _argon_judgment_run(self, out: list[Sprite], text: str, cx: float,
                            cy: float, scale: float, color, alpha: float,
                            rot: float) -> None:
        """ArgonJudgementPiece text: uppercase result, additive, Spacing (5,0),
        drawn centred at the object with the group rotated rigidly about it (so
        the MISS down-drift rotation reads like lazer)."""
        if alpha <= 0.0:
            return
        # bundled Argon font (aglyph_): scale the cell so the visible cap keeps
        # the DejaVu-tuned ~14.4-osu!px size under Nunito's lower cap-fill.
        h = self.cam.len_to_screen(ARGON_JUDGE_FONT_OSU) * scale \
            * ARGON_GLYPH_CAP_SCALE
        spacing = self.cam.len_to_screen(ARGON_JUDGE_SPACING_OSU) * scale
        widths = [self.bank.argon_glyph_aspect.get(ch, 0.6) * h for ch in text]
        total = sum(widths) + spacing * max(len(text) - 1, 0)
        cosr, sinr = math.cos(rot), math.sin(rot)
        gx = -total / 2.0
        for ch, wq in zip(text, widths):
            ox = gx + wq / 2.0
            out.append(Sprite(cx + ox * cosr, cy + ox * sinr, wq, h,
                              f"aglyph_{ch}", (*color, alpha),
                              rotation=rot, additive=True))
            gx += wq + spacing

    # --- follow points -----------------------------------------------------------------

    def _followpoint_sprites(self, t: float) -> list[Sprite]:
        """The dotted same-combo connections (markers.py schedule): skin
        `followpoint` (frame 0 / AnimationFramerate cycling) or the
        procedural dot, rotated along the line, drawn UNDER all objects."""
        while (self._fp_idx < len(self._fp_dots)
               and self._fp_dots[self._fp_idx].fade_in <= t):
            self._fp_active.append(self._fp_dots[self._fp_idx])
            self._fp_idx += 1
        fade = self.diff.time_fade_in
        sk = self.skin
        use_skin = sk is not None and sk.has("followpoint")
        blanked = use_skin and "followpoint" in sk.empty
        out: list[Sprite] = []
        keep: list = []
        k = self.circle_k
        for dot in self._fp_active:
            if t > dot.fade_out + fade:
                continue                  # expired
            keep.append(dot)
            if blanked:
                continue                  # skin explicitly blanks followpoints
            st = followpoint_state(t, dot, fade)
            if st is None:
                continue
            alpha, x, y, scale = st
            sx, sy = self.cam.to_screen(x, y)
            if use_skin:
                w, h = sk.size["followpoint"]
                out.append(Sprite(sx, sy, w * k * scale, h * k * scale,
                                  sk.frame_key("followpoint", t),
                                  (1.0, 1.0, 1.0, alpha),
                                  rotation=dot.rotation))
            elif sk is None:                  # Argon league (ArgonFollowPoint)
                d = ARGON_FP_LOGICAL_PX * k * scale
                out.append(Sprite(sx, sy, d, d, "argon_followpoint",
                                  (1.0, 1.0, 1.0, alpha),
                                  rotation=dot.rotation, additive=True))
            else:
                d = FP_DOT_LOGICAL_PX * k * scale
                out.append(Sprite(sx, sy, d, d, "dot",
                                  (1.0, 1.0, 1.0, 0.85 * alpha),
                                  rotation=dot.rotation))
        self._fp_active = keep
        return out

    # --- cursor -----------------------------------------------------------------------

    def _in_break(self, t: float) -> bool:
        return any(b0 <= t <= b1 for b0, b1 in self._breaks)

    def _bm_factor(self, t: float) -> float:
        """OsuModBloom cursor-scale multiplier at t (=1 with no BM / in a
        break)."""
        if self.bloom_mod is None or self._in_break(t):
            return 1.0
        return _sm.combo_value_at(self._bm_timeline, t,
                                  _sm.BLOOM_TRANSITION_MS, 1.0)

    def _ns_factor(self, t: float) -> float:
        """OsuModNoScope cursor-alpha multiplier at t (=1 with no NS, or during
        a break / a spinner period)."""
        if self.no_scope is None:
            return 1.0
        if self._in_break(t) or any(s0 <= t <= s1 for s0, s1 in self._ns_spinner):
            return 1.0
        initial = _sm.no_scope_alpha(0, self.no_scope.hidden_combo_count)
        return _sm.combo_value_at(self._ns_timeline, t,
                                  _sm.NO_SCOPE_TRANSITION_MS, initial)

    def _smoke_sprites(self, t: float) -> list[Sprite]:
        """Replay Smoke (bit 16): additive white dabs along the held cursor
        path, alpha per lazer SmokeSegment.PointColour. Empty when the
        replay never pressed Smoke (self._smoke_segs == [])."""
        out: list[Sprite] = []
        base_d = 2.0 * self.cam.len_to_screen(self._smoke_width_osu)
        for seg in self._smoke_segs:
            if t < seg.start_ms or t > seg.kill_ms:
                continue
            trunc = min(SMOKE_INITIAL_FADE_MS, seg.end_ms - seg.start_ms)
            lo = bisect.bisect_left(seg.times, min(seg.end_ms, t) - trunc)
            hi = bisect.bisect_right(seg.times, t)
            for i in range(lo, hi):
                x, y, pt, angle, settle = seg.pts[i]
                a = smoke_point_alpha(pt, t, seg.start_ms, seg.end_ms)
                if a <= 0.0:
                    continue
                age = t - pt
                ks = 1.0 - (1.0 - _clamp01(age / SMOKE_SCALE_MS)) ** 5
                scale = SMOKE_INITIAL_SCALE + ks * (1.0 - SMOKE_INITIAL_SCALE)
                if scale <= 0.0:
                    continue
                kr = 1.0 - (1.0 - _clamp01(age / SMOKE_ROT_MS)) ** 5
                rot = angle + kr * settle
                sx, sy = self.cam.to_screen(x, y)
                d = base_d * scale
                out.append(Sprite(sx, sy, d, d, self._smoke_tex,
                                  (1.0, 1.0, 1.0, a), rotation=rot,
                                  additive=True))
        return out

    def _cursor_sprites(self, t: float) -> list[Sprite]:
        sprites = self._base_cursor_sprites(t)
        # BM (bloom): scale the cursor about its screen centre; NS (no scope):
        # fade it. Both are cursor-only, so they wrap the base cursor sprites.
        scale = self._bm_factor(t)
        alpha = self._ns_factor(t)
        if scale == 1.0 and alpha == 1.0:
            return sprites
        cx_osu, cy_osu, _ = cursor_at(self.frames, t)
        px, py = self.cam.to_screen(cx_osu, cy_osu)
        out: list[Sprite] = []
        for sp in sprites:
            col = sp.color
            if alpha != 1.0:
                col = (col[0], col[1], col[2], col[3] * alpha)
            out.append(replace(sp, x=px + (sp.x - px) * scale,
                               y=py + (sp.y - py) * scale,
                               w=sp.w * scale, h=sp.h * scale, color=col))
        return out

    def _merge_smoke_sprites(self, segs, color, t):
        """showdown!mrgd: one player's replay Smoke (bit 16), TINTED to `color`
        (osu draws it white). Ported from _smoke_sprites."""
        out: list[Sprite] = []
        base_d = 2.0 * self.cam.len_to_screen(self._smoke_width_osu)
        for seg in segs:
            if t < seg.start_ms or t > seg.kill_ms:
                continue
            trunc = min(SMOKE_INITIAL_FADE_MS, seg.end_ms - seg.start_ms)
            lo = bisect.bisect_left(seg.times, min(seg.end_ms, t) - trunc)
            hi = bisect.bisect_right(seg.times, t)
            for i in range(lo, hi):
                x, y, pt, angle, settle = seg.pts[i]
                a = smoke_point_alpha(pt, t, seg.start_ms, seg.end_ms)
                if a <= 0.0:
                    continue
                age = t - pt
                ks = 1.0 - (1.0 - _clamp01(age / SMOKE_SCALE_MS)) ** 5
                scale = SMOKE_INITIAL_SCALE + ks * (1.0 - SMOKE_INITIAL_SCALE)
                if scale <= 0.0:
                    continue
                kr = 1.0 - (1.0 - _clamp01(age / SMOKE_ROT_MS)) ** 5
                rot = angle + kr * settle
                sx, sy = self.cam.to_screen(x, y)
                d = base_d * scale
                out.append(Sprite(sx, sy, d, d, self._smoke_tex,
                                  (*color, a), rotation=rot, additive=True))
        return out

    def _merge_miss_sprites(self, misses, color, t):
        """showdown!mrgd: a small, clean per-player colored X at each MISS — pops in,
        holds, fades over ~1.1 s — so you can see WHO choked. A solid bar +
        an additive glow layer per stroke. misses = [(time_ms, (x,y) osu)];
        the position is the PLAYER'S CURSOR at the miss moment (set in merge.py),
        so several players missing the same note don't stack into one pile."""
        out: list[Sprite] = []
        dur = 1100.0
        r = self.radius_px
        for _mtime, _mpos in misses:
            age = t - _mtime
            if age < 0.0 or age > dur:
                continue
            pop = min(1.0, age / 80.0)                     # quick pop-in
            f = pop * ((1.0 - age / dur) ** 0.5)
            sx, sy = self.cam.to_screen(_mpos[0], _mpos[1])
            # small, clean X centred on the player's cursor at the miss moment
            # (~circle-sized) with a soft same-colour halo.
            out.append(Sprite(sx, sy, r * 0.95, r * 0.95, "glow",
                              (*color, 0.30 * f), additive=True))
            for _rot in (0.7853981633974483, -0.7853981633974483):
                out.append(Sprite(sx, sy, r * 1.15, r * 0.15, "disc",
                                  (*color, f), rotation=_rot))
                out.append(Sprite(sx, sy, r * 1.15, r * 0.08, "disc",
                                  (*color, 0.85 * f), rotation=_rot, additive=True))
        return out

    def _merge_cursor_sprites(self, frames, accent, t, end_ms=None):
        """showdown!mrgd: one player's cursor, tinted to `accent`. Skin cursor
        + SPARSE stateless trail (scales to N) if skinned, else procedural glow.
        Instant shrink to 0.75 on keypress; fades out over 500 ms once the
        player's replay has ended (death / partial replay)."""
        fade = 1.0
        if end_ms is not None and t > end_ms:
            fade = 1.0 - (t - end_ms) / 500.0
            if fade <= 0.0:
                return []
        sk = self.skin
        out: list[Sprite] = []
        if sk is not None and sk.has("cursor"):
            k = (self.cam.screen_h / CURSOR_UI_HEIGHT) * self.cursor_scale
            centre = sk.info.cursor_centre
            if sk.has("cursortrail"):
                tw, th = sk.size["cursortrail"]
                tw, th = tw * self.trail_scale, th * self.trail_scale
                ox, oy = (0.0, 0.0) if centre else (tw * k / 2.0, th * k / 2.0)
                if self.long_trail:            # cursormiddle present -> connected ribbon
                    _tp = self._merge_trail_cache.get(id(frames))
                    if _tp is None:
                        _pts = build_distance_trail(frames)
                        _tp = (_pts, [p[2] for p in _pts])
                        self._merge_trail_cache[id(frames)] = _tp
                    for x, y, strength in long_trail_points(_tp[0], _tp[1], t):
                        sx, sy = self.cam.to_screen(x, y)
                        out.append(Sprite(sx + ox, sy + oy, tw * k, th * k,
                                          "sk_cursortrail", (*accent, 0.85 * strength),
                                          additive=True))   # glowing trail (adds onto pixels below)
                else:
                    for ti, strength in sparse_trail_times(t):
                        x, y, _ = cursor_at(frames, ti)
                        sx, sy = self.cam.to_screen(x, y)
                        out.append(Sprite(sx + ox, sy + oy, tw * k, th * k,
                                          "sk_cursortrail", (*accent, 0.85 * strength),
                                          additive=True))   # glowing trail (adds onto pixels below)
            x, y, keys = cursor_at(frames, t)
            sx, sy = self.cam.to_screen(x, y)
            cs = 0.75 if (keys & 0b1111) else 1.0
            cw, ch = sk.size["cursor"]
            ox, oy = (0.0, 0.0) if centre else (cw * k / 2.0, ch * k / 2.0)
            out.append(Sprite(sx + ox, sy + oy, cw * k * cs, ch * k * cs,
                              "sk_cursor", (*accent, 1.0)))
            if sk.has("cursormiddle"):
                mw, mh = sk.size["cursormiddle"]
                out.append(Sprite(sx, sy, mw * k * cs, mh * k * cs,
                                  "sk_cursormiddle", (*accent, 1.0)))
        else:
            d_glow = 2.0 * self.cam.len_to_screen(CURSOR_RADIUS_OSU) * self.cursor_scale
            for ti, kk in trail_times(t):
                x, y, _ = cursor_at(frames, ti)
                sx, sy = self.cam.to_screen(x, y)
                ss = d_glow * (0.55 + 0.35 * kk) * self.trail_scale
                out.append(Sprite(sx, sy, ss, ss, "glow", (*accent, 0.28 * kk), additive=True))
            x, y, keys = cursor_at(frames, t)
            sx, sy = self.cam.to_screen(x, y)
            core = d_glow * 0.62 * (0.75 if (keys & 0b1111) else 1.0)
            out.append(Sprite(sx, sy, core, core, "disc", (1.0, 1.0, 1.0, 1.0)))
            out.append(Sprite(sx, sy, core, core, "ring", (*accent, 0.9)))
            out.append(Sprite(sx, sy, d_glow * 1.6, d_glow * 1.6, "glow",
                              (*accent, 0.5), additive=True))
        if fade < 1.0:
            out = [replace(sp, color=(sp.color[0], sp.color[1], sp.color[2],
                                      sp.color[3] * fade)) for sp in out]
        return out

    def _base_cursor_sprites(self, t: float) -> list[Sprite]:
        if self.use_skin_cursor:
            return self._skin_cursor_sprites(t)
        if self.skin is None:            # Argon league (ArgonCursor + trail)
            return self._argon_cursor_sprites(t)
        # §4.8 rainbow: hue-cycle the accent (glow/ring/trail tint)
        accent = (rainbow_rgb(t) if self.cursor_rainbow
                  else CURSOR_GLOW_COLOR)
        out: list[Sprite] = []
        d_glow = 2.0 * self.cam.len_to_screen(CURSOR_RADIUS_OSU) * self.cursor_scale
        for ti, k in trail_times(t):
            x, y, _ = cursor_at(self.frames, ti)
            sx, sy = self.cam.to_screen(x, y)
            s = d_glow * (0.55 + 0.35 * k) * self.trail_scale
            out.append(Sprite(sx, sy, s, s, "glow",
                              (*accent, 0.28 * k), additive=True))
        x, y, _ = cursor_at(self.frames, t)
        sx, sy = self.cam.to_screen(x, y)
        core = d_glow * 0.62
        out.append(Sprite(sx, sy, core, core, "disc", (1.0, 1.0, 1.0, 1.0)))
        out.append(Sprite(sx, sy, core, core, "ring", (*accent, 0.9)))
        out.append(Sprite(sx, sy, d_glow * 1.6, d_glow * 1.6, "glow",
                          (*accent, 0.5), additive=True))
        return out

    def _argon_cursor_sprites(self, t: float) -> list[Sprite]:
        """ArgonCursor (skinless): the pink→dark-red ring body + white centre
        dot with the cyan EdgeEffect glow, over the ArgonCursorTrail — soft
        WHITE additive blobs (Scale 0.8 of the cursortrail texture ≈ the
        cursor radius; IntervalMultiplier 0.4 → dense continuous trail;
        FadeExponent 4 → short bright tail). NO Colour override in
        ArgonCursorTrail.cs → the trail is WHITE, never pink/cyan. Rainbow
        hue-cycles the ring tint when enabled."""
        out: list[Sprite] = []
        d = 2.0 * self.cam.len_to_screen(CURSOR_RADIUS_OSU) * self.cursor_scale
        tint = rainbow_rgb(t) if self.cursor_rainbow else (1.0, 1.0, 1.0)
        # thin white trail: dense distance-resampled dots, alpha^4 fade
        tw = self.cam.len_to_screen(ARGON_TRAIL_WIDTH_OSU) * self.trail_scale
        trail_tint = tint if self.cursor_rainbow else ARGON_CURSOR_TRAIL
        for x, y, strength in long_trail_points(
                self._argon_trail_pts, self._argon_trail_times, t,
                window_ms=ARGON_TRAIL_WINDOW_MS):
            sx, sy = self.cam.to_screen(x, y)
            a = ARGON_TRAIL_ALPHA * strength ** ARGON_TRAIL_FADE_EXP
            if a <= 0.0:
                continue
            out.append(Sprite(sx, sy, tw, tw, "glow", (*trail_tint, a),
                              additive=True))
        x, y, _ = cursor_at(self.frames, t)
        sx, sy = self.cam.to_screen(x, y)
        out.append(Sprite(sx, sy, d * 1.5, d * 1.5, "glow",
                          (*ARGON_CURSOR_CGLOW, 0.45), additive=True))
        out.append(Sprite(sx, sy, d, d, "argon_cursor", (*tint, 1.0)))
        dot = d * 0.20                    # ArgonCursor centre Circle Scale(0.2)
        out.append(Sprite(sx, sy, dot, dot, "disc", (1.0, 1.0, 1.0, 1.0)))
        return out

    def _skin_cursor_sprites(self, t: float) -> list[Sprite]:
        """Skin cursor: trail snapshots under the cursor, cursormiddle on
        top. Sized in stable's 768-line UI space (native logical px ×
        screen_h/768 × cursor_scale — NOT circle-tied). CursorCentre=0
        hangs the texture from the pointer (stable's top-left anchor).

        Trail per the §3.3 two-mode rule (self.long_trail): cursormiddle
        present (or ForceLongTrail) → LONG CONNECTED trail — cursortrail
        laid along the cursor path strictly by DISTANCE (the
        build_distance_trail resample: uniform spacing at any speed,
        uniform base alpha, age fade along the ribbon); else the classic
        sparse 16.67 ms drops fading over TRAIL_SPRITE_LIFE_MS."""
        sk = self.skin
        k = (self.cam.screen_h / CURSOR_UI_HEIGHT) * self.cursor_scale
        centre = sk.info.cursor_centre
        # §4.8 rainbow: modulate the skin cursor+trail (white/light skin
        # cursors take the hue cleanly; danser tints the same way)
        tint = rainbow_rgb(t) if self.cursor_rainbow else (1.0, 1.0, 1.0)
        out: list[Sprite] = []
        if sk.has("cursortrail"):
            tw, th = sk.size["cursortrail"]
            tw, th = tw * self.trail_scale, th * self.trail_scale
            ox, oy = (0.0, 0.0) if centre else (tw * k / 2.0, th * k / 2.0)
            if self.long_trail:
                pts = long_trail_points(self._trail_pts, self._trail_times,
                                        t)
                for x, y, strength in pts:
                    sx, sy = self.cam.to_screen(x, y)
                    out.append(Sprite(sx + ox, sy + oy, tw * k, th * k,
                                      "sk_cursortrail",
                                      (*tint, 0.85 * strength),
                                      additive=True))
            else:
                for ti, strength in sparse_trail_times(t):
                    x, y, _ = cursor_at(self.frames, ti)
                    sx, sy = self.cam.to_screen(x, y)
                    out.append(Sprite(sx + ox, sy + oy, tw * k, th * k,
                                      "sk_cursortrail",
                                      (*tint, 0.85 * strength),
                                      additive=True))
        x, y, _ = cursor_at(self.frames, t)
        sx, sy = self.cam.to_screen(x, y)
        cw, ch = sk.size["cursor"]
        ox, oy = (0.0, 0.0) if centre else (cw * k / 2.0, ch * k / 2.0)
        out.append(Sprite(sx + ox, sy + oy, cw * k, ch * k, "sk_cursor",
                          (*tint, 1.0)))
        if sk.has("cursormiddle"):
            mw, mh = sk.size["cursormiddle"]
            out.append(Sprite(sx, sy, mw * k, mh * k, "sk_cursormiddle",
                              (*tint, 1.0)))
        return out


class ScenePlayer:
    """record.pipeline.Player over a StdScene: fixed-timestep map clock.

    §5.3 Update semantics: map time advances delta*speed per tick (DT/HT
    rate mods change speed; frame TIMES stay wall-clock so the video runs
    at the right rate with atempo'd audio).

    WU/WD (rate ramp): ``rate_fn`` (timewarp.TimeWarp.rate_at) makes the map
    time advance delta*rate(t) per tick — the INSTANTANEOUS ramped rate. This
    per-tick ``t += dt*rate(t)`` is exactly lazer's own per-frame discretization
    of dg/dw = rate(g), so late-map objects approach faster (WU) / slower (WD)
    with no per-object rescaling. ``rate_fn=None`` keeps the constant-rate line
    untouched -> byte-identical for every non-ramp render.
    """

    def __init__(self, scene: StdScene, end_ms: float,
                 speed: float = 1.0, start_ms: float = 0.0,
                 rate_fn=None):
        self.scene = scene
        self.t = start_ms
        self.end_ms = end_ms
        self.speed = speed
        self.rate_fn = rate_fn

    def update(self, delta_ms: float) -> bool:
        if self.rate_fn is None:
            self.t += delta_ms * self.speed
        else:
            self.t += delta_ms * self.rate_fn(self.t)
        return self.t >= self.end_ms

    def draw(self) -> list:
        # pipelined: 0..n ready frames, strict display order (the PBO
        # readback ring lags ~2 frames; drain() flushes the tail)
        return self.scene.frame_rgb_pipelined(self.t)

    def drain(self) -> list:
        return self.scene.frame_rgb_drain()
