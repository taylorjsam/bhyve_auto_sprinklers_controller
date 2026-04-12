# B-hyve Auto Sprinklers Controller

> Beta software: This integration is in active development, may have breaking changes, and should be treated as a prerelease.

Home Assistant custom integration that adds smarter irrigation control for Orbit B-hyve sprinkler controllers.

## Current Status

The repo now includes a working custom-integration scaffold under [custom_components/bhyve_auto_sprinklers_controller](custom_components/bhyve_auto_sprinklers_controller) with:

- a Home Assistant config flow for B-hyve `email + password`
- optional controller ID pinning for accounts with multiple controllers
- direct Orbit/B-hyve cloud discovery and zone control
- zone entities with quick-run control
- controller services for quick run, stop, refresh, and planner recalculation
- controller watering-window time entities
- account-level weather-source selectors and planner guard-rail controls
- rolling water-balance and runtime-planning entities

This is now the current beta baseline for live sprinkler control, planner evaluation, dashboarding, and field testing.

For the current beta field-test checklist and rollout guidance, see [docs/beta_field_test.md](docs/beta_field_test.md).

## Auth Setup

1. Install the repository through HACS as a custom integration.
2. Restart Home Assistant after HACS finishes installing it.
3. In Home Assistant, go to `Settings > Devices & Services > Add Integration`.
4. Choose `B-hyve Auto Sprinklers Controller`.
5. Enter your B-hyve email and password.
6. If your account has multiple controllers, optionally enter the sprinkler controller `device_id` to pin setup to one controller.

Once connected, Home Assistant should:

- create the B-hyve config entry through the UI flow
- register a sprinkler controller device in Home Assistant
- attach an `API status` diagnostic entity to that controller device
- attach mirrored weather-input sensors to the B-hyve account device
- attach an `overall watering coefficient` entity to the B-hyve account device
- attach `minimum run threshold`, `maximum watering wind speed`, and `minimum watering temperature` entities to the B-hyve account device
- attach `automatic watering`, `notifications enabled`, and `notification target` control entities to the B-hyve account device
- attach `watering start time` and `watering end time` entities to each controller
- attach `use automatic watering window` plus suggested/active watering time sensors to each controller
- create one valve entity per sprinkler zone
- create one sensor entity per sprinkler zone for `settings`
- create one number entity per sprinkler zone for `quick run duration`
- create one number entity per sprinkler zone for `max weekly runtime`
- create one number entity per sprinkler zone for `watering coefficient`

If generic B-hyve device discovery can positively identify a sprinkler controller, the device will use that controller's B-hyve metadata. If B-hyve only exposes the account generically at first, the integration still creates a fallback `B-hyve Sprinkler Controller` device so setup still completes and the planner entities remain usable.

## Weather Inputs

The integration now supports selecting other Home Assistant entities as weather inputs through account-level configuration entities on the `B-hyve Account` device.

Use the `B-hyve Account` device configuration card to assign:

- daily rain
- a native Home Assistant `weather` entity for tomorrow's forecast rain
- current humidity
- current wind speed
- current wind gust (optional)
- notification target
- current UV index
- current temperature

Those selected entities are mirrored into this integration as account-level sensors so the irrigation logic can read them in one place, while still letting each user choose their own weather station or source integration.

If you provide a `weather` entity, the planner will call Home Assistant's native `weather.get_forecasts` service to pull the next-day rain amount directly.

The integration also exposes `Weekly rain (7-day)` automatically by summing the last 7 days of recorded daily rain, so you do not need to assign a separate weekly-rain entity.

It also exposes `Effective rain (24h)` as a built-in account-level sensor, using the same smooth discount curve the planner applies before crediting rainfall toward the water balance so small rain-source revisions do not create hard jumps in the derived totals.

The planner uses your Home Assistant home location automatically for ET and watering-window calculations.

Why this integration does not support cheap soil-moisture sensors as a control input:

- low-cost consumer moisture sensors tend to be highly soil-specific, temperature-sensitive, and difficult to calibrate well enough for automatic irrigation control
- lawn and large mixed zones usually need multiple representative probes per zone to be trustworthy, which makes the hardware cost and placement complexity rise quickly
- this integration already models water demand from rainfall, effective rain, ET, soil storage, plant type, exposure, and actual irrigation history, which is a more stable control loop for the intended use case
- because of that, cheap moisture sensors are intentionally not offered as a planner input option right now; if moisture sensing is added later, it will likely be for advisory/trend validation rather than direct closed-loop watering control

Important current unit note:

