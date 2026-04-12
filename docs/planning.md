# Planning Notes

## Current Beta Scope

This document started as the forward-looking design note for the integration.
Some sections below still describe future architecture, but the current beta
already includes a meaningful subset of that design:

- B-hyve authentication and sprinkler control through the built-in direct Orbit/B-hyve API client
- per-zone control, runtime, profile, cap, and watering-coefficient entities
- imported Home Assistant weather, forecast, humidity, and wind inputs
- a location-aware rolling water-balance planner
- account-level planner controls for overall watering coefficient, minimum run threshold, maximum watering wind speed, and minimum watering temperature
- per-zone watering coefficients that bias zone-specific demand without causing recursive next-day overcorrection
- weather holds for high wind and low temperature with runtime banking
- soil-storage-aware minimum-run thresholds
- zone-specific deficit sensors and zone-page water-balance graphs in the exported dashboard
- exported YAML dashboards with live controller, zone, history, and planner entities already populated
- dashboard-friendly history and next-cycle entities
- notifications and manual planner evaluation controls
- a deterministic planner validation harness in `scripts/run_planner_scenarios.py`

Current planner guard rails before beta field testing:

- spring and shoulder-season runtimes are intentionally restrained
- midsummer heat in dry climates is intentionally stronger
- lawns, perennials, and garden zones use different seasonal curves
- forecast rain can defer watering, but not when the deficit is already too high
- windy or too-cold mornings can hold watering without losing the skipped demand
- cycle-and-soak and max weekly runtime are enforced at the zone level

For the current calibration gate, see [docs/planner_calibration.md](planner_calibration.md). For the recommended beta rollout process, see [docs/beta_field_test.md](beta_field_test.md).

## Project Goal

Create a Home Assistant custom integration that turns the B-hyve Sprinkler Controller into a weather-aware irrigation system driven by:

- B-hyve controller and zone metadata
- Ambient Weather observations
- a per-zone water-balance model
- Home Assistant entities, services, and scheduling

Terminology note: in Home Assistant this should be implemented as a custom integration under `custom_components`, even if we casually call it a plugin.

## Recommended MVP

The first version should do four things well:

- authenticate to B-hyve and load the controller plus all zones
- expose each zone as a Home Assistant valve plus a few useful sensors
- pull weather observations from Ambient Weather
- compute a recommended runtime and optionally execute the next run from Home Assistant

This avoids the hardest part early on: mutating B-hyve schedule objects. We can still support that later if offline resilience becomes important.

## Architecture

### Core pieces

- `BhyveClient`
  Reads controller state, zones, recent events, and current run state.
  Starts and stops manual watering runs.

- `WeatherProvider`
  Abstract interface for observed weather inputs.
  MVP provider: Ambient Weather.
  Future provider: Home Assistant weather forecast entity for predictive skips.

- `IrrigationEngine`
  Computes daily or hourly zone water balance, recommended runtime, and skip reasons.

- `SchedulePlanner`
  Converts recommendations into a non-overlapping queue of zone runs.
  Applies quiet hours, soak breaks, maximum runtime, and global suspend rules.

- `Coordinator`
  Home Assistant `DataUpdateCoordinator` that refreshes B-hyve and weather data and publishes the merged state to entities.

### Recommended Home Assistant layout

- `custom_components/bhyve_auto_sprinklers_controller/__init__.py`
- `custom_components/bhyve_auto_sprinklers_controller/manifest.json`
- `custom_components/bhyve_auto_sprinklers_controller/config_flow.py`
- `custom_components/bhyve_auto_sprinklers_controller/coordinator.py`
- `custom_components/bhyve_auto_sprinklers_controller/api/bhyve.py`
- `custom_components/bhyve_auto_sprinklers_controller/weather/ambient.py`
- `custom_components/bhyve_auto_sprinklers_controller/irrigation/engine.py`
- `custom_components/bhyve_auto_sprinklers_controller/irrigation/planner.py`
- `custom_components/bhyve_auto_sprinklers_controller/valve.py`
- `custom_components/bhyve_auto_sprinklers_controller/sensor.py`
- `custom_components/bhyve_auto_sprinklers_controller/button.py`
- `custom_components/bhyve_auto_sprinklers_controller/switch.py`
- `custom_components/bhyve_auto_sprinklers_controller/number.py`
- `custom_components/bhyve_auto_sprinklers_controller/calendar.py`
- `custom_components/bhyve_auto_sprinklers_controller/services.yaml`
- `custom_components/bhyve_auto_sprinklers_controller/translations/en.json`

## Zone Model

Your B-hyve payload already maps nicely into an irrigation domain model.

### Fields we should store directly

- `zone_id`
- `zone_number`
- `name`
- `enabled`
- `crop_type`
- `garden_subtypes`
- `crop_coefficient`
- `manual_crop_coefficient`
- `root_depth`
- `manual_root_depth`
- `available_water_capacity`
- `manage_allow_depletion`
- `flow_rate`
- `efficiency`
- `exposure_type`
- `soil_type`
- `slope_type`
- `nozzle_type`
- `smart_duration`
- `smart_schedule_id`
- `latest_events`
- `soil_moisture_level_at_end_of_day_pct`

