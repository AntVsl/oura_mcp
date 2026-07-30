# sleep-trend

Is sleep getting better or worse, and which part of it is moving.

Use when the user asks how they have been sleeping lately, whether something is
improving, or why they feel tired despite "sleeping enough".

## Steps

1. `get_sleep(days_back=28)` unless the user named a period. Four weeks is the
   shortest window where a weekly trend is worth trusting — a single bad night
   swings a seven-day mean by a lot.
2. Read `stats` — the trend is already computed. `trend_per_week` is the change
   per week in the metric's own units: `-0.3` on `total_h` means twenty minutes
   less sleep each week.
3. Look at which components move together:
   - `total_h` falling while `efficiency` holds → less time in bed, not worse sleep
   - `efficiency` falling while `total_h` holds → same time in bed, worse sleep
   - `deep_h` or `rem_h` falling while `total_h` holds → composition shifted
   - `avg_hrv` falling with `lowest_hr` rising → load accumulating, not a sleep
     problem in itself
4. Find the extremes: the best and worst nights by `total_h` or `efficiency`.
   Check `naps_h` on the worst days — a long nap often explains a short night.
5. `get_tags(days_back=28)` if anything stands out. The user's own notes explain
   more than any correlation.

## Presenting it

Lead with the direction and its size in human units — "about 25 minutes less per
week over the last month" beats "trend_per_week: -0.42". Then name the component
that moved. Then the caveats.

Say plainly when there is no trend. Metrics wander; four weeks of noise is a
normal and useful answer.

## Watch for

A window shorter than four days returns no `trend_per_week` at all. Do not fill
that gap by eyeballing the daily numbers and calling it a trend.

Compare like with like: a month spanning a holiday or an illness is not a
baseline. If `get_tags` shows something big, treat the periods separately.