- rain entities should already report inches
- the current temperature entity should already report Fahrenheit
- humidity should report a `0-100%` relative humidity value
- wind speed should already report mph, m/s, km/h, ft/s, or knots
- wind gust can use the same supported speed units if you choose to configure it
- this beta does not auto-convert rain or temperature inputs yet, so pick entities that already match those expectations

## Zone Control

Each discovered zone is exposed as:

- a `button` entity that starts a quick run for that zone
- a `valve` entity that starts a quick run when opened
- a `sensor` entity that exposes the raw B-hyve zone settings for later calculations
- a `sensor` entity that exposes the planner's recommended runtime for that zone
- a `number` entity that sets the quick-run duration in seconds for that zone
- a `number` entity that stores the zone's measured application rate in `in/hr`
- `number` entities that store each zone's explicit agronomy inputs: `root depth`, `soil WHC`, `MAD`, `kc`, and `trigger buffer`
- a read-only `sensor` entity that shows the zone's computed usable `Capacity` in inches
- a `number` entity that stores the zone's maximum weekly runtime in minutes
- a `number` entity that stores a per-zone watering coefficient, defaulting to `1.0`
- a `select` entity that sets the zone watering profile to `Default (lawn)`, `Trees / shrubs`, `Drought tolerant`, `Vegetable garden`, or `Disabled`
- a `select` entity that sets the zone's `Sprinkler head type` to `Standard spray`, `Rotary / stream`, or `Drip / bubbler`

Current behavior:

- opening a zone valve starts a quick run for that zone using the configured duration
- closing a zone valve stops watering if that same zone is the active run
- each controller has a `Refresh B-hyve values` button that pulls the latest zone settings and status from B-hyve
- the integration also performs one automatic B-hyve refresh after Home Assistant startup so zone-backed values populate immediately
- each controller has an `Evaluate irrigation plan` button that recomputes the rolling deficit and zone runtime recommendations
- each controller now has a `Water recommended now` button that recalculates the plan and immediately runs the recommended controller cycle
- each zone now has a `Water now` button that recalculates the plan and immediately runs just that zone's recommended runtime
- each zone now has one `Calibrate zone` button that runs that zone for 15 minutes; the exported dashboard confirmation explains the tuna-can method, then you multiply the measured inches by `4` and enter that result as the zone's `Application rate (in/hr)` in Settings
- if you trigger one of the planner-based watering buttons while that controller is already watering, the integration stops the current run and switches to the newly requested zone or cycle
- zone settings sensors expose calculation inputs like `available_water_capacity`, `crop_coefficient`, `crop_type`, `root_depth`, `manage_allow_depletion`, `soil_type`, `slope_type`, `nozzle_type`, `flow_rate`, `efficiency`, and plant subtypes as attributes
- zone settings sensors also expose B-hyve `latest_event` and `recent_events`, and the planner subtracts that recent B-hyve irrigation history from the rolling deficit so manual app-triggered runs are included too
- each zone now exposes its own `Zone deficit` sensor plus a read-only `Capacity` sensor so you can see both the current usable deficit and the size of that zone's modeled bucket
- the planner now converts zone deficit inches to minutes using your measured `Application rate` instead of a seeded B-hyve runtime baseline
- max weekly runtime is stored as a persisted Home Assistant number in minutes, with `0` meaning no cap is set yet
- each zone now has a persisted `watering coefficient` number from `0.1-3.0`, defaulting to `1.0`, so you can bias that zone up or down without fighting the deficit model the next day
- each zone now has a `Watering profile` dropdown so lawn zones are explicit, trees and shrubs can run deeper/less-frequently, drought-tolerant drip beds can stay even lighter, and raised-bed vegetable zones can follow shorter/frequent watering without behaving like lawn
- each zone now has its own persisted `Sprinkler head type` select, defaulting to conservative `Standard spray`, with `Rotary / stream` and `Drip / bubbler` options for zones that genuinely handle wind differently
- overall watering coefficient is stored as a persisted Home Assistant number from `0.1-3.0`, defaulting to `1.0`
- minimum run threshold is stored as a persisted Home Assistant number in minutes, defaulting to `10`, so spring zones can bank small recommendations instead of running tiny nuisance cycles
- maximum watering wind speed and minimum watering temperature are stored as persisted Home Assistant numbers so you can tune weather-hold behavior for your site
- each controller gets persisted `watering start time` and `watering end time` entities for later schedule-window logic
- each controller also gets an `Automatic watering time` selector so automatic windows can finish as close to `Morning (dawn)` as practical or start as close to `Evening (sunset)` as practical while still sizing the window to the actual runtime needed that day
- the integration also exposes controller services in [services.yaml](custom_components/bhyve_auto_sprinklers_controller/services.yaml):
  `bhyve_auto_sprinklers_controller.quick_run_zone`, `bhyve_auto_sprinklers_controller.stop_watering`, `bhyve_auto_sprinklers_controller.refresh_zones`, and `bhyve_auto_sprinklers_controller.recalculate_plan`

