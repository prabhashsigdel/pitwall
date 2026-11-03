"""Tunable settings for lap cleaning (clean.py). Change these, re-run clean.py."""

# Seconds a car gains per lap as fuel burns off. Used to normalise every lap
# to lap-1 fuel load: fuel_corrected_time = lap_time + this * (lap_number - 1).
FUEL_EFFECT_PER_LAP = 0.055

# A lap is "in traffic" if the car physically ahead crossed the timing line
# less than this many seconds before this car started the lap.
TRAFFIC_GAP_S = 1.5

# A lap is an outlier if it's more than this fraction slower than the
# driver's median lap in the same stint (0.07 = 7% slower).
OUTLIER_PCT = 0.07

# Races with less than this fraction of clean laps get highlighted in the
# summary (usually wet or chaotic races).
LOW_CLEAN_RACE_PCT = 0.30
