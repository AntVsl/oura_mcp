---
name: oura
description: Recipes for reading Oura Ring data through the my-oura-mcp server — sleep, readiness, HRV, resting heart rate, activity, stress. Use when the user asks how they slept, whether they are recovered, what a trend looks like, or wants to investigate how something affected their body.
---

# Oura

Workflows for `my-oura-mcp`. The server exposes eleven tools; this skill covers
what to do with them beyond a single call.

## What the tools already handle

Worth knowing, because it changes what you have to think about:

- **Answers arrive compressed.** Every tool returns per-day values plus `stats`
  with mean, min, max and — when there are at least four days — `trend_per_week`.
  You do not need to average anything by hand, and there is no pagination to
  manage.
- **Timezones are resolved server-side** from `OURA_TZ`. Never convert to UTC
  yourself. "Today" already means the user's today, night sleep is already
  attributed to the right calendar day, and heart rate is already grouped by
  local day.
- **Naps are separated from the main night.** `get_sleep` reports the main sleep
  and puts anything else in `naps_h`.

Pass `raw=True` to any tool for the untouched API response. Only worth it when a
field is missing from the summary — it is a lot of tokens.

## Choosing a window

Every ranged tool takes either `days_back` or an explicit `start_date` /
`end_date` pair in `YYYY-MM-DD`. **Explicit dates win** if both are given.

Resolve vague phrasing before calling:

| Phrase | Window |
|---|---|
| «за неделю», "last week", "past week" | `days_back=7` |
| «за две недели», "last two weeks" | `days_back=14` |
| «за месяц», "last month" | `days_back=30` |
| «вчера», "yesterday" | `start_date` = `end_date` = yesterday |
| «сегодня», "today" | `days_back=1` |

If the user names no period at all, seven days is the sensible default — except
where a recipe below says otherwise, in which case follow the recipe.

`trend_per_week` only appears with four or more days of data. A three-day window
gives you no trend, so do not promise one.

## Tools

| Tool | Answers |
|---|---|
| `get_daily_summary` | sleep, readiness and activity scores together |
| `get_sleep` | stages, efficiency, HRV, resting heart rate, breathing, temperature |
| `get_sleep_score` | just the nightly score — lighter than `get_sleep` |
| `get_readiness` | readiness, HRV balance, temperature deviation |
| `get_activity` | steps, calories, activity score |
| `get_heartrate` | heart rate grouped by local day |
| `get_stress` | daytime stress and recovery time |
| `get_spo2` | blood oxygen and breathing disturbance |
| `get_heart_health` | cardiovascular age and VO₂ max |
| `get_tags` | the user's own notes and markers |
| `get_status` | whether the server has real data or sandbox data |

## Recipes

- [sleep-trend](recipes/sleep-trend.md) — is sleep getting better or worse, and why
- [recovery-check](recipes/recovery-check.md) — can the user take load today
- [symptom-investigator](recipes/symptom-investigator.md) — what the body was doing when the user felt bad
- [intervention-experiment](recipes/intervention-experiment.md) — did a change actually work

## Two habits worth keeping

**An empty answer is information, not an error.** `n: 0` or a missing day usually
means the ring was not worn or has not synced — say so rather than reporting
zeros. Today's night is also simply absent until the user wakes and syncs.

**Do not diagnose.** These are consumer-grade wellness metrics. Describe what
changed and how much, name what it correlates with, and leave conclusions about
health to the user and their doctor.