## Planner Behavior

The first scheduler layer is now inside the integration. This beta currently focuses on planning, recommendation, and operator review. It does not yet execute a full unattended daily schedule from the planner by itself.

Current planner behavior:

- stores a per-zone allowable-depletion bucket where `current_water_in` is the source of truth and `deficit_in = capacity_in - current_water_in`
- keeps the rolling 7-day weather ledger only for cold-start bootstrap and recovery instead of rebuilding the whole deficit from scratch every refresh
- estimates daily ET from the current month, latitude, temperature, UV, humidity, and wind
- can optionally use a direct hourly ET entity when one is configured and fresh, with a computed ET fallback when that source is stale or unavailable
- uses average/current wind for ET, and can optionally use gust as a separate safety stop input for watering holds
- discounts rain into "effective rain" before crediting it to the water balance
- applies rain and irrigation as positive bucket refills and applies ET as hourly daylight-gated draw, so the deficit only falls when actual water is added
- subtracts recent B-hyve irrigation events from the bucket using the same measured `Application rate (in/hr)` that runtime planning uses
- computes a zone-specific bucket and deficit instead of treating the whole controller like one blended landscape, so soil storage, crop type, crop coefficient, exposure, and watering profile can diverge cleanly by zone
- applies each zone's user watering coefficient to that zone's demand model before deficit and runtime are computed, so lowering a zone multiplier really lowers future demand instead of being cancelled back out by the next day's deficit
- respects each zone's `max weekly runtime`
- uses each zone's crop coefficient and exposure to scale runtime recommendations
- uses each zone's soil storage and allowable depletion to adjust how aggressively it banks short runtimes in spring and shoulder season
- applies per-zone watering profiles so `Default (lawn)` stays the turf baseline, `Trees / shrubs` waters somewhat less often and more deeply, `Drought tolerant` stays the least aggressive, and `Vegetable garden` waters most aggressively with shorter summer runtimes
- seeds explicit per-zone agronomy defaults from the selected profile and shows the resulting usable `Capacity` in inches so advanced tuning stays legible
- keeps drought-tolerant zones as deep single-session runs instead of splitting them into cycle-and-soak
- converts each zone's planned deficit into minutes from that zone's measured `Application rate (in/hr)`, so runtime math is based on your own tuna-can calibration instead of B-hyve baseline durations
- decides whether to trigger a run by projecting daylight-only ET draw from now until the next allowed watering window and comparing the projected remaining bucket water against that zone's `trigger buffer`
- uses nozzle-specific session caps and cycle-and-soak thresholds so drip and bubbler zones are not flattened by the same runtime limit as spray zones
- banks sub-threshold zone runtimes up to a configurable minimum-run floor, but treats that bank as a floor rather than additive debt so it does not double count against the next day's rolling deficit
- weather-holds watering when the current temperature is too low, the current average wind speed is too high, or an optional configured gust input crosses the gust stop limit, and banks that missed runtime so the skipped day is carried forward without doubling the next day's deficit
- if watering is already running and the live wind rises above the active limit, the integration stops watering for the rest of that local day and preserves the skipped demand through the runtime bank
- clears a banked runtime after meaningful rain and forces a below-threshold run once a zone has gone 7 days without watering
- builds suggested watering start/end times from crop mix, sun exposure, sunrise, season, and total demand, including longer summer windows when needed
- when automatic windows are enabled, a controller-level setting chooses whether that variable-length window should finish as close to dawn as practical or start as close to sunset as practical
- lets you override those suggested times by turning off `Use automatic watering window` and editing the existing `Watering start time` and `Watering end time` entities
- defers watering for forecast rain when tomorrow looks wet, unless the deficit is already too high or too many dry days have stacked up
- respects the configured controller watering window when reporting whether watering is allowed right now
- rotates due zones into later cycles when the active watering window cannot fit every recommended zone in one morning

Location matters in two main ways:

- watering amount should scale with local evaporative demand, which changes by season, latitude, temperature, sun exposure, and recent rain
- watering time should stay in the early-morning window, but the exact window should move with sunrise and local day length rather than being hard-coded for one state

