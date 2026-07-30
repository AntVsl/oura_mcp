# recovery-check

Can the user take load today, and if not, what is holding them back.

Use for "am I recovered", "should I train today", "why do I feel wrecked".

## Steps

1. `get_readiness(days_back=14)` — today's number means little without the
   fortnight around it. Readiness of 70 is bad news for someone who normally
   sits at 85 and good news for someone at 60.
2. `get_sleep(days_back=14)` for the drivers: `avg_hrv`, `lowest_hr`,
   `total_h`, `temp_deviation_c`.
3. Place today against the person's own baseline, not against 100:
   - readiness within roughly ±5 of their 14-day mean → an ordinary day
   - `avg_hrv` below the mean **and** `lowest_hr` above it → real load, the two
     agreeing matters more than either alone
   - `temp_deviation_c` above about +0.5 °C, especially with a resting
     heart rate jump → the body is fighting something; this is the strongest
     signal in the whole set
4. Check whether it is one bad night or a slide: is readiness down for one day
   or four in a row? A single dip after a late night is not accumulated fatigue.
5. `get_stress(days_back=7)` if readiness is low while sleep looks fine —
   daytime load is the remaining explanation.

## Presenting it

Answer the question first — recovered, partly, or not — then give the two or
three numbers that decide it, each against the person's own baseline. "HRV 31
against your usual 42, resting heart rate up 6" tells them more than a score.

If temperature is elevated, say so early and plainly. It is the one signal here
that regularly precedes feeling ill.

## Watch for

Do not tell the user whether to train. Describe the state and what it usually
precedes; the decision is theirs, and it depends on things the ring cannot see.

One night proves nothing. If the user has just returned from a flight, has been
drinking, or slept in a strange bed, the numbers describe that, not their
fitness.
