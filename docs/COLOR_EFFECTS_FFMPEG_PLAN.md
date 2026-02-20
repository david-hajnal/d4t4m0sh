# FFmpeg Color Effects Feature Plan

## Goal

Add a new feature set that applies gritty, cinematic color treatments similar to:

- `/Users/kaszperek/repos/d4t4m0sh/docs/example1.jpg`
- `/Users/kaszperek/repos/d4t4m0sh/docs/example2.jpg`
- `/Users/kaszperek/repos/d4t4m0sh/docs/example3.webp`
- `/Users/kaszperek/repos/d4t4m0sh/docs/example4.jpg`

Primary objective: provide reusable FFmpeg-based presets that can be applied to any clip before or after moshing.

## Visual Targets From Reference Images

## `example1.jpg` target
- High contrast and deeper blacks
- Cool/green shadows with warm highlights
- Strong local detail with grit
- Slight dirty-film texture

## `example2.jpg` target
- Muted palette with green/cyan cast
- Matte/faded whites
- Dirt/scratch-like texture layer
- Slight ghosting/double-image feel

## `example3.webp` target
- Washed film look, lifted highlights
- Teal/cyan atmosphere with warm yellow subject bias
- Soft grain and low-intensity texture overlay

## `example4.jpg` target
- Crushed shadows and very hard contrast
- Split-toned look (cool shadows, warm skin/highlights)
- Vignette and sharpened edge intensity

## Proposed Feature Scope (V1)

Create a new algorithm: `color_fx_ffmpeg` (single-input video transform).

Add preset modes:

1. `urban_grit` (example1-like)
2. `dirty_glass` (example2-like)
3. `faded_teal_amber` (example3-like)
4. `hard_shadow_split` (example4-like)

User controls:

- `--color_preset` (preset choice)
- `--fx_strength` (0.0-2.0, scales preset intensity)
- `--grain` (0-40)
- `--vignette` (0.0-1.0)
- `--ghost` (0.0-1.0, lightweight frame blend)
- `--keep_audio` (default true)

## FFmpeg Building Blocks

Core filters to combine per preset:

- `eq` (contrast/brightness/saturation/gamma)
- `colorbalance` (shadow/mid/high color bias)
- `curves` (tone shaping)
- `hue` (global hue/saturation shift as needed)
- `unsharp` (grit/clarity)
- `noise` (film grain)
- `vignette` (edge darkening)
- `tblend` (subtle ghosting)

Optional texture pass (Phase 2):

- Synthetic overlay stream using `-f lavfi` noise source + blend mode

## Initial Preset Recipes (Starting Point)

Note: values below are baseline targets and should be tuned with side-by-side visual review.

## `urban_grit`
```text
eq=contrast=1.35:brightness=-0.03:saturation=0.85:gamma=0.92,
colorbalance=rs=-0.02:gs=0.04:bs=0.10:rm=0.05:gm=-0.01:bm=-0.04:rh=0.08:gh=0.02:bh=-0.06,
unsharp=5:5:0.8:3:3:0.0,
noise=alls=14:allf=t+u,
vignette=angle=PI/4
```

## `dirty_glass`
```text
eq=contrast=1.10:brightness=0.02:saturation=0.75:gamma=0.95,
colorbalance=rs=-0.01:gs=0.06:bs=0.08:rm=0.00:gm=0.03:bm=0.02:rh=0.05:gh=0.02:bh=-0.03,
noise=alls=20:allf=t,
gblur=sigma=0.3,
tblend=all_mode=average:all_opacity=0.15
```

## `faded_teal_amber`
```text
eq=contrast=1.08:brightness=0.04:saturation=0.70:gamma=1.05,
colorbalance=rs=-0.03:gs=0.02:bs=0.12:rh=0.10:gh=0.04:bh=-0.08,
curves=all='0/0 0.15/0.2 0.6/0.62 1/0.95',
noise=alls=10:allf=t+u
```

## `hard_shadow_split`
```text
eq=contrast=1.50:brightness=-0.08:saturation=0.90:gamma=0.88,
colorbalance=rs=-0.06:gs=0.02:bs=0.10:rh=0.14:gh=0.02:bh=-0.10,
curves=all='0/0 0.25/0.18 0.6/0.65 1/1',
unsharp=7:7:1.2:3:3:0.0,
vignette=angle=PI/5
```

## Implementation Plan

## Phase 1: Core algorithm and CLI integration

Files to change:

- `/Users/kaszperek/repos/d4t4m0sh/mosh_algorithms/color_fx_ffmpeg.py` (new)
- `/Users/kaszperek/repos/d4t4m0sh/mosh_algorithms/__init__.py`
- `/Users/kaszperek/repos/d4t4m0sh/main.py`

Tasks:

1. Implement `process(input_path, output_path, color_preset, fx_strength, grain, vignette, ghost, codec, gop, verbose, keep_audio)` in new module.
2. Build preset dictionary + helper to scale/filter values by `fx_strength`.
3. Run FFmpeg with one `-vf` chain, encode video with selected codec, copy audio when possible.
4. Register algorithm in `ALGORITHMS`.
5. Add CLI args with sensible defaults and help text.

Deliverable:

- Working command:
  - `python3 main.py -a color_fx_ffmpeg -f videosrc/clip.mp4 -o out.mp4 --color_preset urban_grit -v`

## Phase 2: Wizard integration

Files to change:

- `/Users/kaszperek/repos/d4t4m0sh/wizard.py`

Tasks:

1. Add `color_fx_ffmpeg` to `ALGORITHM_INFO` (category: `creative` or new `color` group).
2. Add option metadata in `OPTION_INFO` for preset and controls.
3. Extend `build_command()` mapping for this new algorithm.
4. Keep defaults safe so user can run with one Enter-through flow.

Deliverable:

- User can select preset from wizard and execute without manual command editing.

## Phase 3: Preset tuning against references

Tasks:

1. Generate stills from a test clip for all presets.
2. Compare against `docs/example1-4` and tune coefficients.
3. Repeat until each preset has a clear and distinct visual identity.

Deliverable:

- Finalized preset values documented in code and README examples.

## Phase 4: Documentation and examples

Files to change:

- `/Users/kaszperek/repos/d4t4m0sh/README.md`
- `/Users/kaszperek/repos/d4t4m0sh/CLAUDE.md` (optional if agent workflow changed)

Tasks:

1. Add usage examples for all four presets.
2. Describe when to use each preset.
3. Note performance expectations and codec recommendations.

## Acceptance Criteria

V1 is done when:

1. `color_fx_ffmpeg` is selectable via `main.py -a`.
2. All four presets produce visually distinct outputs.
3. At least one preset can be run successfully from the wizard.
4. Audio passthrough works for common MP4 inputs.
5. README contains copy-paste runnable examples.

## Risks and Mitigations

- Risk: Presets look inconsistent across source footage.
  - Mitigation: expose `--fx_strength` and a minimal set of tuning knobs.

- Risk: Heavy filter chain is slow on high-res clips.
  - Mitigation: document preview workflow at lower resolution before final render.

- Risk: Over-processing crushes detail too aggressively.
  - Mitigation: keep conservative defaults and allow strength < 1.0.

## Suggested Next Step

Implement Phase 1 first with only two presets (`urban_grit`, `faded_teal_amber`) to lock interface quickly, then add the other two presets in Phase 3 after tuning.
