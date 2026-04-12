# Beta Field Test Guide

This integration is ready for real-world beta testing, but the safest approach
is still to treat the planner as a recommendation system first and a fully
trusted automatic scheduler second.

## Before You Start

1. Confirm your weather inputs are configured on the `B-hyve Account` device configuration card.
2. Verify your selected rain entities report the units you expect.
3. Set realistic per-zone `max weekly runtime` values before enabling unattended watering.
4. Set each zone's `Watering profile` to `Default (lawn)`, `Trees / shrubs`, `Drought tolerant`, `Vegetable garden`, or `Disabled` before you judge the planner.
5. Leave each zone `Watering coefficient` at `1.0` initially unless you already know a specific zone needs to be biased up or down.
6. Review your zone settings sensors after pressing `Refresh B-hyve values` so Home Assistant is using the latest B-hyve crop, soil, and nozzle metadata.
7. Press `Export dashboard` on the sprinkler controller you want to monitor, or `Export all dashboards` on the `B-hyve Account` device if you want one file per controller. Then add the exported YAML file under `lovelace: dashboards:` in `configuration.yaml` using the snippet shown in the export notification.
8. Set a valid `Notification target` before you turn `Notifications enabled` on.
9. Set `Maximum watering wind speed` and `Minimum watering temperature` at the account/controller level, then review each zone's `Sprinkler wind profile` so `Standard spray`, `Rotary / stream`, and `Drip / bubbler` zones are held appropriately for wind in your climate.
10. Use weather entities that already report rain in inches and temperature in Fahrenheit because this beta does not auto-convert those inputs yet.

## What To Watch In Beta

### Spring and shoulder season

- Cool-season lawns should ramp up slowly.
- Perennial and drip zones should stay noticeably lighter than turf.
- `Trees / shrubs` zones should wait longer between runs than the same zone would in `Default (lawn)`, but not as long as `Drought tolerant`.
- Drought-tolerant profile zones should wait longer between runs than the same zone would in `Default (lawn)`.
- Garden zones should remain conservative until real summer heat arrives.

### Peak summer

- Utah, Colorado, inland California, Arizona, and similar climates should show a much bigger jump in recommended runtime than spring.
- Garden zones should not be flattened down to the same curve as turf.
- Vegetable-garden profile zones should prefer shorter daily or near-daily runs instead of long lawn-style sessions.
- Forecast rain should not suppress watering when the rolling deficit is already high.

### Humid and storm-prone climates

- Atlanta, Houston, Miami, and similar climates should defer when tomorrow's rain is both likely and meaningful.
- After a real soaking rain, the planner should hold watering for a reasonable carry-over period.
- Repeated drizzle below the effective-rain threshold should not trick the planner into thinking the yard is fully watered.

## Daily Beta Checklist

- Check `Irrigation decision` and `Rolling deficit` on each controller.
- Check `Last watering` and `Next watering cycle` on the controller card or dashboard.
- Compare `Recommended runtime` against what the yard actually needs, especially for lawn vs perennials vs garden beds.
- If you change a zone's `Watering coefficient`, confirm the next recommendation moves in the same direction instead of getting cancelled out by the following day's deficit.
- Watch the `cycle_minutes` attributes on recommended runtime sensors for longer rotary, drip, and bubbler runs.
- Watch the weather-hold reasons and runtime-bank attributes after cold, windy, or gusty mornings to make sure skipped demand is carrying forward sensibly.
- If watering was stopped mid-cycle for wind, confirm the controller stays held for the rest of that local day instead of immediately restarting later.
- Confirm no zone exceeds its configured `max weekly runtime`.

## Safe Rollout Pattern

Recommended rollout:

1. Start with `Automatic watering` off.
2. Let the planner run for several days and compare recommendations with your own judgment.
3. Use short manual test runs and confirm actual sprinkler behavior matches the plan.
4. Keep treating the planner as recommendation-first during this beta; do not assume full unattended execution from the planner yet.
5. Keep notifications enabled during beta so you can spot odd defer/run decisions quickly.
6. Treat those very short manual tests as control checks only; the planner ignores them when evaluating specialty-profile spacing.

## What To Report During Beta

Capture these when something looks wrong:

- date and approximate local weather
- controller `Irrigation decision`
- controller `Rolling deficit`
- the forecast rain amount being used
- the current temperature, humidity, wind speed, and gust being used
- the zone `Recommended runtime`
- whether the issue was overwatering, underwatering, a bad defer, or a bad rain delay

Best reporting path for this beta:

- open a GitHub issue in this repo
- include the scenario details above plus screenshots of the relevant Home Assistant entities when possible

If possible, also note:

- whether the zone is lawn, perennials, or garden
- soil type and exposure from the zone settings sensor
- how much it had really rained in the last 1 to 3 days

## Known Limits In This Beta

- ET now uses month, latitude, temperature, UV, humidity, and wind, but it is still a bounded practical estimate rather than a full Penman-Monteith implementation.
- The calibration suite is broad for a beta, but it is still representative modeling, not a replay of your full station history.
- Forecast behavior depends on the Home Assistant weather entity or forecast sensors you choose.
- The controller still exposes one summary `Rolling deficit`, but that value now reflects the most demanding active zone rather than a single blended whole-yard balance.

## Current Beta Gate

Before this beta pass, the planner was rechecked with the live harness in [scripts/run_planner_scenarios.py](../scripts/run_planner_scenarios.py). The current gate covers `240` scenarios across the U.S. and includes:

- Utah spring, midsummer, drizzle, single storm, rain-delay, and weekly-cap cases
- desert, coastal, inland, Pacific Northwest, high-plains, Upper Midwest, Northeast, Southeast, Gulf, and tropical-humid climates
- humidity/wind ET contrasts plus low-temperature, sustained-wind, and gust-only weather holds
- threshold-boundary checks for low-temperature and high-wind weather holds
- persisted same-day wind-stop holds after a live shutdown
- weather-hold bank release with weekly-cap protection
- forecast defer and forecast override behavior
- drought-tolerant and vegetable-garden zone profile behavior
- per-zone watering-coefficient behavior that must reduce both modeled demand and the following runtime recommendation without recursive rebound, including a next-day follow-up case after the lighter run actually occurred
- soil-storage contrast for higher- and lower-storage spring garden beds
- short manual test runs that should not incorrectly delay the next specialty-profile cycle
- cycle-and-soak and weekly-cap enforcement

For the tuning summary behind that gate, see [docs/planner_calibration.md](planner_calibration.md).
