# CNC G-code Generator — Walkthrough

## What Was Built

A Python CLI tool that takes any STEP file and auto-generates CNC G-code with **three machining strategies**, then estimates operation time.

### Project Files

| File | Purpose |
|---|---|
| [machining_config.yaml](file:///c:/Users/aenit/Desktop/gcode/machining_config.yaml) | All tunable parameters (machine, material, tools, strategies, safety) |
| [step_analyzer.py](file:///c:/Users/aenit/Desktop/gcode/step_analyzer.py) | Reads STEP → extracts bounding box, face types, holes |
| [gcode_generator.py](file:///c:/Users/aenit/Desktop/gcode/gcode_generator.py) | Generates G-code: roughing (zigzag), finishing (contour), drilling (G83 peck) |
| [time_estimator.py](file:///c:/Users/aenit/Desktop/gcode/time_estimator.py) | Parses G-code, tracks XYZ moves → time breakdown per operation |
| [main.py](file:///c:/Users/aenit/Desktop/gcode/main.py) | CLI entry point chaining the pipeline |

## Usage

```bash
python main.py --step part.step [--config machining_config.yaml] [--output part.gcode]
```

## Test Results

Ran against a **60×40×20 mm box with 2 holes** (`test_part.step`):

- **Geometry detected**: 8 faces (6 planar, 2 curved), 2 holes (Ø10 and Ø6)
- **G-code generated**: 1700 lines → `test_output.gcode`
- **Time estimate**:

| Operation | Time |
|---|---|
| Roughing | 5.92 min |
| Finishing | 50.01 min |
| Drilling | 0.17 min |
| **Total** | **56.09 min** |

G-code starts with `%`, uses `G21` (metric), `G90` (absolute), includes tool changes (`T1 M6`, `T2 M6`, `T3 M6`), coolant (`M8`/`M9`), and ends with `M30`.

## Customization

Edit [machining_config.yaml](file:///c:/Users/aenit/Desktop/gcode/machining_config.yaml) to change:
- **Feeds & speeds** → `material.surface_speed`, `material.chip_load`
- **Tool sizes** → `tools.roughing.diameter`, `tools.finishing.diameter`
- **Strategy params** → `roughing.stepdown`, `finishing.spring_passes`, `drilling.peck_depth`
- **Machine limits** → `machine.max_spindle_rpm`, `machine.max_feed_rate`

Re-run the same command and the G-code + time estimate automatically update.
