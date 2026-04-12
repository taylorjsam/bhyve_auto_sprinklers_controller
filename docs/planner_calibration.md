# Planner Calibration

This note records the current planner-calibration gate used before the public
beta field-test pass. The goal is not to perfectly model every yard in America.
The goal is to make sure the planner behaves sensibly across the major climate
families, rain patterns, and zone types we expect Home Assistant users to have.

## Current Validation Gate

The live planner is currently checked by [scripts/run_planner_scenarios.py](../scripts/run_planner_scenarios.py), which loads the real planner module and exits non-zero if the calibration expectations regress.

As of this pass, the gate covers `240` deterministic scenarios and verifies:

- early-season guard rails in Utah and the Upper Midwest
- hot interior-West and desert demand
- forecast defer behavior in humid storm-prone climates
- forecast override behavior when the deficit is already too high
- effective-rain differences between trace drizzle, meaningful light rain, and a single downpour
- rain-delay carry-over after a soaking rain
- crop-type divergence between lawn, perennials, and garden zones
- profile-specific divergence for drought-tolerant and raised-bed vegetable zones
- profile-aggressiveness ordering where drought-tolerant zones stay least aggressive, default stays in the middle, and vegetable-garden zones stay most aggressive
- short manual test runs not blocking specialty-profile interval spacing
- max weekly runtime enforcement
- nozzle/session caps and cycle-and-soak splitting
- broad location/season/rain ordering across a generated U.S. climate matrix
- specialty-profile behavior across drought-tolerant and vegetable zones in multiple climates
- warm-season turf spring ramp, midsummer demand, and fall/winter taper
- forecast-defer behavior at the actual amount/probability threshold boundaries
- humidity and wind ET contrast behavior
- gust-only weather holds without changing ET when the average wind is unchanged
- low-temperature and high-wind weather holds with banking
- persisted same-day wind-stop holds after a live mid-cycle shutdown
- threshold-boundary behavior for low-temperature and high-wind weather holds
- weather-bank release behavior under remaining weekly caps
- spring soil-storage contrast for higher- and lower-storage garden beds
- per-zone watering-coefficient behavior that must reduce both modeled demand and the following runtime recommendation without recursive rebound

## Climate Coverage

The current scenario matrix includes:

- Utah interior: Salt Lake City spring, midsummer, drizzle, single storm, rain delay, weekly cap, xeric drip spacing, xeric short-test ignore, raised-bed vegetable spacing
- Utah desert: St. George summer heat
- Arizona desert: Phoenix monsoon-watch override
- California inland and coastal: Sacramento summer heat and San Diego marine spring
- Pacific Northwest: Seattle dry summer and wet spring
- High plains: Denver summer
- Upper Midwest: Minneapolis spring and winter dormancy
- Northeast: Boston summer
- Southeast and Gulf: Atlanta stormy and dry-streak cases, Houston stormy case, Miami tropical wet
- a generated U.S. climate matrix spanning Salt Lake City, St. George, Phoenix, Sacramento, San Diego, Seattle, Denver, Minneapolis, Chicago, Boston, Atlanta, Houston, and Miami
- per-location spring dry, spring wet, summer dry, trace drizzle, light-daily rain, single-soaker, forecast-storm, fall dry, drought-tolerant recent-run, and vegetable-due cases
- targeted warm-season turf cases for Dallas spring, summer, fall, and winter dormancy
- targeted shade/nozzle cases for Dallas and Seattle
- targeted forecast boundary cases around the current defer thresholds
- targeted wind-hold, gust-hold, cold-hold, boundary-threshold, weather-bank release, weekly-cap release, and soil-storage contrast cases

## Representative Outputs

The harness uses a representative mixed controller with:

- cool-season grass on rotary nozzles
- front-yard perennials on drip
- a backyard garden on bubblers

Current representative outputs:

| Scenario | Decision | Grass | Perennials | Garden |
| --- | --- | ---: | ---: | ---: |
| Salt Lake July hot dry | run | 45 min | 90 min | 60 min |
| Salt Lake July trace drizzle | run | 45 min | 90 min | 52 min |
| Salt Lake July light daily rain | run | 40 min | 61 min | 35 min |
| Salt Lake July single soaker | run | 45 min | 72 min | 41 min |
| Salt Lake April shoulder | run | 24 min | 36 min | 15 min |
| St George June desert heat | run | 45 min | 90 min | 58 min |
| Sacramento July inland heat | run | 45 min | 90 min | 60 min |
| Seattle July mild dry | run | 41 min | 62 min | 35 min |
| Atlanta July stormy | defer | 0 min | 0 min | 0 min |
| Houston June stormy | defer | 0 min | 0 min | 0 min |
| Atlanta August dry streak | run | 45 min | 90 min | 60 min |
| Miami July tropical wet | defer | 0 min | 0 min | 0 min |

