-- Energy produced per interval, and the energy the same sunlight would give an ideal array
-- (the "reference yield"). Their ratio is the Performance Ratio, the standard PV health KPI.
{% set interval_h = var('interval_minutes') / 60 %}
{% set daylight = var('daylight_irradiance_wm2') %}

select
    t.array_id,
    t.zone,
    t.date_day,
    t.reading_ts,
    t.status,
    t.ac_power_kw,
    t.module_temp_c,
    t.irradiance_wm2,
    t.ac_power_kw * {{ interval_h }}                                 as energy_kwh,
    a.capacity_kw * t.irradiance_wm2 / 1000.0 * {{ interval_h }}     as reference_kwh,
    t.irradiance_wm2 > {{ daylight }}                                as is_daylight,
    t.irradiance_wm2 > {{ daylight }} and t.status = 'OFFLINE'       as is_offline,
    t.irradiance_wm2 > {{ daylight }} and t.status = 'DEGRADED'      as is_degraded
from {{ ref('stg_telemetry') }} as t
inner join {{ ref('arrays') }} as a on a.array_id = t.array_id