The current planner handles that by using Home Assistant's configured location to shift the seasonal ET baseline and the suggested watering window. Southern-hemisphere locations automatically flip the seasonal reference table so summer and winter line up correctly.

## Research Basis

The location-aware planner is intentionally based on a few conservative irrigation rules that hold up well across regions:

- water requirement should track evapotranspiration and recent effective rain, not just a static timer
- watering should stay in the early-morning window to reduce evaporation and wind loss
- heavier clay soils should usually be watered less often but with enough soak time to avoid runoff
- forecast rain should defer irrigation when the current deficit is still moderate
- high wind and freeze-prone mornings should hold irrigation without discarding the missed runtime
- average wind is the right input for ET, while gust is better treated as an optional safety override for active watering stops

Good reference points for this logic:

- [EPA WaterSense watering tips](https://www.epa.gov/watersense/watering-tips) for early-morning watering, roughly weekly water budgeting, and cycle-and-soak on clay/slope
- [FAO Crop Evapotranspiration (Paper 56)](https://www.fao.org/land-water/land/land-governance/land-resources-planning-toolbox/category/details/en/c/1026557/) for ET, crop coefficients, and climate-driven crop water demand
- [University of Maryland watering lawns](https://extension.umd.edu/resource/watering-lawns) for early-morning watering, deep irrigation, and avoiding shallow frequent cycles
- [USU gardening in clay soils](https://extension.usu.edu/yardandgarden/research/gardening-in-clay-soils) for clay infiltration and dry-down behavior
- [Hunter Wind-Clik](https://www.hunterindustries.com/irrigation-product/sensors/wind-clik) for the common irrigation-industry shutoff range, which supports the conservative `12 mph` default as the low end of a real wind-sensor stop band rather than an arbitrary number

This planner currently adjusts amount and timing using latitude, season, temperature, UV, humidity, average wind, optional gust, rain, forecast rain, crop coefficient, exposure, crop type, soil storage, each zone's sprinkler head type, measured application rate, and your zone caps. It also applies crop-specific seasonal guard rails so cool-season lawns ramp up more gently in spring, perennials stay less aggressive than turf, and garden zones get a stronger midsummer boost.

For calibration and future tuning work, the repo now also includes [scripts/run_planner_scenarios.py](scripts/run_planner_scenarios.py), which runs representative climate scenarios through the live planner so seasonal, crop-coefficient, and per-zone multiplier changes can be checked before they land in the integration. The current results are summarized in [docs/planner_calibration.md](docs/planner_calibration.md).

The current beta gate covers `274` deterministic scenarios across Utah, the desert Southwest, inland and coastal California, the Pacific Northwest, the high plains, the Upper Midwest, the Northeast, the Southeast, the Gulf Coast, and tropical-humid climates. It now also checks measured application-rate planning, unconfigured-zone handling, tuna-can calibration follow-up behavior, humidity and wind ET behavior, gust-only weather holds, live wind-stop persistence, low-temperature and high-wind weather holds, weather-hold threshold boundaries, weather-hold banking, weather-bank release under weekly caps, soil-storage contrast, forecast holds, forecast override under high deficit, weekly-cap enforcement, cycle-and-soak splitting, drought-tolerant spacing, raised-bed vegetable profiles, direct profile-aggressiveness ordering, per-zone watering-coefficient anti-recursion behavior across same-day and next-day follow-up cases, and short manual test runs that should not skew interval spacing.

For dashboarding, the integration now includes an `Export dashboard` button on each sprinkler controller device plus an `Export all dashboards` button on the `B-hyve Account` device. These exports write ready-to-use YAML dashboards into `/config/dashboards/` with the live controller, zone, and account entities already filled in. The generated layout stays centered on three views only: `Overview`, `Zones`, and `Settings`, with direct-action controller buttons on the dashboard itself and source assignment kept on the settings page. The overview now includes a dedicated `Water now` action for the full controller plus a per-zone `Time Watered This Week by Zone` runtime summary, while the `Zones` page carries the per-zone `Water zone now` actions, `Zone multiplier` controls, live `This week` runtime tiles, and `Deficit - Last 7 Days` graphs so each zone's own balance is visible without crowding the landing page.

The exporter also writes optional `card-mod` styling hooks so the generated dashboard can get closer to the polished dark mockups in this repo. The dashboard still renders without [card-mod](https://github.com/thomasloven/lovelace-card-mod), but the visual treatment is noticeably better if `card-mod` is installed.

**To install the dashboard:**

1. On the sprinkler controller you want to use, press `Export dashboard`
2. Open your Home Assistant `configuration.yaml`
3. Add the exported dashboard under `lovelace: dashboards:` using the snippet shown in the export notification
4. Restart Home Assistant
5. The new dashboard will appear in the sidebar with a compact overview plus dedicated zones and settings views already populated

If you only have one controller, pressing `Export all dashboards` from the `B-hyve Account` device is also fine.

Example:

```yaml
lovelace:
  dashboards:
    bhyve-auto-sprinklers-controller-back-yard-06d188:
      mode: yaml
      title: "Back Yard"
      icon: mdi:sprinkler
      show_in_sidebar: true
      filename: "dashboards/bhyve_auto_sprinklers_controller_back_yard_06d188.yaml"
```

If you already have a `lovelace:` section in `configuration.yaml`, merge the new dashboard entry into the existing `dashboards:` block instead of adding a second `lovelace:` root.

## Beta Field Testing

For this beta, the safest rollout is:

1. assign your weather-source entities on the `B-hyve Account` configuration card and refresh the B-hyve values
2. review the planner recommendations and dashboard for a few days
3. set a `Notification target` before relying on planner notifications
4. keep `Automatic watering` off during this beta because the planner is still recommendation-first rather than full unattended execution
5. keep `Notifications enabled` on while you evaluate rain holds and runtime recommendations
6. set conservative `max weekly runtime` values before allowing unattended control

The most important things to watch are:

- spring should stay noticeably lighter than midsummer
- lawns, perennials, and garden zones should not all track the same curve
- humid storm-prone days should defer watering when tomorrow's rain is likely
- desert or high-deficit weeks should still water even if a forecast says rain might come

The detailed checklist is in [docs/beta_field_test.md](docs/beta_field_test.md).

Planner entities:

- account-level forecast rain sensor showing the forecast amount the planner is using
- account-level `Automatic watering`, `Notifications enabled`, and `Notification target` control entities
- controller-level `Irrigation decision`, `Average deficit`, `Last watering`, and `Next watering cycle` sensors
- controller-level `Suggested watering start`, `Suggested watering end`, `Active watering start`, and `Active watering end` sensors
- zone-level `Recommended runtime` sensors with cycle-and-soak and cap details in attributes
- zone-level `Zone deficit` sensors so you can inspect the real per-zone balance instead of only the controller summary

Notification behavior:

- if `Notifications enabled` is on, pressing `Evaluate irrigation plan` sends a push summary through the selected `Notification target`
- this uses any Home Assistant `notify.*` service exposed in your system

## Security Notes

- The config flow masks the `password` field in the UI.
- The integration does not expose credentials or tokens as entity attributes.
- Config-entry diagnostics redact `email`, `password`, controller IDs, and the saved Orbit session token.
- Home Assistant config entries are persisted locally on disk. As far as the official developer docs indicate, that storage is persistent but not an encrypted secret vault. So this integration minimizes exposure inside Home Assistant, but true at-rest protection still depends on the host running Home Assistant, such as disk encryption and OS-level access control.

## Current Caveat

Sprinkler control is now routed through a direct Orbit/B-hyve API client built into this integration. The planner has been tuned against a broader U.S. scenario matrix, but this is still beta software and needs live validation across more than one B-hyve account, controller setup, and climate.

## Zone Data Available

The zone payload already exposes most of the inputs needed for water-balance scheduling:

- identity: `zone_id`, `zone_number`, `name`, `enabled`
- plant demand: `crop_type`, `garden_subtypes`, `crop_coefficient`, `manual_crop_coefficient`
- soil reservoir: `root_depth`, `manual_root_depth`, `available_water_capacity`
- watering trigger: `manage_allow_depletion`
- raw hydraulic references: `flow_rate`
- raw hydraulic references: `efficiency`
- site conditions: `exposure_type`, `soil_type`, `slope_type`, `nozzle_type`
- history and reference fields: `latest_events`, `schedules`, `smart_duration`

Your sample also shows a realistic mixed yard:

- perennial beds in full sun
- drip zones
- a vegetable garden with custom depletion and root depth
- cool-season grass zones with different efficiencies
- a disabled zone that should stay out of auto-plans unless re-enabled

## Related Docs

- [docs/beta_field_test.md](docs/beta_field_test.md) for the recommended real-world testing checklist
- [docs/planner_calibration.md](docs/planner_calibration.md) for the current validation gate and tuning notes
- [custom_components/bhyve_auto_sprinklers_controller/dashboards/bhyve_auto_sprinklers_controller_dashboard.yaml](custom_components/bhyve_auto_sprinklers_controller/dashboards/bhyve_auto_sprinklers_controller_dashboard.yaml) for the packaged Lovelace dashboard template
