"""Tunable settings for lap cleaning (clean.py). Change these, re-run clean.py."""

# Seconds a car gains per lap as fuel burns off. Only used for clean_laps.fuel_corrected_time
# (deg.py estimates fuel itself). Normalises every lap
# to lap-1 fuel load: fuel_corrected_time = lap_time + this * (lap_number - 1).
FUEL_EFFECT_PER_LAP = 0.049

# A lap is "in traffic" if the car physically ahead crossed the timing line
# less than this many seconds before this car started the lap.
TRAFFIC_GAP_S = 1.5

# A lap is an outlier if it's more than this fraction slower than the
# driver's median lap in the same stint (0.07 = 7% slower).
OUTLIER_PCT = 0.07

# Races with less than this fraction of clean laps get highlighted in the
# summary (usually wet or chaotic races).
LOW_CLEAN_RACE_PCT = 0.30

# ---------------------------------------------------------------------------
# Tyre degradation model (deg.py)
# ---------------------------------------------------------------------------
# Below these, an estimate is stored but marked low_confidence.
MIN_LAPS_FIELD = 30      # clean laps on a compound in a race
MIN_DRIVERS_FIELD = 3    # drivers who ran that compound
MIN_LAPS_TEAM = 15
MIN_LAPS_DRIVER = 10

# Cliff check: stints with at least CLIFF_MIN_STINT clean laps. If the last
# CLIFF_LAST_N clean laps are on average more than CLIFF_THRESHOLD_S slower
# than the straight line fitted to the earlier laps, flag possible_cliff.
CLIFF_MIN_STINT = 15
CLIFF_LAST_N = 5
CLIFF_THRESHOLD_S = 0.5

# A race's fitted fuel+track effect (s/lap) outside this range is suspicious.
FUEL_SANE_RANGE = (0.02, 0.10)
# Even worse than this (s/lap above the line) is too sudden for wear:
# classified as probable_damage instead of a cliff.
DAMAGE_THRESHOLD_S = 2.0

# A race is unreliable (kept per race, left out of season summaries) if the
# field model's R2 is below MIN_R2, or more than WET_RACE_PCT of laps were wet.
MIN_R2 = 0.4
WET_RACE_PCT = 0.05

# Season summary rows based on fewer races than this are dropped.
MIN_RACES_SEASON = 4

# Fuel fallback: a race's own fuel+track estimate is replaced if it's outside
# FUEL_SANE_RANGE or its 95% CI is wider than this (s/lap). The replacement is
# the season median of reliable races that kept their own estimate.
FUEL_MAX_CI_WIDTH = 0.05

# Outlier checks in clean.py: the stint median is only trusted if the stint has
# at least this many laps passing every other flag...
MIN_STINT_LAPS_FOR_MEDIAN = 5
# ...and a backup check always runs: more than this fraction slower than the
# driver's median clean lap for the whole race is an outlier.
RACE_OUTLIER_PCT = 0.15

# ---------------------------------------------------------------------------
# Pit battles (battles.py)
# ---------------------------------------------------------------------------
BATTLE_GAP_S = 3.0        # cars within this gap (same lap) before A's stop are a battle
TREND_LAPS = 5            # laps before A's stop used for the gap trend
TREND_THRESHOLD = 0.1     # s/lap change in the gap that counts as closing / dropping
UNDERCUT_MAX_LAPS = 5     # B responding within this many laps = undercut window
STICK_LAPS = 3            # laps after the judged lap to check if the result held
MIN_STOP_LAP = 5          # stops before this lap (damage, lap-1 chaos) are left out of stats
PIT_LOSS_SUSPICIOUS_S = 5.0  # |pit_loss_diff| above this: look for a penalty in race control
