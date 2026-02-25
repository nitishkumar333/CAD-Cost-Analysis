"""
time_estimator.py — Parse G-code and estimate CNC machining time.

Tracks tool position through G0/G1/G2/G3/G83 commands and computes
per-operation and total time breakdowns.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class OperationTime:
    """Time breakdown for a single machining operation."""
    name: str
    rapid_time: float = 0.0       # seconds
    cutting_time: float = 0.0     # seconds
    dwell_time: float = 0.0       # seconds
    drill_time: float = 0.0       # seconds

    @property
    def total(self) -> float:
        return self.rapid_time + self.cutting_time + self.dwell_time + self.drill_time


@dataclass
class TimeEstimate:
    """Total time estimate for a G-code program."""
    operations: List[OperationTime] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(op.total for op in self.operations)

    @property
    def total_minutes(self) -> float:
        return self.total_seconds / 60.0

    def summary(self) -> str:
        lines = []
        lines.append("=" * 55)
        lines.append(" CNC OPERATION TIME ESTIMATE")
        lines.append("=" * 55)
        for op in self.operations:
            lines.append(f"\n  {op.name}")
            lines.append(f"    Rapid moves:    {op.rapid_time:8.1f} s")
            lines.append(f"    Cutting:        {op.cutting_time:8.1f} s")
            lines.append(f"    Drilling:       {op.drill_time:8.1f} s")
            lines.append(f"    Dwell:          {op.dwell_time:8.1f} s")
            lines.append(f"    Subtotal:       {op.total:8.1f} s  ({op.total/60:.2f} min)")
        lines.append("\n" + "-" * 55)
        lines.append(f"  TOTAL TIME:       {self.total_seconds:8.1f} s  ({self.total_minutes:.2f} min)")
        lines.append("=" * 55)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# G-code parser helpers
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"([A-Z])([+-]?\d*\.?\d+)")


def _parse_words(line: str) -> Dict[str, float]:
    """Extract letter-value pairs from a G-code line."""
    # Strip comments
    line = re.sub(r"\(.*?\)", "", line)
    line = line.split(";")[0]
    return {m.group(1): float(m.group(2)) for m in _WORD_RE.finditer(line.upper())}


def _distance(x1, y1, z1, x2, y2, z2) -> float:
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------

class TimeEstimator:
    """Parse G-code and estimate machining time."""

    def __init__(self, rapid_feed: float = 8000.0):
        """
        Args:
            rapid_feed: Assumed rapid traverse rate in mm/min for G0 moves.
        """
        self.rapid_feed = rapid_feed

    def estimate(self, gcode: str) -> TimeEstimate:
        """Parse a G-code string and return a TimeEstimate."""
        result = TimeEstimate()
        current_op = OperationTime(name="Setup / Preamble")
        result.operations.append(current_op)

        # Machine state
        x = y = z = 0.0
        current_feed = 0.0
        active_g = 0           # 0=rapid, 1=linear
        absolute = True        # G90 vs G91

        for raw_line in gcode.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("%"):
                continue

            # Detect operation boundaries from comments
            if line.startswith("("):
                upper = line.upper()
                if "ROUGHING" in upper and "TOOL" in upper:
                    current_op = OperationTime(name="Roughing")
                    result.operations.append(current_op)
                elif "FINISHING" in upper and "TOOL" in upper:
                    current_op = OperationTime(name="Finishing")
                    result.operations.append(current_op)
                elif "DRILLING" in upper and "TOOL" in upper:
                    current_op = OperationTime(name="Drilling")
                    result.operations.append(current_op)
                continue

            words = _parse_words(line)
            if not words:
                continue

            # Modal G-codes
            g = words.get("G")
            if g is not None:
                g = int(g)
                if g == 90:
                    absolute = True
                    continue
                elif g == 91:
                    absolute = False
                    continue
                elif g == 0:
                    active_g = 0
                elif g == 1:
                    active_g = 1
                elif g == 4:
                    # Dwell
                    p = words.get("P", 0)
                    current_op.dwell_time += p
                    continue
                elif g == 83:
                    # Peck drill cycle
                    drill_z = words.get("Z", z)
                    r_plane = words.get("R", z)
                    q_peck = words.get("Q", abs(drill_z - r_plane))
                    f_drill = words.get("F", current_feed)
                    if f_drill > 0:
                        depth = abs(r_plane - drill_z)
                        # Number of pecks
                        if q_peck > 0:
                            n_pecks = math.ceil(depth / q_peck)
                        else:
                            n_pecks = 1
                        # Each peck: drill down, retract, rapid back
                        # Simplified: total drill distance ≈ depth + n_pecks * retract distance
                        total_drill_dist = depth + n_pecks * abs(r_plane - drill_z) * 0.1
                        drill_time = (depth / f_drill) * 60  # cutting time
                        rapid_time = (n_pecks * abs(r_plane - drill_z) * 0.3 / self.rapid_feed) * 60
                        current_op.drill_time += drill_time
                        current_op.rapid_time += rapid_time
                    z = drill_z
                    continue
                elif g == 80:
                    # Cancel canned cycle
                    continue
                elif g == 28:
                    # Home — ignore for time
                    continue
                elif g in (17, 18, 19, 20, 21, 54, 55, 56, 57, 58, 59):
                    continue

            # Update feed
            if "F" in words:
                current_feed = words["F"]

            # Compute move target
            new_x = words.get("X", x if absolute else 0)
            new_y = words.get("Y", y if absolute else 0)
            new_z = words.get("Z", z if absolute else 0)

            if not absolute:
                new_x += x
                new_y += y
                new_z += z

            # Only compute time if there's actually a position word
            if "X" in words or "Y" in words or "Z" in words:
                dist = _distance(x, y, z, new_x, new_y, new_z)
                if dist > 0.0001:
                    if active_g == 0 or g == 0:
                        # Rapid move
                        t = (dist / self.rapid_feed) * 60  # seconds
                        current_op.rapid_time += t
                    elif active_g == 1 or g == 1:
                        if current_feed > 0:
                            t = (dist / current_feed) * 60  # seconds
                            current_op.cutting_time += t

                x, y, z = new_x, new_y, new_z

        # Remove empty setup op if no time accumulated
        if result.operations and result.operations[0].total < 0.001:
            result.operations.pop(0)

        return result


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def estimate_time(gcode: str, rapid_feed: float = 8000.0) -> TimeEstimate:
    """One-call convenience function."""
    return TimeEstimator(rapid_feed).estimate(gcode)
