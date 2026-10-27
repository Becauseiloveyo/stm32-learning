"""Versioned measurement timing and illustrative gates; no physical truth inputs.

Defaults are executable research assumptions, not calibrated product settings.
See docs/MEASUREMENT-CONTRACT.md for units and observation transport rules.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import math

PROTOCOL_VERSION = "1.0"
OBSERVATION_VERSION = "1.0"
UNIT_SYSTEM = "s_degC_pctRH_Pa"
OBSERVATION_UNITS = {
    "time_s": "s", "ambient_T_C": "degC", "ambient_RH_pct": "%RH",
    "local_T_C": "degC", "local_RH_pct": "%RH", "delta_e_Pa": "Pa",
}
PHASES = ("baseline", "flush", "recovery")


def finite_number(value):
    # Booleans, strings and null are never measurements; do not coerce transports.
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class MeasurementProtocol:
    version: str = PROTOCOL_VERSION
    baseline_s: float = 30.0
    flush_s: float = 20.0
    recovery_s: float = 90.0
    sample_period_s: float = 1.0
    max_gap_factor: float = 2.5
    head_window_s: float = 5.0
    tail_window_s: float = 10.0
    min_phase_samples: int = 2
    min_window_samples: int = 2
    time_tolerance_s: float = 1e-8

    @property
    def recovery_start_s(self):
        return self.baseline_s + self.flush_s

    @property
    def total_s(self):
        return self.recovery_start_s + self.recovery_s

    @property
    def max_sample_gap_s(self):
        return self.sample_period_s * self.max_gap_factor

    def phase_at(self, time_s):
        # A sample at a transition belongs to the new phase; final sample is recovery.
        return "baseline" if time_s < self.baseline_s - self.time_tolerance_s else (
            "flush" if time_s < self.recovery_start_s - self.time_tolerance_s else "recovery"
        )

    def aligned(self, time_s):
        ratio = time_s / self.sample_period_s
        return math.isfinite(ratio) and abs(time_s - round(ratio) * self.sample_period_s) <= self.time_tolerance_s

    def validate(self):
        if self.version != PROTOCOL_VERSION:
            raise ValueError("Unsupported measurement protocol version")
        for name, value in asdict(self).items():
            if name != "version" and (not finite_number(value) or value <= 0):
                raise ValueError(f"{name} must be a finite positive number")
        for name in ("min_phase_samples", "min_window_samples"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 2:
                raise ValueError(f"{name} must be an integer >= 2")
        if self.max_gap_factor < 1:
            raise ValueError("max_gap_factor must be >= 1")
        if self.time_tolerance_s >= self.sample_period_s / 1000:
            raise ValueError("time_tolerance_s must be less than sample_period_s / 1000")
        if not finite_number(self.total_s) or not finite_number(self.max_sample_gap_s):
            raise ValueError("Derived protocol times must be finite")
        for boundary in (self.baseline_s, self.recovery_start_s, self.total_s):
            if not self.aligned(boundary):
                raise ValueError("Phase boundaries must align with sample_period_s")
        if any(round(duration / self.sample_period_s) < self.min_phase_samples
               for duration in (self.baseline_s, self.flush_s)):
            raise ValueError("baseline/flush cannot supply min_phase_samples")
        if self.head_window_s + self.tail_window_s >= self.recovery_s:
            raise ValueError("Recovery feature windows must not overlap")
        if round(self.recovery_s / self.sample_period_s) + 1 < self.min_phase_samples:
            raise ValueError("recovery cannot supply min_phase_samples")
        for window in (self.head_window_s, self.tail_window_s):
            ratio = (window + self.time_tolerance_s) / self.sample_period_s
            if not math.isfinite(ratio) or math.floor(ratio) + 1 < self.min_window_samples:
                raise ValueError("Feature window cannot supply min_window_samples")
        return self

    def snapshot(self):
        self.validate()
        return asdict(self)

    @property
    def fingerprint(self):
        # Canonicalize numeric fields, so 30 and 30.0 identify the same protocol.
        values = {key: float(value) if type(value) in (int, float) else value
                  for key, value in self.snapshot().items()}
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Gates:
    # Demonstration thresholds only; none are calibrated for real garments.
    high_ambient_rh_pct: float = 90.0
    saturation_rh_pct: float = 98.0
    ambient_change_rh_pct: float = 3.0
    temp_mismatch_c: float = 1.0
    strong_delta_pa: float = 100.0
    weak_delta_pa: float = 40.0

    def validate(self):
        for name, value in asdict(self).items():
            if not finite_number(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("high_ambient_rh_pct", "saturation_rh_pct", "ambient_change_rh_pct"):
            if getattr(self, name) > 100:
                raise ValueError(f"{name} must be in 0..100 percent/percentage points")
        if self.temp_mismatch_c > 50:
            raise ValueError("temp_mismatch_c must be in 0..50 C")
        if self.strong_delta_pa <= self.weak_delta_pa:
            raise ValueError("strong_delta_pa must be greater than weak_delta_pa")
        return self


DEFAULT_PROTOCOL = MeasurementProtocol().validate()
DEFAULT_GATES = Gates().validate()