## Calibration Read

The current planner behavior is where we want it for beta:

- spring and shoulder-season runtimes stay materially below midsummer for the same property
- hot interior-West and desert cases still water aggressively enough in June through August
- humid storm-prone climates defer when tomorrow's rain is both likely and meaningful
- high-deficit desert cases do not get incorrectly suppressed by forecast rain
- repeated light rain gets more credit than a single runoff-prone storm with a similar weekly total
- trace drizzle below the effective-rain threshold barely changes the recommendation
- perennials and garden zones no longer mirror turf with the same seasonal curve
- long rotary, drip, and bubbler recommendations split into multiple cycles while moderate shoulder-season recommendations stay single-cycle
- low weekly runtime caps clamp recommendations cleanly instead of letting a zone overrun the weekly budget
- the new minimum-run threshold continues to bank nuisance runtimes without double counting the next day's deficit
- humidity and wind now nudge ET in the expected direction without destabilizing the seasonal curves
- cold, windy, and gusty weather-hold mornings bank skipped demand instead of dropping it on the floor
- a live wind stop now persists for the rest of the same local day instead of letting the planner restart watering immediately afterward
- higher-storage clay-heavy spring garden beds now hold short runtimes longer than lower-storage sandy beds

One pattern that did stand out in the wider U.S. matrix:

- in the hottest inland/desert summer cases, turf and drip zones often saturate at their session caps even after meaningful rain; the weather signal still shows up in total controller runtime and in lower-priority zones, but not always in the grass runtime number alone

That behavior currently looks acceptable rather than wrong, but it is worth watching during field testing because it means the hottest markets can flatten against the cap sooner than milder climates.

## Tuning Decisions In This Pass

This validation and tuning pass changed the live planner in several meaningful ways:

1. Spring and shoulder-season ET references were reduced while July and August stayed assertive.
2. Crop-specific seasonal factors were added for cool-season grass, perennials, and garden zones.
3. The forecast-defer guard rail was loosened in humid climates by raising the defer-deficit factor to `0.90`, which better matches Gulf and Southeast storm patterns while still allowing desert override behavior.
4. Zone watering profiles were added so `Default (lawn)`, `Trees / shrubs`, `Drought tolerant`, and `Vegetable garden` zones can follow different frequency patterns without changing the whole controller.
5. The scenario gate now checks cycle-and-soak splitting, weekly-cap behavior, short-test filtering, and zone-profile behavior explicitly instead of relying on spot inspection.
6. Warm-season turf now has its own seasonal curve instead of falling through to a generic factor all year.
7. Humidity and average wind are now part of the bounded ET estimate instead of being treated as future work.
8. Gust can now act as an optional safety hold for watering without distorting ET, and each zone now defaults to the conservative `Standard spray` wind profile unless the user explicitly switches that zone to `Rotary / stream` or `Drip / bubbler`.
9. Weather holds for cold mornings and windy mornings now bank missed runtime instead of discarding it.
10. Soil storage now affects the effective minimum-run floor so sandy beds release short spring runtimes sooner than clay-heavy beds.
11. Zone deficits now stay explicitly zone-specific, with profile aggressiveness checked directly instead of relying only on final runtime inspection.
12. Per-zone watering coefficients now bias zone demand directly, and the gate explicitly checks both same-day and next-day follow-up behavior so reducing a coefficient lowers deficit and runtime instead of being cancelled by the following day's planner math.
13. The profile validation gate now checks the intended ordering `Drought tolerant < Trees / shrubs < Default (lawn) < Vegetable garden` under matching conditions.

The main failure mode this is trying to avoid is the one the user already saw in B-hyve's stock automation:

- too much watering early in the season
- not enough watering in the hottest part of summer
- not enough distinction between lawn and non-lawn zones

## Best Next Tuning Targets

If field data shows systematic bias, these are still the highest-value follow-ups:

1. Replay the planner against actual Ambient Weather history from the property instead of representative snapshots.
2. Refine the effective-rain discount curve if local clay-heavy soils show slower infiltration or longer carry-over.
3. Add more mixed-controller scenarios where lawns, garden beds, and drip zones share the same watering window.
4. Compare recommended runtime against actual turf response and manual observations over a full spring-to-summer transition.
5. Tune the gust offsets or the rotary/stream wind bonus if field data shows the conservative spray default is too cautious for a specific controller.

## Reproducing

Run:

```bash
python3 scripts/run_planner_scenarios.py
```

At the time of this document update, the script ends with:

```text
PASS: 237 scenarios and all planner expectations passed.
```
