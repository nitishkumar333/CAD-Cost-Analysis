"""
gcode_generator.py — Generate CNC G-code from analyzed part geometry.

Strategies implemented:
  1. Roughing  — zigzag pocket clearing, layer by layer
  2. Finishing — contour tracing at fine stepdown
  3. Drilling  — G83 peck-drill cycle for each detected hole
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Any

from step_analyzer import PartGeometry, HoleFeature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rpm(surface_speed_m_min: float, diameter_mm: float, factor: float = 1.0, max_rpm: int = 12000) -> int:
    """Calculate spindle RPM from surface speed and tool diameter."""
    rpm = (surface_speed_m_min * 1000) / (math.pi * diameter_mm) * factor
    return min(int(round(rpm)), max_rpm)


def _feed(rpm: int, flutes: int, chip_load: float, factor: float = 1.0) -> int:
    """Calculate feed rate (mm/min) from RPM, flutes, and chip load."""
    return int(round(rpm * flutes * chip_load * factor))


# ---------------------------------------------------------------------------
# G-code builder
# ---------------------------------------------------------------------------

class GcodeBuilder:
    """Accumulates G-code lines with optional line numbers."""

    def __init__(self, line_numbers: bool = True, decimals: int = 3):
        self.lines: List[str] = []
        self.line_numbers = line_numbers
        self.decimals = decimals
        self._n = 0

    def comment(self, text: str):
        self.lines.append(f"({text})")

    def blank(self):
        self.lines.append("")

    def cmd(self, code: str):
        if self.line_numbers:
            self._n += 10
            self.lines.append(f"N{self._n} {code}")
        else:
            self.lines.append(code)

    def _f(self, v: float) -> str:
        return f"{v:.{self.decimals}f}"

    def rapid(self, x=None, y=None, z=None):
        parts = ["G0"]
        if x is not None: parts.append(f"X{self._f(x)}")
        if y is not None: parts.append(f"Y{self._f(y)}")
        if z is not None: parts.append(f"Z{self._f(z)}")
        self.cmd(" ".join(parts))

    def linear(self, x=None, y=None, z=None, f=None):
        parts = ["G1"]
        if x is not None: parts.append(f"X{self._f(x)}")
        if y is not None: parts.append(f"Y{self._f(y)}")
        if z is not None: parts.append(f"Z{self._f(z)}")
        if f is not None: parts.append(f"F{int(f)}")
        self.cmd(" ".join(parts))

    def build(self) -> str:
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

class GcodeGenerator:
    """Generate G-code for a part using the machining config."""

    def __init__(self, geometry: PartGeometry, config: Dict[str, Any]):
        self.geo = geometry
        self.cfg = config
        self.gb = GcodeBuilder(
            line_numbers=config.get("output", {}).get("line_numbers", True),
            decimals=config.get("output", {}).get("decimal_places", 3),
        )

    def generate(self) -> str:
        """Run the full machining pipeline and return the G-code string."""
        self._header()
        self._roughing()
        self._finishing()
        self._drilling()
        self._footer()
        return self.gb.build()

    # ------------------------------------------------------------------
    # Header / Footer
    # ------------------------------------------------------------------

    def _header(self):
        gb = self.gb
        gb.lines.append("%")
        gb.comment("===========================================")
        gb.comment(" AUTO-GENERATED CNC G-CODE")
        gb.comment(f" Material: {self.cfg['material']['name']}")
        gb.comment(f" Stock: {self.geo.stock_x:.1f} x {self.geo.stock_y:.1f} x {self.geo.stock_z:.1f} mm")
        gb.comment(f" Holes detected: {len(self.geo.holes)}")
        gb.comment("===========================================")
        gb.blank()
        units = "G21" if self.cfg.get("output", {}).get("units", "metric") == "metric" else "G20"
        gb.cmd(units)                          # Units
        gb.cmd("G90")                          # Absolute positioning
        gb.cmd("G17")                          # XY plane
        gb.cmd(self.cfg["machine"]["work_coordinate"])  # Work offset
        gb.blank()

    def _footer(self):
        gb = self.gb
        gb.blank()
        gb.comment("--- Program End ---")
        gb.cmd("M5")        # Spindle stop
        gb.cmd("M9")        # Coolant off
        safe_z = self.cfg["safety"]["clearance_plane"]
        gb.rapid(z=safe_z)
        gb.cmd("G28 G91 Z0")  # Return to home Z
        gb.cmd("M30")         # Program end
        gb.lines.append("%")

    # ------------------------------------------------------------------
    # Coolant helper
    # ------------------------------------------------------------------

    def _coolant_on(self):
        mode = self.cfg["safety"].get("coolant", "flood")
        if mode == "flood":
            self.gb.cmd("M8")
        elif mode == "mist":
            self.gb.cmd("M7")

    # ------------------------------------------------------------------
    # ROUGHING — zigzag pocket
    # ------------------------------------------------------------------

    def _roughing(self):
        gb = self.gb
        cfg_r = self.cfg["roughing"]
        tool = self.cfg["tools"]["roughing"]
        mat = self.cfg["material"]
        machine = self.cfg["machine"]

        rpm = _rpm(mat["surface_speed"], tool["diameter"],
                   cfg_r.get("spindle_factor", 1.0), machine["max_spindle_rpm"])
        feed = _feed(rpm, tool["flutes"], mat["chip_load"],
                     cfg_r.get("feed_factor", 1.0))
        feed = min(feed, machine["max_feed_rate"])

        stepdown = cfg_r["stepdown"]
        stepover = tool["diameter"] * cfg_r["stepover_pct"] / 100.0
        safe_z = self.cfg["safety"]["clearance_plane"]

        bb = self.geo.bounding_box
        # Machining area = stock extents
        x_start = bb.x_min - self.cfg["safety"]["stock_clearance"]
        x_end = bb.x_max + self.cfg["safety"]["stock_clearance"]
        y_start = bb.y_min - self.cfg["safety"]["stock_clearance"]
        y_end = bb.y_max + self.cfg["safety"]["stock_clearance"]
        z_top = bb.z_max
        z_bottom = bb.z_min

        gb.blank()
        gb.comment("=========================================")
        gb.comment(f" ROUGHING — Tool {tool['id']}: {tool['description']}")
        gb.comment(f" RPM={rpm}  Feed={feed} mm/min")
        gb.comment(f" Stepdown={stepdown} mm  Stepover={stepover:.1f} mm")
        gb.comment("=========================================")

        # Tool change
        gb.cmd(f"T{tool['id']} M6")
        gb.cmd(f"S{rpm} M3")
        self._coolant_on()
        gb.rapid(z=safe_z)

        # Layer-by-layer zigzag
        z = z_top - stepdown
        while z >= z_bottom:
            z_cut = max(z, z_bottom)
            gb.blank()
            gb.comment(f"  Roughing layer Z={z_cut:.3f}")
            y = y_start
            forward = True
            first_pass = True
            while y <= y_end:
                if forward:
                    x_a, x_b = x_start, x_end
                else:
                    x_a, x_b = x_end, x_start
                # Move to start of pass
                gb.rapid(x=x_a, y=y)
                if first_pass:
                    gb.rapid(z=self.cfg["safety"]["retract_plane"] + z_top)
                    gb.linear(z=z_cut, f=feed // 2)  # plunge at half feed
                    first_pass = False
                else:
                    gb.linear(z=z_cut, f=feed)
                # Cut across
                gb.linear(x=x_b, y=y, f=feed)
                y += stepover
                forward = not forward

            gb.rapid(z=safe_z)
            z -= stepdown

        gb.cmd("M5")   # Spindle stop after roughing
        gb.cmd("M9")   # Coolant off
        gb.rapid(z=safe_z)
        gb.blank()

    # ------------------------------------------------------------------
    # FINISHING — contour
    # ------------------------------------------------------------------

    def _finishing(self):
        gb = self.gb
        cfg_f = self.cfg["finishing"]
        tool = self.cfg["tools"]["finishing"]
        mat = self.cfg["material"]
        machine = self.cfg["machine"]

        rpm = _rpm(mat["surface_speed"], tool["diameter"],
                   cfg_f.get("spindle_factor", 1.0), machine["max_spindle_rpm"])
        feed = _feed(rpm, tool["flutes"], mat["chip_load"],
                     cfg_f.get("feed_factor", 1.0))
        feed = min(feed, machine["max_feed_rate"])

        stepdown = cfg_f["stepdown"]
        safe_z = self.cfg["safety"]["clearance_plane"]
        bb = self.geo.bounding_box
        offset = tool["diameter"] / 2.0  # tool radius offset from part edge

        z_top = bb.z_max
        z_bottom = bb.z_min

        gb.blank()
        gb.comment("=========================================")
        gb.comment(f" FINISHING — Tool {tool['id']}: {tool['description']}")
        gb.comment(f" RPM={rpm}  Feed={feed} mm/min")
        gb.comment(f" Stepdown={stepdown} mm  Spring passes={cfg_f['spring_passes']}")
        gb.comment("=========================================")

        gb.cmd(f"T{tool['id']} M6")
        gb.cmd(f"S{rpm} M3")
        self._coolant_on()
        gb.rapid(z=safe_z)

        # Contour rectangle tracing at each Z level
        x_min = bb.x_min - offset
        x_max = bb.x_max + offset
        y_min = bb.y_min - offset
        y_max = bb.y_max + offset

        z = z_top
        while z >= z_bottom:
            z_cut = max(z, z_bottom)
            total_passes = 1 + cfg_f.get("spring_passes", 0)
            for pass_num in range(total_passes):
                gb.blank()
                label = "Spring pass" if pass_num > 0 else "Finish pass"
                gb.comment(f"  {label} Z={z_cut:.3f}")
                gb.rapid(x=x_min, y=y_min)
                gb.rapid(z=self.cfg["safety"]["retract_plane"] + z_top)
                gb.linear(z=z_cut, f=feed // 2)
                # Trace rectangle
                gb.linear(x=x_max, y=y_min, f=feed)
                gb.linear(x=x_max, y=y_max, f=feed)
                gb.linear(x=x_min, y=y_max, f=feed)
                gb.linear(x=x_min, y=y_min, f=feed)
                gb.rapid(z=safe_z)
            z -= stepdown

        gb.cmd("M5")
        gb.cmd("M9")
        gb.rapid(z=safe_z)
        gb.blank()

    # ------------------------------------------------------------------
    # DRILLING — G83 peck cycle
    # ------------------------------------------------------------------

    def _drilling(self):
        if not self.geo.holes:
            self.gb.blank()
            self.gb.comment(" DRILLING — No holes detected, skipping.")
            return

        gb = self.gb
        cfg_d = self.cfg["drilling"]
        tool = self.cfg["tools"]["drill"]
        mat = self.cfg["material"]
        machine = self.cfg["machine"]

        rpm = _rpm(mat["surface_speed"], tool["diameter"],
                   cfg_d.get("spindle_factor", 1.0), machine["max_spindle_rpm"])
        feed = _feed(rpm, tool["flutes"], mat["chip_load"],
                     cfg_d.get("feed_factor", 1.0))
        feed = min(feed, machine["max_feed_rate"])

        peck = cfg_d["peck_depth"]
        retract = cfg_d["retract_height"]
        dwell = cfg_d["dwell_time"]
        safe_z = self.cfg["safety"]["clearance_plane"]

        gb.blank()
        gb.comment("=========================================")
        gb.comment(f" DRILLING — Tool {tool['id']}: {tool['description']}")
        gb.comment(f" RPM={rpm}  Feed={feed} mm/min")
        gb.comment(f" Peck={peck} mm  Retract={retract} mm  Dwell={dwell}s")
        gb.comment(f" Holes: {len(self.geo.holes)}")
        gb.comment("=========================================")

        gb.cmd(f"T{tool['id']} M6")
        gb.cmd(f"S{rpm} M3")
        self._coolant_on()
        gb.rapid(z=safe_z)

        f_str = self.gb._f
        for i, hole in enumerate(self.geo.holes):
            gb.blank()
            gb.comment(f"  Hole {i+1}: center=({hole.center_x}, {hole.center_y}) "
                        f"dia={hole.diameter} depth={hole.depth:.2f}")
            gb.rapid(x=hole.center_x, y=hole.center_y)
            gb.rapid(z=hole.top_z + retract)
            # G83 peck drill cycle
            gb.cmd(f"G83 Z{f_str(hole.bottom_z)} Q{f_str(peck)} "
                   f"R{f_str(hole.top_z + retract)} F{int(feed)}")
            if dwell > 0:
                gb.cmd(f"G4 P{dwell}")
            gb.cmd("G80")  # Cancel canned cycle
            gb.rapid(z=safe_z)

        gb.cmd("M5")
        gb.cmd("M9")
        gb.rapid(z=safe_z)
        gb.blank()


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def generate_gcode(geometry: PartGeometry, config: dict) -> str:
    """One-call convenience function."""
    return GcodeGenerator(geometry, config).generate()
