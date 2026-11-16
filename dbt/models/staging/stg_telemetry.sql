-- Light renaming and typing on top of Silver. One row per array and 15-minute interval.
select
    event_id,
    plant_id,
    zone,
    inverter_id,
    array_id,
    ts                              as reading_ts,
    cast(ts as date)                as date_day,
    irradiance_wm2,
    ambient_temp_c,
    module_temp_c,
    dc_voltage_v,
    dc_current_a,
    ac_power_kw,
    status,
    ingested_at
from {{ source('silver', 'telemetry') }}
