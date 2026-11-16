select
    array_id,
    zone,
    date_trunc('hour', reading_ts)                         as hour_ts,
    array_id || '|' || date_trunc('hour', reading_ts)      as array_hour_key,
    round(sum(energy_kwh)::numeric, 3)                     as energy_kwh,
    round(sum(reference_kwh)::numeric, 3)                  as reference_kwh,
    round(avg(irradiance_wm2)::numeric, 1)                 as avg_irradiance_wm2,
    round(avg(module_temp_c)::numeric, 1)                  as avg_module_temp_c
from {{ ref('int_interval_energy') }}
group by 1, 2, 3, 4
