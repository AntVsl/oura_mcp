# intervention-experiment

The user changed something — magnesium, no screens after ten, an earlier
bedtime, no alcohol — and wants to know whether it worked.

Use for "did X help", "I've been doing Y for two weeks, any difference".

## Steps

1. Establish the change date. Ask if it is not stated; without it there is
   nothing to compare and the whole exercise is theatre.
2. Pull equal windows on both sides:
   - `get_sleep(start_date=change - N, end_date=change - 1)`
   - `get_sleep(start_date=change, end_date=today)`

   Equal length, and at least fourteen days each. Anything shorter and normal
   variation will look like an effect.
3. Compare the `stats` blocks directly — mean, min, max are already computed.
   Pick the metric the intervention actually targets:
   - sleep aid, earlier bedtime → `total_h`, `efficiency`, `latency_min`
   - alcohol, training load → `avg_hrv`, `lowest_hr`
   - screens, evening light → `deep_h`, `latency_min`
4. Judge the size against the spread. A mean moving by less than the
   before-period's min-to-max range is not an effect, it is noise.
5. Check the metrics the change should **not** have touched. If everything
   improved at once, something else changed too — a holiday, the season, the end
   of a stressful stretch.

## Presenting it

Give both means, the difference, and the spread, in that order. "7.1 h before,
7.4 h after, but nights ranged from 5.9 to 8.4 in both periods" is honest.
"Improved by 0.3 h" alone is not.

Say clearly when the answer is "cannot tell". Two weeks of consumer wearable
data usually cannot separate a small effect from noise, and pretending otherwise
is how people end up believing in things that do nothing.

## Watch for

**Nothing here establishes causation.** One person, no control, no blinding, and
the user knew what they were testing — expectation alone moves these numbers.

Seasons move sleep. So does daylight, work intensity, and whatever else
coincided with the change. Ask what else was going on before crediting the
intervention.

If the user is testing something medical, that belongs with a doctor rather than
a ring.