### How to interpret them

- `flow_rate`
  Treat this as precipitation rate in inches per hour. That matches B-hyve support documentation and the values in your sample such as `0.5`, `0.7`, and `1.5`.

- `crop_coefficient`
  Multiplies reference evapotranspiration to estimate crop water use.

- `available_water_capacity`
  Treat this as stored water per inch of soil depth. That is an inference from the B-hyve terminology and the sample values such as `0.15` and `0.2`.

- `manage_allow_depletion`
  The dryness threshold that triggers watering.

- `efficiency`
  Percent of applied water that actually benefits the zone.

- `smart_duration`
  Likely B-hyve's internally recommended runtime in seconds. We can expose it as a baseline sensor, but we should not make our algorithm depend on it.

## Watering Model

### Recommended water-balance approach

For each zone, track soil water deficit over time.

Use these values:

- `effective_root_depth_in`
  `manual_root_depth` if present and non-zero, otherwise `root_depth`

- `effective_crop_coefficient`
  `manual_crop_coefficient` if present and non-zero, otherwise `crop_coefficient`

- `total_available_water_in`
  `available_water_capacity * effective_root_depth_in`

- `readily_available_water_in`
  `total_available_water_in * (manage_allow_depletion / 100)`

- `effective_irrigation_in`
  `runtime_hours * flow_rate * (efficiency / 100)`

- `effective_rain_in`
  observed rain adjusted by a capture factor

- `daily_crop_use_in`
  `ETo * effective_crop_coefficient * exposure_multiplier`

Then update:

`new_deficit = clamp(old_deficit + daily_crop_use_in - effective_rain_in - effective_irrigation_in, 0, total_available_water_in)`

Water when:

- `new_deficit >= readily_available_water_in`
- the zone is enabled
- the controller is not suspended
- no skip rule blocks the run

### Why this fits your data

This uses the zone inputs you already have instead of inventing new ones:

- soil storage comes from `available_water_capacity` and `root_depth`
- plant demand comes from `crop_coefficient`
- watering threshold comes from `manage_allow_depletion`
- runtime conversion comes from `flow_rate`
- actual delivered water is reduced by `efficiency`
- sun/shade is adjusted from `exposure_type`

## Weather Inputs

### Ambient Weather

Ambient Weather is a good match for observed weather and station history.

Minimum observed inputs for MVP:

- rainfall
- temperature
- humidity

Preferred inputs for better scheduling:

- solar radiation or UV
- wind speed
- pressure

### Important limitation

The official Ambient Weather API docs we reviewed describe device listings, recent observations, history, and realtime websocket updates. I did not find forecast endpoints in those docs. Because of that, forecast-based skip logic should be designed as an optional second provider.

### Forecast provider for later

If you want "skip tomorrow because rain is coming", add a second provider that reads a Home Assistant `weather` entity.

That keeps the design clean:

- Ambient Weather for what is happening at your house
- HA forecast entity for what is expected next

## Entity Plan

### Device structure

- one Home Assistant device for the B-hyve sprinkler controller
- one valve entity per zone attached to the controller device
- one logical service layer for schedule and planner operations

### Entities for MVP

- `valve.<zone>`
  Manual run and stop control for each zone

- `sensor.<zone>_recommended_runtime`
  Suggested runtime in minutes for the next watering

- `sensor.<zone>_water_deficit`
  Current modeled soil water deficit

- `sensor.<zone>_last_watered`
  Latest event timestamp

- `sensor.<zone>_last_runtime`
  Latest event duration

- `sensor.<zone>_smart_duration`
  B-hyve-reported smart duration, exposed for comparison

- `switch.auto_irrigation`
  Global auto mode

- `button.recalculate_plan`
  Force a refresh and recompute

- `calendar.irrigation_plan`
  Upcoming scheduled zone runs

### Good follow-up entities

- `binary_sensor.rain_skip_active`
- `binary_sensor.wind_skip_active`
- `binary_sensor.freeze_skip_active`
- `number.<zone>_runtime_cap`
- `number.<zone>_seasonal_adjustment`
- `select.<zone>_strategy`

## Services

Recommended custom services:

- `bhyve_auto_sprinklers_controller.run_zone`
- `bhyve_auto_sprinklers_controller.stop_zone`
- `bhyve_auto_sprinklers_controller.stop_all`
- `bhyve_auto_sprinklers_controller.recalculate`
- `bhyve_auto_sprinklers_controller.skip_next_run`
- `bhyve_auto_sprinklers_controller.run_plan_now`

## Scheduling Rules

### MVP rules

- never run two zones at once
- skip disabled zones
- respect a configurable watering window
- avoid runs when measured rain in the last 24 hours exceeds a threshold
- cap any individual zone runtime

