# symptom-investigator

The user felt bad on some day and wants to know what the body was doing.

Use for "why was I so tired on Tuesday", "I had a headache all week", "something
was off on the weekend".

## Steps

1. Pin down the day or days. Ask if it is vague — "last week" is not a symptom
   window, and guessing wastes the whole investigation.
2. Pull a window that **surrounds** the day, not just the day itself:
   `get_sleep(start_date=symptom_day - 5, end_date=symptom_day + 1)`. Causes
   usually precede symptoms by a day or two, and the night after shows the cost.
3. Add `get_readiness` and `get_stress` over the same window.
4. Compare the symptom day against the days before it, looking for:
   - `temp_deviation_c` rising a day or two ahead — the usual precursor of
     an infection
   - `avg_hrv` dropping while `lowest_hr` rises — load, poor recovery, alcohol
   - `total_h` or `efficiency` collapsing the night before
   - `spo2_avg` and breathing disturbance via `get_spo2` if the complaint is
     headaches or morning grogginess
5. `get_tags` over the window. The user's own note beats any inference the data
   supports.

## Presenting it

Give a timeline, not a verdict: what changed, on which day, relative to the days
around it. "Temperature was up 0.6 °C two days before, HRV dropped from 45 to 28
the night before, sleep fell to 5.2 hours" is a story the user can act on.

Name what you did **not** find. "Sleep and HRV look ordinary for that week" is a
real result and narrows things down.

## Watch for

**This is not diagnosis, and the line matters here more than anywhere else.**
Present correlations as correlations. A temperature rise before a bad day is a
pattern worth noticing, not an illness. Anything persistent belongs with a
doctor, and say so.

One day against five is a small sample. Two metrics moving together is weak
evidence; one metric moving alone is barely evidence at all.

If the user was travelling, the numbers may describe timezone shift rather than
anything about their health.
