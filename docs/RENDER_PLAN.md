# Go Renderer: Map + Skin → Video — Complete Render Plan

How a Go + OpenGL 3.3 renderer (GLFW + BASS + libyuv + ffmpeg) parses a `.osu` beatmap and an osu! skin and turns them into a rendered video (`-record` mode). Based on a full read of the source (July 2026, master).

Contents:
1. [Bird's-eye pipeline](#1-birds-eye-pipeline)
2. [Beatmap parsing](#2-beatmap-parsing) — every function, struct, and formula
3. [Skin loading](#3-skin-loading) — fallback chain, skin.ini, every texture/sample consumer
4. [Every changeable variable](#4-every-changeable-variable) — full settings JSON + CLI flags
5. [The record/render pipeline](#5-the-recordrender-pipeline) — frame loop, GL→ffmpeg, offline audio
6. [Master pseudo-code](#6-master-pseudo-code)

---

## 1. Bird's-eye pipeline

```
renderer -replay x.osr -record -out video
  │
  ├─ app.Run() → run()                     app/app.go
  │    ├─ parse CLI flags
  │    ├─ parse .osr (rplpa) → md5, mods; sets KNOCKOUT=true, RECORD=true
  │    ├─ settings.LoadSettings(version)   → ConfigDir/default.json (or <name>.json)
  │    ├─ database.Init/LoadBeatmaps       → find beatmap by md5/id/name
  │    ├─ GLFW window (hidden, sized Recording.FrameWidth×FrameHeight), GL init
  │    ├─ bass.Init(offscreen=true)        → 48 kHz float DECODE mixer, no audio device
  │    ├─ beatmap.ParseTimingPointsAndPauses + ParseObjects   (heavy pass)
  │    └─ player = states.NewPlayer(beatMap)
  │         ├─ skin lazily initializes on first texture request (skin.checkInit)
  │         ├─ cameras: osu! 512×384 space → screen (0.8 inset)
  │         ├─ controller: ReplayController (cursor from .osr frames)
  │         ├─ overlay: ScoreOverlay (HUD), background+storyboard, bloom/blur
  │         └─ RECORD mode: skip the realtime update goroutine entirely
  │
  └─ mainLoopRecord()                      fixed-timestep, deterministic
       ├─ ffmpeg.StartFFmpeg(fps,w,h,1000,out)  → 2 ffmpeg processes (video pipe + audio pipe)
       ├─ loop: player.Update(Δ)  where Δ = 1000/max(fps,1000) ms
       │    ├─ every 1 ms of sim time  → ffmpeg.PushAudio()   (BASS decode mixer → pipe)
       │    └─ every 1000/fps ms       → render frame:
       │         fbo.Bind → PreFrame (motion-blur layer / RGB→YUV begin)
       │         → player.Draw (bg → objects → cursors → HUD, bloom)
       │         → MakeFrame (async PBO readback, fence, → video pipe)
       └─ on MapEnd: StopFFmpeg → combine(): ffmpeg -c:v copy -c:a copy → final .mp4/.mkv
```

Three inputs, three parsers:
- **`.osu` file** → `app/beatmap/parser.go` (3 passes: metadata, timing, objects).
- **skin folder + skin.ini** → `app/skin/` (lazy, atlas-packed, 3-level fallback).
- **`.osr` replay** → `github.com/wieku/rplpa` + `app/dance/rcontroller.go` (cursor + keys).

---

## 2. Beatmap parsing

### 2.1 The three passes

The same `.osu` file is scanned up to three times, so DB import stays cheap and curve generation is deferred until playback:

| Pass | Function | Reads | Builds |
|---|---|---|---|
| 1. Import | `ParseBeatMap` (`app/beatmap/parser.go:152`) | `[General] [Metadata] [Difficulty] [Events] [TimingPoints]`, *counts* `[HitObjects]` | metadata, timing points, object counts, `Length` |
| 2. Lazy timing | `ParseTimingPointsAndPauses` (`parser.go:258`) | `[Events]` breaks + `[TimingPoints]` | timing points (when loaded from DB cache) |
| 3. Play/render | `ParseObjects` (`parser.go:303`) | version line, `[Colours]`, `[HitObjects]` | actual `IHitObject`s, combos, slider paths, ticks, stacking |

```
DB import:   ParseBeatMapFile → ParseBeatMap        (no objects)
Play/render: ParseTimingPointsAndPauses (if needed)
             → ParseObjects
                 → CreateObject per line → NewCircle/NewSlider/NewSpinner
                 → sort by start time → combo numbering
                 → obj.SetTiming(timings, version, diffCalcOnly)   ← slider curves + ticks
                 → CalculateStackLeniency → processStacking
Runtime:     beatMap.Reset() → obj.SetDifficulty(diff)             ← sprites, fades, ball
```

### 2.2 `app/beatmap/parser.go` — every function

**Helpers**
- `tokenize(line, delimiter) []string` (:118) → `tokenizeN(line, delimiter, -1)`.
- `tokenizeN(line, delimiter, n)` (:122) — nil for `//` comments or missing delimiter; `strings.SplitN` + trim each token.
- `getSection(line) string` (:136) — strips `[`/`]` from section headers.
- `bufferPool` (:145) — `sync.Pool` of 10 MiB scan buffers (`bufferSize = 10*1024*1024`).

**`parseGeneral(line, beatMap) bool`** (:22) — `[General]`, split on `:`:

| Key | Target |
|---|---|
| `Mode` | `beatMap.Mode` (int64) |
| `StackLeniency` | `beatMap.StackLeniency` (NaN→0.0) |
| `AudioFilename` | `beatMap.Audio` |
| `PreviewTime` | `beatMap.PreviewTime` |
| `SampleSet` | `beatMap.Timings.BaseSet` (`Normal`/`All`→1, `Soft`/`None`→2, `Drum`→3) |

**`parseMetadata(line, beatMap)`** (:49) — `[Metadata]`: `Title→Name`, `TitleUnicode→NameUnicode`, `Artist`, `ArtistUnicode`, `Creator`, `Version→Difficulty` (diff name), `Source`, `Tags`, `BeatmapID→ID`, `BeatmapSetID→SetID`.

**`parseDifficulty(line, beatMap)`** (:74) — `[Difficulty]`; AR/CS/OD/HP clamped to [0,10] into `beatMap.Diff`:

| Key | Action |
|---|---|
| `SliderMultiplier` | `beatMap.SliderMultiplier` **and** `Timings.SliderMult` |
| `ApproachRate` | `Diff.SetAR`, sets `ARSpecified=true` |
| `CircleSize` | `Diff.SetCS` |
| `SliderTickRate` | `Timings.TickRate` |
| `HPDrainRate` | `Diff.SetHP` |
| `OverallDifficulty` | `Diff.SetOD`; **if no AR was specified, AR := OD** (pre-AR-era maps) |

**`parseEvents(line, beatMap)`** (:101) — `[Events]`, split on `,`: `Background`/`0` → `beatMap.Bg` (quotes stripped); `Break`/`2` → `beatMap.Pauses += NewPause(line)`.

**`parseHitObjects(line, beatMap)`** (:110) — `objects.CreateObject(line)`, append if non-nil. Only called by `ParseObjects`.

**`ParseBeatMap(beatMap) error`** (:152) — opens `<SongsDir>/<Dir>/<File>` via `files.NewScanner`. For `[HitObjects]` it only counts (`Circles`/`Sliders`/`Spinners`; LONGNOTE counts as slider) and computes `Length = max(objectTime)` (time at index 2 for circle/slider, 5 for spinner, `split(arr[5],":")[0]` for holds). For `[TimingPoints]` it calls `beatMap.ParsePoint(line)`. Ends with `FinalizePoints()`. Returns `"corrupted file"` if `Name+Artist+Creator == ""` or zero timing points.

**`ParseBeatMapFile(file) *BeatMap`** (:241) — `NewBeatMap()` + set `Dir`/`File` + `ParseBeatMap`.

**`ParseTimingPointsAndPauses(beatMap)`** (:258) — early-out if `Timings.HasPoints()`; re-scan only breaks + timing points.

**`ParseObjects(beatMap, diffCalcOnly, parseColors bool)`** (:303):
1. `osu file format vN` → `beatMap.Version`.
2. `[Colours]`: if `parseColors` → `skin.AddBeatmapColor(arr)`, then `skin.FinishBeatmapColors()`.
3. `[HitObjects]` → `CreateObject`.
4. `slices.SortStableFunc` by `GetStartTime()`.
5. **Combo numbering** (:362): tracks `comboNumber`, `comboSet`, `comboSetHax`, `forceNewCombo`. Spinners force new combo on next object. On new combo: `SetNewCombo(true)`, `comboNumber=1`, `comboSet++`, `comboSetHax += ColorOffset+1` (colour-skip bits); previous object gets `SetLastInCombo(true)`. Also sets `SetID`, `SetComboNumber/Set/SetHax`, `SetStackLeniency`.
6. `obj.SetTiming(beatMap.Timings, beatMap.Version, diffCalcOnly)` per object — slider path/tick generation happens here.
7. If `settings.Objects.StackEnabled || KNOCKOUT || PLAY || diffCalcOnly` → `beatMap.CalculateStackLeniency(beatMap.Diff)`.

### 2.3 `BeatMap` struct (`app/beatmap/beatmap.go:18`) — all fields

| Field | Type | Meaning |
|---|---|---|
| `Artist` / `ArtistUnicode` | string | romanised / original artist |
| `Name` / `NameUnicode` | string | title |
| `Difficulty` | string | diff name (`Version:` in file) |
| `Creator`, `Source`, `Tags` | string | metadata |
| `Mode` | int64 | game mode (0 = osu!std) |
| `SliderMultiplier` | float64 | base slider velocity |
| `StackLeniency` | float64 | default 0.7 |
| `Diff` | `*difficulty.Difficulty` | AR/OD/CS/HP + derived values |
| `Dir`, `File` | string | folder (rel. to Songs) + filename |
| `Audio`, `Bg` | string | audio / background filenames |
| `MD5` | string | file hash |
| `SetID`, `ID` | int64 | beatmap set/map IDs |
| `LastModified, TimeAdded, PlayCount, LastPlayed, PreviewTime` | int64 | DB/stat fields |
| `Stars` | float64 | star rating (-1 = uncalculated) |
| `StarsVersion` | int | SR algorithm version |
| `Length` | int | last object time (ms) |
| `Circles, Sliders, Spinners` | int | object counts |
| `MinBPM` / `MaxBPM` | float64 | init +Inf / 0 |
| `Timings` | `*objects.Timings` | timing-point container |
| `HitObjects` | `[]objects.IHitObject` | parsed objects |
| `Pauses` | `[]*Pause` | breaks |
| `Queue` | `[]objects.IHitObject` | runtime spawn queue (from `Reset`) |
| `processed` | `[]objects.IHitObject` | (unexported) active objects |
| `Version` | int | .osu format version |
| `ARSpecified` | bool | AR field present in file |
| `LocalOffset` | int | per-map audio offset |
| `pathCache` | `*files.FileMap` | (unexported) case-insensitive file lookup |
| `stackCalcCache` | `map[int64]bool` | (unexported) stacking memo per threshold |

Key methods: `NewBeatMap()` (:73 — Diff `NewDifficulty(5,5,5,5)`, StackLeniency 0.7, default 60 BPM timing); `Reset()` (:87 — rebuild Queue, `SetDifficulty` on every object); `Update(time)` (:102 — spawn at `startTime - Preempt`, finalize at `endTime + HitFadeOut + Hit50`); `ParsePoint(point)` (:152); `FinalizePoints()` (:205); `CalculateStackLeniency(diff)` (:234 — `stackThreshold = floor(Preempt * StackLeniency)`, memoised); `GetRelatedFile`/`GetAudioFile` (case-insensitive via FileMap).

### 2.4 Difficulty (`app/beatmap/difficulty/`) — CS/AR/OD/HP → runtime values

`Difficulty` struct stores base stats (`baseAR/baseOD/baseCS/baseHP`), working mod-adjusted stats (`ar/od/cs/hp`), and derived quantities: `PreemptU/Preempt`, `TimeFadeIn`, `CircleRadiusU/CircleRadius`, `CircleScaleL/CircleRadiusL` (lazer), `Hit50U/Hit100U/Hit300U` + int64 versions, `HPMod`, `SpinnerRatio`, `LzSpinnerMinRPS/MaxRPS`, `Speed`, `BaseModSpeed`, `ARReal`, `ODReal`, `Mods` bitmask, `modSettings`.

Constants (`difficulty.go:14`): `HitFadeIn=400`, `HitFadeOut=240`, `HittableRange=400`, `ResultFadeIn=120`, `ResultFadeOut=600`, `PostEmpt=500`.

**The core interpolator** (`difficulty.go:643`):
```go
func DifficultyRate(diff, minV, midV, maxV float64) float64 {
    diff = float64(float32(diff))          // float32 cast first (stable quirk)
    if diff > 5 { return midV + (maxV-midV)*(diff-5)/5 }
    if diff < 5 { return midV - (midV-minV)*(5-diff)/5 }
    return midV
}
// DiffFromRate = inverse
```

**`calculate()` (`difficulty.go:96`) — exact formulas:**
```
HardRock: ar=min(ar*1.4,10)  cs=min(cs*1.3,10)  od=min(od*1.4,10)  hp=min(hp*1.4,10)
Easy:     ar/=2  cs/=2  od/=2  hp/=2

CircleRadiusU = DifficultyRate(cs, 54.4, 32, 9.6)      // px @ CS 0/5/10
CircleRadius  = CircleRadiusU * 1.00041                // osu's "weird allowance"
CircleScaleL  = (1.0 - 0.7*(cs-5)/5)/2 * 1.00041       // lazer, float32
CircleRadiusL = CircleScaleL * 64

PreemptU   = DifficultyRate(ar, 1800, 1200, 450)       // ms @ AR 0/5/10
Preempt    = floor(PreemptU)
TimeFadeIn = 400 * min(1, PreemptU/450)

Hit50U  = DifficultyRate(od, 200, 150, 100)            // half-window ms
Hit100U = DifficultyRate(od, 140, 100, 60)
Hit300U = DifficultyRate(od, 80, 50, 20)

SpinnerRatio    = DifficultyRate(od, 3, 5, 7.5)
LzSpinnerMinRPS = DifficultyRate(od, 90, 150, 225)/60
LzSpinnerMaxRPS = DifficultyRate(od, 250, 380, 430)/60

DT → BaseModSpeed=1.5 ; HT → 0.75 ; else 1  (custom rate via SpeedSettings)
ARReal = DiffFromRate(PreemptU/Speed, 1800, 1200, 450)
ODReal = (80 - Hit300U/Speed) / 6
GetModifiedTime(t) = t / Speed
```
`GetRadius()`: Lazer→`CircleRadiusL`; Autopilot→100; else `CircleRadius`.

**Mods** (`mods.go`, bitmask `1<<(iota-1)`): NoFail, Easy, TouchDevice, Hidden, HardRock, SuddenDeath, DoubleTime, Relax, HalfTime, Nightcore, Flashlight, Autoplay, SpunOut, Relax2 (autopilot), Perfect, Key mods, FadeIn, Random, Cinema, Target, ScoreV2, plus extensions Daycore, Lazer, Classic, DifficultyAdjust, Mirror, Traceable. NC implies DT; Perfect implies SD; Daycore implies HT. `SetMods/AddMod/SetMods2([]rplpa.ModInfo)` install per-mod config structs (`DiffAdjustSettings`, `SpeedSettings`, `EasySettings`, `FlashlightSettings`, `ClassicSettings`, `MirrorSettings`) then re-`calculate()`.

### 2.5 Hit object types (`app/beatmap/objects/`)

**Type flags** (`util.go:27`): `CIRCLE=1, SLIDER=2, NEWCOMBO=4, SPINNER=8, LONGNOTE=128`. `CreateObject(data)`: CIRCLE→`NewCircle`, SPINNER→`NewSpinner` (only if `Objects.LoadSpinners || KNOCKOUT || PLAY`), SLIDER→`NewSlider` (nil if invalid).

**Common parse** (`commonParse(data, extraIndex)`, `baseobject.go`):
```
data[0],data[1] → StartPosRaw/EndPosRaw (Vector2f)
data[2]         → StartTime = EndTime
data[3]         → objType; NewCombo = (objType&4)!=0; ColorOffset = (objType>>4)&7
BasicHitSound   = parseExtras(data, extraIndex)   // sampleSet:additionSet:customIndex:volume
```

**`HitObject` base** (`hitobject.go:64`): `StartPosRaw, EndPosRaw, StartTime, EndTime, StackLeniency, StackIndexMap map[int64]int64, PositionDelegate, HitObjectID, LastInCombo, NewCombo, ComboNumber, ComboSet, ComboSetHax, ColorOffset, BasicHitSound, audioSubmissionDisabled`.

`ModifyPosition(hitObject, basePos, diff)` (:207) — HR/Mirror flips + stack offset:
```
hFlip: X = 512-X ;  vFlip: Y = 384-Y     (vFlip = HardRock XOR mirror-vertical)
stackOffset(stable)         = stackIndex * CircleRadius / 10
stackOffset(lazer/diffcalc) = stackIndex * CircleScaleL * 6.4
pos -= (stackOffset, stackOffset)
```

**Circle** (`circle.go`): `NewCircle` (:53) — `commonParse(data,5)`, `sample=int(data[4])`, texture base `"hit"`. `SetTiming` stores Timings. `SetDifficulty` (:143) builds sprites and fade/scale transforms from `Preempt/TimeFadeIn/Hit100/Hit50`; approach circle scales 4→1 over `[startTime,endTime]`; Hidden/Traceable variants handled here. Special constructors: `DummyCircle`, `DummyCircleInherit`, `NewSliderEndCircle` (slider head/tail/reverse visuals).

**Spinner** (`spinner.go`): `NewSpinner` (:71) — `commonParse(data,6)`, `EndTime=data[5]`. `rpms = 0.00795` auto-spin constant. Approach circle 1.9→0.1 over the spin.

**Slider** (`slider.go`) — key fields: `multiCurve *curves.MultiCurve`, `scorePath []PathLine` (stable time-parameterised segments), `TPoint TimingPoint`, `pixelLength`, `partLen`, `RepeatCount`, per-edge sample arrays, `TickPoints/TickReverse/ScorePoints []TickPoint`, lazer `EndTimeLazer/ScorePointsLazer/spanDuration`. Constants: `maxPathLength=100_000_000`, `maxRepeats=10_000`.

`NewSlider(data)` (:105):
```
commonParse(data, 10)
pixelLength = data[7];  RepeatCount = data[6]
reject if pixelLength*RepeatCount > maxPathLength*10;  clamp both
multiCurve = parseCurve(data[5])          // nil → invalid slider → nil
if pixelLength==0: pixelLength = multiCurve.GetLength()
baseSample = data[4]
data[8] "|"-split → per-edge hitsound bitmasks
data[9] "|"-split ":"-split → per-edge sampleSet:additionSet
```

`parseCurve(curveData)` (:183): splits `B|200:100|...` on `|`; first token = curve type via `tryGetType`: **`P`→CCirArc (perfect circle), `L`→CLine (linear), `B`→CBezier, `C`→CCatmull**; default Catmull when absent (old maps). Handles lazer multi-segment sliders (repeated anchor → new segment type). Skips first anchor if equal to start pos. Rejects beziers with `controlDistance >= 2*maxPathLength`. Returns `curves.NewMultiCurveT(defs, pixelLength)` (length-truncated).

`SetTiming(timings, version, diffCalcOnly)` (:360):
```
TPoint = timings.GetPointAt(StartTime)
calculateFollowPointsLazer(version)          // always
if diffCalcOnly: return
calculateFollowPointsStable(version)         // memory-heavy scorePath
```

**Velocity/tick math** — lazer (:373):
```
velocity        = 100 * SliderMult / TPoint.GetBeatLength()
scoringDistance = velocity * TPoint.GetBaseBeatLength()
tickDistance    = scoringDistance / TickRate * (version<8 ? 1/GetRatio2() : 1)
EndTimeLazer    = StartTime + RepeatCount * curveLengthLazer / velocity
spanDuration    = (EndTimeLazer - StartTime) / RepeatCount
```
Stable (:433) walks `multiCurve.GetLines()` forward/backward per span building `scorePath []PathLine{Time1,Time2,Linear}` with `progress(ms) = 1000*distance/velocity`, emits `TickPoints/ScorePoints` every `tickDistance` (capped 32768/repeat, suppressed within `velocity*0.01` of the tail), reverse/last markers per span end, `partLen = (EndTime-StartTime)/RepeatCount`. `PositionAt(time)` binary-searches `scorePath` and lerps. `IsRetarded()` (:645): empty path or zero-length in time.

`SetDifficulty` (:561): snake-in/out gliders, head `DummyCircle`, per-repeat `NewSliderEndCircle` reverse arrows, tick sprites, and the **generated** body: `sliderrenderer.NewBody(multiCurve, vFlip, hFlip, CircleRadius)`.

### 2.6 Timing points (`app/beatmap/objects/timing.go`)

`TimingPoint` (:9): `Time`, `beatLengthBase` (inherited red-line ms/beat), `beatLength` (raw; **negative = green-line SV**), `SampleSet`, `SampleIndex`, `SampleVolume`, `Signature`, `Inherited`, `Kiai`, `OmitFirstBarLine`.
- `GetRatio()`: `beatLength>=0 || NaN → 1.0`, else `clamp(-beatLength,10,1000)/100` (SV clamp 0.1–10×).
- `GetBeatLength() = beatLengthBase * GetRatio()` (SV-scaled effective beat length).

`Timings` (:55): `SliderMult`, `TickRate`, `points` (all), `originalPoints` (red only), `BaseSet`, default point 60 BPM.
- `FinalizePoints()` (:104): stable-sort by time; **green lines copy `beatLengthBase` from the previous point**; reds also go to `originalPoints`.
- `GetPointAt(time)` — binary search last point ≤ time.
- `GetScoringDistance() = 100 * SliderMult / TickRate`.
- `GetVelocity(point) = GetScoringDistance()*TickRate * (1000/beatLength if beatLength>=0)`.
- `GetTickDistance(point) = GetScoringDistance() / point.GetRatio()`.

`BeatMap.ParsePoint(line)` (`beatmap.go:152`) — line = `time,beatLength,meter,sampleSet,sampleIndex,volume,uninherited,effects`:
```
[0] time    [1] beatLength (>=0 → BPM = 60000/beatLength, updates Min/MaxBPM)
[2] meter (0→4)   [3] sampleSet   [4] sampleIndex   [5] volume/100
[6] uninherited flag → inherited = (value==0)   // 0 = green line
[7] effects: kiai = bit0; omitFirstBarLine = bit3
```

### 2.7 Stacking (`app/beatmap/stackleniency.go`)

`stackDistance = 3.0` px. `processStacking(hitObjects, version, diff, stackLeniency)` (:23): `stackThreshold = floor(Preempt * stackLeniency)`; `version >= 6` → `applyNewStacking` (:43, ppy's OsuBeatmapProcessor port: backwards scan for start-start / sliderend-start proximity within threshold, second pass redistributes slider-tail stacks), else `applyOldStacking` (:148, simple forward scan). Spinners always reset to stack 0. Result stored per-threshold in `StackIndexMap`, applied as pixel offset in `ModifyPosition` (§2.5).

### 2.8 Breaks (`app/beatmap/pause.go`)

`Pause{StartTime, EndTime float64}`; `NewPause(data)`: start=`data[1]`, end=`data[2]`.

### 2.9 Slider curve math (`framework/math/curves/`)

`CType`: `CLine=0, CBezier=1, CCirArc=2, CCatmull=3`.

**`MultiCurve`** (`multicurve.go:26`): `sections []float32` (cumulative lengths), `lines []Linear` (flattened polyline, stable), `points []Vector2f` + `cumLength []float64` (lazer). `NewMultiCurveT(defs, desiredLength)` (:103) truncates/extends the flattened curve to exactly the slider's pixel length. Flattening per type:
- `processLinear` (:315) — copy, skip duplicate consecutive anchors.
- `processBezier` (:327) — split at repeated anchors into sub-beziers → adaptive `ApproximateBezier` (`BezierApproximator`).
- `processPerfect` (:301) — >3 points → treat as bezier; collinear (`IsStraightLine32`) → linear; else circular-arc approximation (`ApproximateCircularArc(p1,p2,p3, 0.125)` stable / `ApproximateCircularArcLazer` with `segments = ceil(totalAngle / (2*acos(1 - 0.1/r)))`).
- `processCatmull` (:361) — 4-point windows → `ApproximateCatmullRom(pts, 50)` (51 samples per window).

**`CirArc`** (`cirarc.go`): circumcentre formula, radius, start/total angle, direction; `Unstable=true` if collinear → line fallback; has a bit-exact "float87" stable path (`osuPi=3.14159274`). `GetLength()=r*totalAngle`.
**`Bezier`**: Bernstein-weighted sum; length by subdivision.
**`Catmull`**: standard cubic Catmull-Rom basis (factor 0.5), exactly 4 points.
**`Linear`**: lerp; `GetLength87` replicates osu!stable 32-bit quirks.

### 2.10 Beatmap discovery (`app/database/manager.go`)

`importMaps` (:220) walks `SongsDir` with `files.WalkDir` (one folder level per set; root-level .osu skipped), compares mod-times against the SQLite DB, imports new/changed maps through 4 workers calling `ParseBeatMapFile` + MD5 hash. Object/curve generation is always deferred to play time.

---

## 3. Skin loading

### 3.1 Sources & fallback chain (`app/skin/skin.go`)

```go
type Source int
const ( UNKNOWN=0; LOCAL=2; FALLBACK=4; SKIN=8; BEATMAP=16; ALL=LOCAL|FALLBACK|SKIN|BEATMAP )
```
Priority for lookups (high→low): **SKIN (CurrentSkin folder) → FALLBACK (FallbackSkin folder) → LOCAL (embedded `assets/default-skin`)**. `BEATMAP` exists in the enum but is stripped from every texture lookup (`source &= ^BEATMAP`) — **beatmap-folder images are never used as skin textures**; beatmap contribution is hitsounds + combo colours only (§3.6).

Package state: one 2048×2048 `TextureAtlas` (unit 27; mipmaps only when `settings.RECORD`), per-source region caches (`skinCache/fallbackCache/defaultCache`), `animationCache`, `fontCache`, `sampleCache`, `sourceCache` (region→source), case-insensitive `FileMap`s for the skin/fallback folders, `info *SkinInfo` (doubles as init flag), mutexes for font/sound/texture.

**Load flow** — `checkInit()` (lazy, from every accessor) → `tryLoadSkin(settings.Skin.CurrentSkin, settings.Skin.FallbackSkin)`:
- `"default"` → `loadDefault()` (embedded `assets/default-skin/skin.ini`).
- Folder = `settings.General.GetSkinsDir()/<name>`. Missing dir → recurse to fallback → default. Present but no skin.ini → `newDefaultInfo()` (defaults, latest version). Corrupt skin.ini → recurse to fallback.

**Exported functions:**
- `GetInfo() *SkinInfo`
- `GetFont(name) *font.Font` — `"default"`→HitCirclePrefix/Overlap, `"score"`→ScorePrefix/Overlap, `"combo"`→ComboPrefix/Overlap, other→name/0. Glyphs `0-9`, `-comma -dot -percent -x`.
- `GetTexture(name)` = `GetTextureSource(name, ALL)`.
- `GetTextureSource(name, source)` — strips BEATMAP; prunes SKIN/FALLBACK bits when current skin is default or fallback is redundant; tries SKIN→FALLBACK→LOCAL, memoises misses, records source.
- `GetFrames(name, useDash) []*TextureRegion` — animation loader: if `<name><dash>0` exists and beats the single texture in specificity, collects `<name><dash>N` frames **restricted to the same source** (no cross-skin frame mixing); else single-frame; else empty.
- `GetMostSpecific(rg1, rg2)` — higher-priority-source wins (used e.g. to decide whether `sliderstartcircle` overrides `hitcircle`).
- `GetSource(name)` / `GetSourceFromTexture(rg)` — used to lock companion textures to the same layer (cursormiddle→cursor, sliderb-nd/-spec→sliderb, particleN→hitN).
- `GetSample(name) *bass.Sample` — SKIN→FALLBACK→LOCAL; extensions tried `.wav, .ogg, .mp3`.
- `AddBeatmapColor(data)` / `FinishBeatmapColors()` / `GetColors()` / `GetColor(comboSet, comboSetHax, base)`.

**@2x handling** (`loadTexture`): tries `<base>@2x<ext>` first; if present, logical size = pixel size / 2. Textures ≤1000×1000 packed in the atlas, larger ones uploaded as single textures.

### 3.2 skin.ini (`app/skin/info.go`) — `SkinInfo` struct, all fields

`latestVersion = 2.7`. Parsing is key-based (section headers ignored), `tokenize(line, ":")`, `//` comments stripped. **If the `Version` key is absent → Version = 1.0**; `"latest"` → 2.7; no skin.ini at all → 2.7.

| Field | Default | .ini key |
|---|---|---|
| `Name` / `Author` | `""` | Name / Author |
| `Version` | 2.7 (absent key → 1.0) | Version |
| `AnimationFramerate` | -1 | AnimationFramerate |
| `SpinnerFadePlayfield` | true | SpinnerFadePlayfield |
| `SpinnerNoBlink` | false | SpinnerNoBlink |
| `SpinnerFrequencyModulate` | true | SpinnerFrequencyModulate |
| `LayeredHitSounds` | true | LayeredHitSounds |
| `CursorCentre` / `CursorExpand` / `CursorRotate` | true | Cursor* |
| `ComboColors` | osu defaults: (255,192,0) (0,202,0) (18,124,255) (242,24,57) | Combo1…Combo8 |
| `DefaultSkinFollowpointBehavior` | false | DefaultSkinFollowpointBehavior |
| `SliderBallTint` | false | AllowSliderBallTint |
| `SliderBallFlip` | false | SliderBallFlip |
| `SliderBorder` | white | SliderBorder |
| `SliderTrackOverride` | nil | SliderTrackOverride |
| `SliderBall` | nil | SliderBall |
| `SongSelectInactiveText` / `ActiveText` | white / black | SongSelect*Text |
| `InputOverlayText` | black | InputOverlayText |
| `HitCirclePrefix` | "default" | HitCirclePrefix |
| `HitCircleOverlap` | -2 | HitCircleOverlap |
| `HitCircleOverlayAboveNumber` | false | both spellings (`…Number`/`…Numer`) accepted |
| `ScorePrefix` / `ScoreOverlap` | "score" / 0 | Score* |
| `ComboPrefix` / `ComboOverlap` | "score" / 0 | Combo* |

Not parsed: combo bursts (explicitly skipped), slider style, `MenuGlow`, `SpinnerBackground`. `ParseColor` ignores alpha. `GetFrameTime(frames)`: `AnimationFramerate>0 ? 1000/AnimationFramerate : 1000/frames`.

**Version-gated behavior** (circle.go): `Version < 2` → reverse-arrow pulse rotation ±6°, hit-explosion end scale 1.8 (vs 1.4), combo number scales+fades with explosion (vs quick 60 ms fade).

### 3.3 Which elements pull which skin assets

| Element | Assets |
|---|---|
| Hit circle | `hitcircle`, `hitcircleoverlay`, `sliderstartcircle(overlay)`, `sliderendcircle(overlay)` (via GetMostSpecific), `hitcircle-full` (mandala), combo number font (`HitCirclePrefix`), `approachcircle`, `reversearrow` |
| Slider | body **generated** by `sliderrenderer` (colours: `SliderBorder`/`SliderTrackOverride` when UseColorsFromSkin, else `Objects.Colors.Sliders.*`); `sliderb` (anim) + `sliderb-nd` + `sliderb-spec` (source-locked); `sliderfollowcircle` (anim); `sliderscorepoint` |
| Spinner | style auto-detect: `spinner-background` exists → old style (`spinner-background/metre/circle`), else new (`spinner-glow/bottom/top/middle2/middle`); both: `spinner-approachcircle`, `spinner-clear`, `spinner-spin`, `spinner-rpm` |
| Hit results | `hit0/50/100/100k/300/300k/300g` (anims), `particle50/100/300` (source-locked, 150 additive particles), `lighting` (combo-tinted, needs `Gameplay.ShowHitLighting`) |
| HUD | `scorebar-bg/-colour(anim)/-marker/-ki/-kidanger/-kidanger2`, `ranking-*`, `section-pass/fail`, `circularmetre` (forced LOCAL), `inputoverlay-background/-key`, `play-skip`, mod icons, `arrow-warning` |
| Fonts | score/acc/rpm: `ScorePrefix`; combo: `ComboPrefix`; circle numbers: `HitCirclePrefix`; scoreboard: `scoreentry-*` |
| Cursor (UseSkinCursor=true) | `cursor`, `cursortrail`, `cursormiddle` (source-locked); CursorCentre/Expand/Rotate honored; long connected trail when `cursormiddle` exists or `ForceLongTrail`; else 16.67 ms sprite trail |
| Cursor (default) | shader cursor (`assets/shaders/cursortrail.*`), no skin textures |
| Both cursors | `cursor-ripple`, `cursor-smoke` |

### 3.4 Hitsounds (`app/audio/osuaudio.go`)

Sets `normal=1, soft=2, drum=3`; sounds `hitnormal=1, hitwhistle=2, hitfinish=3, hitclap=4, slidertick=5, sliderslide=6, sliderwhistle=7`. `LoadSamples()` preloads the 3×7 grid via `skin.GetSample("<set>-<sound>")`. `PlaySample(...)`: `hitnormal` plays if `LayeredHitSounds` OR bit1 set OR hitsound==0; whistle/finish/clap from the *addition* set per bits 2/4/8; volume floor 0.08; positional balance via `Audio.HitsoundPositionMultiplier` (DIVIDES==1 only). Slider loops: `sliderslide`/`sliderwhistle`; ticks one-shot. `spinnerspin` (rate modulated by `SpinnerFrequencyModulate`), `spinnerbonus`, `failsound` also via skin.

### 3.5 Combo colour resolution (`skin.GetColor`)

```
if Skin.UseColorsFromSkin && colors exist:
    colors = UseBeatmapColors && beatmap has colours ? beatmapColors : skin ComboColors
    pick colors[(UseBeatmapColors ? comboSetHax : comboSet) % len]
else if Objects.Colors.{UseComboColors|UseSkinComboColors|UseBeatmapComboColors}:
    beatmap colours (if enabled & present) → else skin colours (if enabled) → else HSV list
else: base (rainbow/HSV colour)
```

### 3.6 Beatmap-provided elements

- **Textures: none** (BEATMAP source stripped).
- **Hitsounds: yes** — `audio.LoadBeatmapSamples(fileMap)` scans the map folder for `<set>-<sound><index>.{wav,ogg,mp3}` → `MapSamples[3][7]map[int]*Sample`; preferred over skin samples at play time (unless `Audio.IgnoreBeatmapSamples`), keyed by custom sample index from timing points / object extras.
- **Combo colours: yes** — `[Colours]` lines → `skin.AddBeatmapColor` (§3.5).

---

## 4. Every changeable variable

### 4.1 Config files

- `env.ConfigDir()/default.json` (or `<name>.json` via `-settings <name>`); JSON with tab indent; unknown/missing keys keep compiled defaults (unmarshal over a `NewConfigFile()` base). Legacy `settings*.json` in DataDir are auto-migrated.
- Hot reload via fsnotify (disabled in record mode). `-sPatch '<json>'` overlays a JSON patch. `credentials.json` is separate (osu!api ClientId/ClientSecret/AuthType=ClientCredentials/CallbackPort=8294).
- Non-persisted runtime globals (set by flags): `DEBUG, PLAY, SKIP, START, END, KNOCKOUT, PLAYERS, DIVIDES, SPEED, PITCH, TAG, RECORD, REPLAY, LOCALOFFSET, JsonPatch`.

### 4.2 `General`
| Field | Default |
|---|---|
| OsuSongsDir / OsuSkinsDir / OsuReplaysDir | `<osu base>/Songs`, `/Skins`, `/Replays` (Win: registry-discovered osu! install; else `~/.osu`) |
| DiscordPresenceOn | true |
| UnpackOszFiles | true |
| VerboseImportLogs | false |

### 4.3 `Graphics`
| Field | Default | Notes |
|---|---|---|
| Width / Height | 1920 / 1080 | overwritten with monitor size on first run |
| WindowWidth / WindowHeight | 1280 / 720 | **record mode forces these to Recording.FrameWidth/Height** |
| Fullscreen | true | forced false in record |
| VSync | false | forced false in record |
| FPSCap | 0 | 0=off; negative = multiple of monitor Hz |
| MSAA | 0 | 0/2/4/8/16 |
| ShowFPS | true | forced false in record |
| Experimental.UsePersistentBuffers | false | persistent-mapped quad batch |

### 4.4 `Audio`
GeneralVolume 0.5, MusicVolume 0.5, SampleVolume 0.5, Offset 0 (ms; **not** applied to recordings — use `-offset`), OnlineOffset false, HitsoundPositionMultiplier 1.0, IgnoreBeatmapSamples false, IgnoreBeatmapSampleVolume false, PlayNightcoreSamples true, BeatScale 1.2, BeatUseTimingPoints false. `Linux/Unix`: BassPlaybackBufferLength 100, BassDeviceBufferLength 10, BassUpdatePeriod 5, BassDeviceUpdatePeriod 10.

### 4.5 `Input`
LeftKey "Z", RightKey "X", RestartKey "`", SmokeKey "C", ScreenshotKey "F2", MouseButtonsDisabled true, MouseHighPrecision false, MouseSensitivity 1.

### 4.6 `Gameplay` (HUD)
Shared shapes: `hudElement{Show,Scale,Opacity}`, `+Offset{XOffset,YOffset}`, `+Position{XPosition,YPosition}`.

- **HitErrorMeter** (Show true): PointFadeOutTime 10, ShowPositionalMisses true, PositionalMissScale 1.5, ShowUnstableRate true, UnstableRateDecimals 0, UnstableRateScale 1, StaticUnstableRate false, ScaleWithSpeed false.
- **AimErrorMeter** (Show false, pos 1350×650): PointFadeOutTime 10, DotScale 1, Align "Right", ShowUnstableRate false, CapPositionalMisses true, AngleNormalized false.
- **Score** (Show true): ProgressBar "Pie", ShowGradeAlways false, StaticScore/StaticAccuracy false.
- **HpBar** (Show true), **KeyOverlay** (Show true).
- **ComboCounter** (Show true): Static false.
- **PPCounter** (Show true, pos 5×150): Color HSV{0,0,1}, Decimals 0, Align "CentreLeft", ShowInResults true, ShowPPComponents false, Static false.
- **HitCounter** (Show true, pos 5×190): Color300/100/50/Miss/SB all white, Spacing 48, FontScale 1, Align/ValueAlign "Left", Vertical false, Show300 false, ShowSliderBreaks false.
- **StrainGraph** (Show true, 5×310, 130×70, BgColor {0,0,0.2}, FgColor {297,0.4,0.92}, Outline off/Width 2).
- **ScoreBoard** (Show true): Mode "Normal", ModsOnly false, AlignRight false, HideOthers false, ShowAvatars false, ExplosionScale 1.
- **Mods** (Show true): HideInReplays false, FoldInReplays false, ShowLazerMod true, AdditionalSpacing 0.
- **Boundaries**: Enabled true, BorderThickness 1, BorderFill 1, BorderColor white, BorderOpacity 1, BackgroundColor {0,1,0}, BackgroundOpacity 0.5.
- **Underlay**: Path "", AboveHpBar false.
- **Statistics**: template overlays (Template "{{.pp}}PP", pos 100×100, Size 24).
- Scalars: SBFont "", HUDFont "", ShowResultsScreen true, ResultsScreenTime 5, ResultsUseLocalTimeZone false, ShowWarningArrows true, ShowHitLighting false, FlashlightDim 1, PlayUsername "Guest", IgnoreFailsInReplays false, PPVersion "latest", LazerClassicScore false.

### 4.7 `Skin`
CurrentSkin "default", FallbackSkin "default", UseColorsFromSkin false, UseBeatmapColors false; Cursor: UseSkinCursor false, Scale 1, TrailScale 1, ForceLongTrail false, LongTrailLength 2048, LongTrailDensity 1.

### 4.8 `Cursor`
TrailStyle 1 (1 unified / 2 distance-rainbow / 3 time-rainbow / 4 gradient), Style23Speed 0.18, Style4Shift 0.5, Colors{EnableRainbow true, RainbowSpeed 8, BaseColor {0,1,1}, EnableCustomHueOffset false, HueOffset 0, FlashToTheBeat false, FlashAmplitude 0}, EnableCustomTagColorOffset true, TagColorOffset -36, EnableTrailGlow true, EnableCustomTrailGlowOffset true, TrailGlowOffset -36, ScaleToCS false (unimplemented), CursorSize 12, CursorExpand false, ScaleToTheBeat false, ShowCursorsOnBreaks true, BounceOnEdges false, TrailScale 1, TrailEndScale 0.4, TrailDensity 1, TrailMaxLength 2000, TrailRemoveSpeed 1, GlowEndScale 0.4, SmokeRemoveSpeed 1, InnerLengthMult 0.9, AdditiveBlending true, CursorRipples false, SmokeEnabled true.

### 4.9 `Objects`
DrawApproachCircles true, DrawComboNumbers true, DrawFollowPoints true, LoadSpinners true, ScaleToTheBeat false, StackEnabled true.
- **Sliders**: ForceSliderBallTexture true, DrawEndCircles true, DrawSliderFollowCircle true, DrawScorePoints true, SliderMerge false, BorderWidth 1; Distortions{Enabled true, ViewportSize 0, UseCustomResolution false, 1920×1080}; Snaking{In true, Out true, OutFadeInstant true, DurationMultiplier 0, FadeMultiplier 0}.
- **Colors**: MandalaTexturesTrigger 5, MandalaTexturesAlpha 0.3, Color{rainbow on, speed 8, base {0,1,1}, FlashAmplitude 100}, UseComboColors false, ComboColors [{0,1,1}], UseSkinComboColors false, UseBeatmapComboColors false.
- **Colors.Sliders**: WhiteScorePoints true, ScorePointColorOffset 0, SliderBallTint false; Border{UseHitCircleColor false, base white, EnableCustomGradientOffset true, CustomGradientOffset 0}; Body{UseHitCircleColor true, base {0,1,0}, FlashToTheBeat true, InnerOffset -0.5, OuterOffset -0.05, InnerAlpha 0.8, OuterAlpha 0.8}.

### 4.10 `Playfield`
DrawObjects true, DrawCursors true, Scale 1, OsuShift false, ShiftX/Y 0, ScaleStoryboardWithPlayfield false, MoveStoryboardWithPlayfield false, LeadInTime 5 (forced 0 in record), LeadInHold 2, FadeOutTime 5, SeizureWarning{Enabled true, Duration 5}.
- **Background**: LoadStoryboards true, LoadVideos false, FlashToTheBeat false, Dim{Intro 0, Normal 0.95, Breaks 0.5}, Parallax{Enabled true, Amount 0.1, Speed 0.5}, Blur{Enabled false, Values{0, 0.6, 0.3}}, Triangles{Enabled false, Shadowed true, DrawOverBlur true, ParallaxMultiplier 0.5, Density 1, Scale 1, Speed 1}.
- **Logo**: Enabled true, DrawSpectrum false, Dim{0,1,1}.
- **Bloom**: Enabled false, BloomToTheBeat true, BloomBeatAddition 0.3, Threshold 0, Blur 0.6, Power 0.7.

### 4.11 `CursorDance` (procedural cursor mode)
Movers [{Mover "spline", SliderDance false, RandomSliderDance false}] (options: spline, bezier, circular, linear, axis, aggressive, flower, momentum, exgon, pippi), Spinners [{Mover "circle", CenterOffsetX/Y 0, Radius 100}] (heart/triangle/square/cube/circle), ComboTag false, Battle false, DoSpinnersTogether true, TAGSliderDance false.
MoverSettings: Bezier{Aggressiveness 60, SliderAggressiveness 3}; Flower{AngleOffset 90, DistanceMult 0.666, StreamAngleOffset 90, LongJump -1, LongJumpMult 0.7, LongJumpOnEqualPos false}; HalfCircle{RadiusMultiplier 1, StreamTrigger 130}; Spline{RotationalForce false, StreamHalfCircle true, StreamWobble true, WobbleScale 0.67}; Momentum{SkipStackAngles false, StreamRestrict true, DurationMult 2, DurationTrigger 500, StreamMult 0.7, RestrictAngle 90, RestrictArea 40, RestrictInvert true, DistanceMult 0.6, DistanceMultOut 0.45}; ExGon{Delay 50}; Linear{WaitForPreempt true, ReactionTime 100, ChoppyLongObjects false}; Pippi{RotationSpeed 1.6, RadiusMultiplier 0.98, SpinnerRadius 100}.

### 4.12 `Knockout`
Mode 0 (0 ComboBreak / 1 MaxCombo / 2 XReplays / 3 OneVsOne / 4 SSorQuit), GraceEndTime -10, BubbleMinimumCombo 200, ExcludeMods "", HideMods "", MaxPlayers 50, MinPlayers 1, RevivePlayersAtEnd false, LiveSort true, SortBy "Score", HideOverlayOnBreaks false, MinCursorSize 3, MaxCursorSize 7, SmokeEnabled false, Add false, Name "".

### 4.13 `Recording` — the video config

| Field | Default | Meaning |
|---|---|---|
| FrameWidth / FrameHeight | 1920 / 1080 | output resolution |
| FPS | 60 | target video fps (use MotionBlur for the high-fps look, not huge FPS) |
| EncodingFPSCap | 0 | render/encode speed cap; 0 = as fast as possible |
| Encoder | "libx264" | libx264, libx265, libsvtav1, h264/hevc/av1_nvenc, h264/hevc_qsv, h264/hevc/av1_amf, custom |
| PixelFormat | "yuv420p" | yuv420p / yuv444p / nv12 (qsv forces nv12; libsvtav1 forces yuv420p) |
| Filters / AudioFilters | "" | extra ffmpeg -vf / -af |
| AudioCodec | "aac" | aac / libmp3lame / libopus / flac / custom |
| OutputDir | "videos" | rel. to data dir |
| Container | "mp4" | mp4 / mkv |
| ShowFFmpegLogs | true | |

Per-encoder blocks (each also has `AdditionalOptions` free-form string):
- `libx264`: RateControl "crf" (vbr/cbr/crf), Bitrate "10M", CRF 14, Profile "high", Preset "faster".
- `libx265`: crf, "10M", CRF 18, Profile "main", Preset "fast".
- `libsvtav1`: crf, "10M", CRF 22, Preset "7".
- `h264_nvenc`: RateControl "cq" (vbr/cbr/cqp/cq), "10M", CQ 22, Profile "high", Preset "p7".
- `hevc_nvenc`: cq, "10M", CQ 24, Preset "p7".
- `av1_nvenc`: cbr, "5M", Preset "p7".
- `h264_qsv`: RateControl "icq", "10M", Quality 15, Profile "high", Preset "slow".
- `hevc_qsv`: icq, "10M", Quality 20, Preset "slow".
- `h264_amf`: RateControl "cqp" (cqp/cbr/vbr_peak/qvbr/hqvbr/hqcbr), "10M", CQ 20, Profile "high", Preset "quality".
- `hevc_amf`: cqp, "10M", CQ 22, Preset "quality".
- `av1_amf`: cqp, "10M", CQ 24, Preset "high_quality".
- Audio: aac{Bitrate "192k"}; libmp3lame{RateControl "abr", TargetBitrate "192k"}; libopus{RateControl "vbr", "192k"}; flac{CompressionLevel 12 → adds `-compression_level`, `-sample_fmt s32`, `-bits_per_raw_sample 24`}.

**MotionBlur**: Enabled false, OversampleMultiplier 16 (render at FPS×16), BlendFrames 24, BlendFunctionID 27 (0 Flat … 27 GaussSymmetric … 29 SemiCircle), GaussWeightsMult 1.5.

### 4.14 `Debug`
PerfGraph.MaxRange 1.2.

### 4.15 CLI flags (`app/app.go run()`)

| Flag | Default | Purpose |
|---|---|---|
| `-id` / `-md5` | -1 / "" | beatmap by id/hash (override search) |
| `-artist -a / -title -t / -difficulty -d / -creator -c` | "" | beatmap search |
| `-settings` | "" | load `ConfigDir/<v>.json` instead of default.json |
| `-cursors` | 1 | mirror/mandala copies (→ DIVIDES) |
| `-tag` | 1 | TAG cursor count |
| `-knockout` / `-knockout2 '<json list>'` | false / "" | knockout modes |
| `-speed` / `-pitch` | 1.0 / 1.0 | rate/pitch (speed≠1 becomes a DT/HT speed_change mod) |
| `-mods` / `-mods2` | "" | classic string / lazer JSON (mutually exclusive) |
| `-replay -r` | "" | .osr playback; forces knockout, sets md5 |
| `-play` | false | interactive play mode |
| `-start` / `-end` | 0 / +Inf | render time window (s) |
| `-skip` / `-quickstart` | false | skip intro / also zero lead-in |
| `-record` | false | **video render mode** |
| `-out` | "" | output name; implies `-record` (unless `-ss`) |
| `-ss` | NaN | single-frame screenshot at time (s) |
| `-skin` | "" | temporary CurrentSkin override |
| `-ar -od -cs -hp` | NaN | difficulty overrides (adds DA mod) |
| `-offset` | 0 | audio offset ms, **applies to recordings** |
| `-preciseprogress` | false | 1% progress logging |
| `-sPatch` | "" | JSON settings patch |
| `-nodbcheck / -noupdatecheck / -debug / -gldebug` | false | misc |

Incompatible combos panic: record+play, replay+play, knockout+play, replay+knockout, ss+play, ss+record.

---

## 5. The record/render pipeline

### 5.1 Record-mode forcing (`app/app.go:428`)

```go
if settings.RECORD {
    Graphics.VSync = false; Graphics.ShowFPS = false; DEBUG = false
    Graphics.Fullscreen = false
    Graphics.WindowWidth  = Recording.FrameWidth
    Graphics.WindowHeight = Recording.FrameHeight
    Playfield.LeadInTime = 0
}
// window created but never shown; Discord RPC skipped; bass.Init(offscreen=true)
```

### 5.2 The fixed-timestep frame loop (`mainLoopRecord`, `app/app.go:618`)

```go
fps := Recording.FPS                             // e.g. 60
if MotionBlur.Enabled { fps *= OversampleMultiplier }   // render subframes
audioFPS := 1000.0
fbo = buffer.NewFrameMultisampleScreen(w, h, false, 0)  // offscreen target
ffmpeg.StartFFmpeg(fps, w, h, audioFPS, output)

updateFPS  := max(fps, 1000)        // simulation always ≥ 1000 Hz
updateDelta := 1000 / updateFPS     // ms per Update
fpsDelta    := 1000 / fps           // ms per rendered (sub)frame
audioDelta  := 1.0                  // ms per audio push

for !player.Update(updateDelta) {   // returns true at MapEnd — WALL CLOCK NEVER CONSULTED
    deltaSumA += updateDelta
    for deltaSumA >= audioDelta { ffmpeg.PushAudio(); deltaSumA -= audioDelta }

    deltaSumF += updateDelta
    if deltaSumF >= fpsDelta {
        onMainThread {
            fbo.Bind()
            ffmpeg.PreFrame()       // motion-blur layer bind / RGB→YUV begin
            pushFrame()             // player.Draw(0)
            ffmpeg.MakeFrame()      // async PBO readback → ffmpeg pipe
            fbo.Unbind()
        }
        deltaSumF -= fpsDelta
    }
}
ffmpeg.StopFFmpeg()                 // flush, close pipes, mux
```

Determinism: time advances only by fixed `updateDelta`; encoding speed never affects output. Progress % = `GetTimeOffset()/RunningTime`.

### 5.3 Player state (`app/states/player.go`)

`NewPlayer` composes: `Background` (bg image + optional video + storyboard), `HitObjectContainer`, `dance.Controller`, `overlays.Overlay`, Coin, `BloomEffect`, `BlurEffect`, 4 cameras, and ~15 `Glider`s (dim/blur/fx/cursor/hud/volume/objects-alpha fades).

- Cameras: `mainCamera`/`objectCamera` = `SetOsuViewport(W,H, Playfield.Scale, true, OsuShift)`; `bgCamera` similar with storyboard opts; `uiCamera` = virtual **1080p** space (`ScaledHeight=1080`, width by aspect) — the HUD is authored at 1080p and scales with output.
- Controller fork: `PLAY`→PlayerController; `KNOCKOUT` (which `-replay` sets)→**ReplayController** (+ScoreOverlay when 1 player, KnockoutOverlay otherwise); else GenericController (cursordance movers, no ruleset).
- Music: `bass.NewTrack(audioFile)`, or a virtual silent track sized to last object +1 s if the file fails. Silence padding appended so the mixer never runs dry before `MapEnd`.
- **In RECORD mode the realtime update goroutine is skipped** — `mainLoopRecord` drives time externally via `Update(delta)`:

```go
func (player *Player) Update(delta) bool {
    speed = musicPlayer playing ? musicPlayer.GetSpeed() : SPEED * Diff.GetSpeed()
    rawPositionF += delta * speed
    progressMsF = rawPositionF - oldOffset - LOCALOFFSET - onlineOffset  // oldOffset=24 if map Version<5
    updateMain(delta)   // music start/tempo/pitch, objects, controller, overlay, gliders
    if progressMsF >= MapEnd { musicPlayer.Stop(); return true }
    return false
}
```

`Draw` order per frame: rotated camera sets per DIVIDES (`GenRotated(DIVIDES, -2π/DIVIDES)`) → background (dim+blur, bgCamera) → overlay bg / epilepsy warning / coin → bloom Begin → overlay pre-objects → **objectContainer.Draw** (one pass per divide) → overlay normal → storyboard foreground → cursors (`BeginCursorRender`; per divide × per cursor `DrawM` with two colours; `EndCursorRender`) → HUD → bloom EndAndRender.

### 5.4 Playfield mapping (`app/bmath/camera/camera.go`)

`OsuWidth=512, OsuHeight=384`.
```
baseScale = height/384;  if 512/384 > width/height { baseScale = width/512 }   // fit
scl = baseScale * 0.8 * Playfield.Scale        // 0.8 = osu!'s playfield inset
projection = Ortho(-w/2..w/2, h/2..-h/2)       // Y-down
origin (-256,-192); position (ShiftX,ShiftY)*scl (OsuShift → +8px Y)
GenRotated(n, offset) → n rotated PV matrices  // -cursors mirror/mandala
```

### 5.5 Replay → cursor (`app/dance/rcontroller.go`)

`.osr` parsed by rplpa; seed frame (`Time == -12345`) stripped. `Update(time, delta)` replays **every intermediate integer millisecond** so low draw rates never skip frames. Per tick: while `replayTime + frames[idx].Time <= floor(nTime)` advance and `cursor.SetPos(MouseX, MouseY)`; sets key/button state from the replay bitfield (feeding `ruleset.UpdateClickFor/UpdateNormalFor/UpdatePostFor` for judgments); when no frame is consumed, the cursor position is **interpolated** between neighbouring frames. Relax/Autopilot get synthesized clicks/aim.

The ruleset (`app/rulesets/osu/ruleset.go`) computes 300/100/50/miss via `GetResultForDelta` and hit windows / notelock (`CanBeHitStable/Lazer`), maintains combo/acc/score/HP/PP (`SendResult`, `GetFCPP/GetSSPP`), drives fail animation — all consumed by the ScoreOverlay HUD.

Cursor trail (`cursor.go`): points inserted `1/TrailDensity` osu-units apart, aged out by `TrailRemoveSpeed`, drawn as instanced quads with the trail shader (+glow pass); skin cursor path uses `cursortrail` sprites or the long connected trail (§3.3).

### 5.6 GL → ffmpeg (`app/ffmpeg/`)

**`StartFFmpeg(fps, w, h, audioFPS, output)`**: verifies ffmpeg binary + encoders (`ffmpeg -encoders`), creates `<OutputDir>/<output>_temp/`, spawns **two** ffmpeg processes.

**Video** (`video.go:startVideo`) — if motion blur, `fps /= OversampleMultiplier` (ffmpeg receives target fps; subframes are blended before readback). Pixel path: `yuv420p→I420`, `yuv444p→I444`, `nv12→NV12` (GPU RGB→YUV via `effects/rgbyuv.go` shaders), else raw `rgb24` + `vflip` filter. Input via named pipe (Unix) or stdin (Windows).
```
ffmpeg -y -an -f rawvideo -c:v rawvideo -s WxH -pix_fmt <fmt> -r <fps>
  [-color_range 1 -colorspace 1 -color_trc 1 -color_primaries 1]
  -i <pipe> [-vf vflip,<Recording.Filters>]
  -c:v <Encoder> -color_range 1 -colorspace 1 -color_trc 1 -color_primaries 1
  -movflags +write_colr <encoder args from GenerateFFmpegArgs()>
  <temp>/video.<container>
```
Encoder args (e.g. x264): `vbr→-b:v X`, `cbr→-b:v/-minrate/-maxrate/-bufsize`, `crf→-crf N`, plus `-profile:v`, `-preset`, and `AdditionalOptions`.

**Readback** — pool of 10 persistent-mapped `PBO`s (`GL_MAP_PERSISTENT|COHERENT|READ`, size `w*h*3` or `*3/2`):
```
PreFrame():  motion blur → blend.Begin() (bind next accumulation layer)
             else YUV → converter.Begin()
MakeFrame(): frameNumber++
             motion blur: blend.End(); if frameNumber % Oversample != 0 → return
                          blend.Blend()      // weighted sum of N layers (weights.go easing)
             YUV: converter.End(); Draw() → Y + subsampled U/V textures
             pbo <- freePBOPool
             GetTextureSubImage/ReadPixels into PBO;  pbo.sync = FenceSync;  Flush
             frameReadQueue += pbo
             checkData(): fence-signalled PBOs → videoWriteQueue → writer goroutine → pipe
             limiter.Sync()                    // EncodingFPSCap
```
Motion-blur weights: `calculateWeights(BlendFrames)` with easing chosen by `BlendFunctionID` (flat/linear/quad/…/gauss/gaussSymmetric/semiCircle), `w = 1 + ease(i/(n-1))*100`, normalized in the blend shader.

**Audio** (`audio.go`) — BASS runs a **non-playing decode mixer** (48 kHz float stereo, `BASS_STREAM_DECODE`); music track + every hitsound sample attach via `BASS_Mixer_StreamAddChannel`. Each `PushAudio()` = `bass.ProcessMixer(buf)` = `BASS_ChannelGetData(masterMixer, buf, len)` — decodes exactly 1 ms of mixed PCM in lockstep with sim time → pipe:
```
ffmpeg -y -f f32le -acodec pcm_f32le -ar 48000 -ac 2 -i <pipe>
  -nostats -vn [-af <AudioFilters>] -c:a <AudioCodec> -strict -2
  <audio codec args>  <temp>/audio.<container>
```

**Mux** (`StopFFmpeg → combine()`):
```
ffmpeg -y -i <temp>/video.<c> -i <temp>/audio.<c> -c:v copy -c:a copy -strict -2
  [-movflags +faststart]   # mp4 only
  <OutputDir>/<output>.<container>
```
then the temp dir is deleted.

---

## 6. Master pseudo-code

```
INPUT: map.osu (+folder), skin folder (+skin.ini), optional replay.osr, settings JSON, CLI flags

──────────────── PHASE 0: setup ────────────────
parse flags; RECORD=true
replay = rplpa.Parse(osr) → md5, mods, frames[];  KNOCKOUT=true
LoadSettings(name):  json over compiled defaults; migrations; sPatch
beatMap = database lookup by md5 (import walks Songs/, ParseBeatMap per .osu)
GLFW hidden window @ Recording.FrameWidth×Height; GL 3.3; bass.Init(decode mixer)

──────────────── PHASE 1: parse map ────────────────
ParseTimingPointsAndPauses:
    for line in [TimingPoints]: Timings.AddPoint(time, beatLength, set, idx, vol, meter,
                                                 inherited=(uninheritedFlag==0), kiai)
    FinalizePoints(): sort; green lines inherit beatLengthBase from previous red
ParseObjects:
    version = "osu file format vN"
    [Colours] → skin.AddBeatmapColor
    for line in [HitObjects]:
        type = line[3]
        CIRCLE  → NewCircle : pos, time, hitsound bits, extras(set:add:idx:vol)
        SPINNER → NewSpinner: + endTime
        SLIDER  → NewSlider : + curveType|anchors (P/L/B/C → CirArc/Line/Bezier/Catmull
                              → flatten → MultiCurveT truncated to pixelLength),
                              repeats, per-edge hitsounds
    sort by StartTime; assign combo numbers/sets (spinner forces new combo; ColorOffset skips)
    for obj: obj.SetTiming(Timings, version):
        slider: TPoint = point at StartTime
                velocity = 100*SliderMult/TPoint.beatLength()      // SV via GetRatio
                tickDistance = scoringDistance/TickRate
                build scorePath (time-parameterised polyline), ticks, reverses,
                EndTime = StartTime + spans*length/velocity
    processStacking(threshold = floor(Preempt*StackLeniency)): StackIndexMap per object
Diff.calculate():
    radius = DifficultyRate(cs,54.4,32,9.6)*1.00041
    preempt = DifficultyRate(ar,1800,1200,450);  fadeIn = 400*min(1,preempt/450)
    hit300/100/50 = DifficultyRate(od, 80/140/200, 50/100/150, 20/60/100)
    HR: ×1.4 (cs ×1.3);  EZ: ÷2;  DT/HT: Speed 1.5/0.75

──────────────── PHASE 2: skin (lazy) ────────────────
first GetTexture/GetSample triggers:
    load skin.ini (missing key Version → 1.0; missing ini → defaults 2.7)
    lookup(name): SKIN folder → FALLBACK folder → embedded default
                  prefer name@2x (size/2); pack ≤1000px into 2048² atlas
    GetFrames: name-0..N from a single source; AnimationFramerate or 1000/frames
    hitsounds: <set>-<sound>.wav/ogg/mp3, beatmap folder samples override by index
    colours: beatmap [Colours] / skin Combo1-8 / HSV per settings

──────────────── PHASE 3: build player ────────────────
Player: background+storyboard(.osb events → sprite commands), HitObjectContainer,
        ReplayController(frames), ScoreOverlay(ruleset), bloom/blur, gliders
cameras: osu 512×384 → screen: scl = fit(w,h)*0.8*Playfield.Scale, Y-down ortho
each object.SetDifficulty(diff): sprites, fades (start-Preempt, fadeIn), approach 4→1,
        slider body FBO (sliderrenderer), ball/followcircle/tick sprites

──────────────── PHASE 4: record loop (no wall clock) ────────────────
fps = Recording.FPS × (MotionBlur ? Oversample : 1);  Δu = 1000/max(fps,1000)
start ffmpeg video(rawvideo WxH @fps → encoder → temp/video)
start ffmpeg audio(f32le 48k stereo → codec → temp/audio)
while not player.Update(Δu):                  # advances progressMsF += Δu*speed
    # inside Update: replay frames applied per-ms → cursor pos/keys → ruleset judgments
    #                objects spawn at t-Preempt, animate, die at end+fadeout
    every 1 ms:        PushAudio()            # BASS decode mixer → 1 ms PCM → pipe
    every 1000/fps ms: bind FBO
                       PreFrame()             # blur layer / YUV begin
                       Draw: bg(dim,blur) → storyboard → [bloom → objects/divides]
                             → cursors(trail VBO) → HUD(1080p virtual)
                       MakeFrame()            # (blend subframes) → RGB→YUV shader
                                              # → PBO + fence → writer → pipe

──────────────── PHASE 5: finalize ────────────────
flush PBOs/pipes; wait both ffmpeg
ffmpeg -i video -i audio -c copy [+faststart] → OutputDir/name.mp4
delete temp dir
```

---

### Quick reference: what to change for common goals

| Goal | Variable(s) |
|---|---|
| Output resolution / fps | `Recording.FrameWidth/FrameHeight/FPS` |
| Quality | `Recording.libx264.CRF` (or per-encoder CQ/Quality), `Preset` |
| Smooth "blurred" 60fps | `Recording.MotionBlur.Enabled=true`, Oversample 16, BlendFrames 24 |
| Use a skin | `-skin <name>` or `Skin.CurrentSkin`; colours: `Skin.UseColorsFromSkin/UseBeatmapColors` |
| Background dim / storyboard | `Playfield.Background.Dim.*`, `LoadStoryboards`, `LoadVideos` |
| HUD on/off | `Gameplay.<Element>.Show`, `Gameplay.*Font` |
| Skin vs cursor | `Skin.Cursor.UseSkinCursor`, `Cursor.*` (trail style/size) |
| Render a section | `-start <s> -end <s> -skip` |
| Speed/pitch | `-speed -pitch` (or `-mods DT` etc.) |
| Audio sync in the video | `-offset <ms>` (Audio.Offset does NOT affect recordings) |
| Faster encode | `Recording.Encoder=h264_nvenc` etc., `EncodingFPSCap` to limit load |