### V1 smart rules

- apply cycle/soak for clay soils and steeper slopes
- reduce watering in shade
- increase depletion rate for full-sun zones
- allow the vegetable garden to water more frequently than turf

### V2 smart rules

- use forecast rainfall and wind for proactive skips
- use seasonal multipliers
- support soak pauses across the entire schedule
- support priority rules so garden beds water before ornamental areas

## Cycle And Soak

Cycle/soak should be in scope early because your yard includes clay and clay loam zones.

Initial heuristic:

- clay plus spray heads: short cycles and long soak gaps
- drip zones: long single cycles or fewer cycles
- sloped zones: reduce max cycle length

B-hyve's own help center says cycle time is influenced by spray head type, soil type, and slope, while soak time is 30 minutes. That makes a strong default reference for your first implementation.

## Source Of Truth Strategy

Recommended default:

- B-hyve remains the source of truth for zone configuration
- Home Assistant stores only planner state, user options, and temporary overrides

This keeps the project simpler and reduces the chance of fighting the B-hyve app.

Later, you can add optional Home Assistant overrides for:

- zone enablement
- runtime multiplier
- maximum runtime
- exposure adjustment
- rain capture factor

## Risks And Tradeoffs

- B-hyve sprinkler control appears to rely on private or lightly documented cloud endpoints.
  I found official B-hyve API-key documentation and support pages, but I did not find official sprinkler endpoint documentation matching the payload you shared.

- Ambient Weather is excellent for observations, but forecast support may require a second provider.

- A bad irrigation algorithm can overwater fast.
  The integration should default to read-only recommendations before enabling full autonomous runs.

- If both B-hyve schedules and Home Assistant scheduling are active at the same time, you can double-water.
  The UI and docs should strongly steer users toward one scheduling owner.

## Suggested Phased Roadmap

### Phase 1

- repository scaffolding
- config flow
- B-hyve authentication
- controller and zone discovery
- read-only sensors

### Phase 2

- manual zone run and stop
- current run state
- event history exposure

### Phase 3

- Ambient Weather provider
- deficit model
- recommended runtime sensors
- calendar preview

### Phase 4

- automatic execution
- rain skip
- cycle/soak
- quiet hours

### Phase 5

- forecast provider
- smarter seasonal logic
- HA dashboards
- optional B-hyve schedule sync

## Immediate Implementation Decisions

Before coding, I would lock these in:

- Home Assistant owns the schedule for MVP
- B-hyve zone settings are read from the API and treated as defaults
- Ambient Weather is the observed-weather provider
- forecast is optional and comes later
- autonomous mode starts disabled until the recommendation model is validated

## References

- [Home Assistant integration file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure/)
- [Home Assistant config flow docs](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)
- [Home Assistant fetching data with DataUpdateCoordinator](https://developers.home-assistant.io/docs/integration_fetching_data)
- [Home Assistant valve entity docs](https://developers.home-assistant.io/docs/core/entity/valve)
- [Home Assistant calendar entity docs](https://developers.home-assistant.io/docs/core/entity/calendar/)
- [Home Assistant Ambient Weather Station integration](https://www.home-assistant.io/integrations/ambient_station/)
- [Home Assistant Ambient Weather Network integration](https://www.home-assistant.io/integrations/ambient_network/)
- [Ambient Weather official API docs landing page](https://ambientweather.com/faqs/question/view/id/1932/)
- [Ambient Weather official API repository](https://github.com/ambient-weather/api-docs)
- [B-hyve API key documentation](https://support.bhyve.com/hc/en-us/articles/16129834216731-Creating-an-API-Key)
- [B-hyve Sprinkler overview](https://support.bhyve.com/hc/en-us/articles/360052460671-About-B-hyve-Sprinkler-Controller)
- [B-hyve sprinkler automation actions](https://support.bhyve.com/hc/en-us/articles/34966658948507-B-hyve-Sprinkler-Automations)
- [B-hyve precipitation rate](https://support.bhyve.com/hc/en-us/articles/360050838332-What-is-Precipitation-Rate)
- [B-hyve efficiency](https://support.bhyve.com/hc/en-us/articles/360050838372-What-is-Efficiency-and-how-do-I-calculate-it)
- [B-hyve allowed depletion](https://support.bhyve.com/hc/en-us/articles/360050838232-What-does-Allowed-Depletion-mean)
- [B-hyve crop coefficient](https://support.bhyve.com/hc/en-us/articles/360051331671-What-does-Crop-Coefficient-mean)
- [B-hyve available water](https://support.bhyve.com/hc/en-us/articles/360051331631-What-does-Available-Water-mean)
- [B-hyve cycle/soak](https://support.bhyve.com/hc/en-us/articles/360051331371-Cycle-Soak)
- [B-hyve weather skips](https://support.bhyve.com/hc/en-us/articles/360050838432-What-are-weather-skips)
