-- One row per array and day. Mirrors sunops/reference.py::daily_performance.
with agg as (
    select
        array_id,
        zone,
        date_day,
        sum(energy_kwh)                                     as energy_kwh,
        sum(reference_kwh)                                  as reference_kwh,
        max(ac_power_kw)                                    as peak_power_kw,
        avg(case when is_daylight then module_temp_c end)   as avg_module_temp_c,
        sum(case when is_daylight then 1 else 0 end)        as daylight_intervals,
        sum(case when is_offline then 1 else 0 end)         as offline_intervals,
        sum(case when is_degraded then 1 else 0 end)        as degraded_intervals
    from {{ ref('int_interval_energy') }}
    group by 1, 2, 3
)

select
    array_id,
    zone,
    date_day,
    array_id || '|' || date_day                                                   as array_day_key,
    round(energy_kwh::numeric, 3)                                                 as energy_kwh,
    round(reference_kwh::numeric, 3)                                              as reference_kwh,
    round(peak_power_kw::numeric, 3)                                              as peak_power_kw,
    round(avg_module_temp_c::numeric, 3)                                          as avg_module_temp_c,
    daylight_intervals,
    offline_intervals,
    degraded_intervals,
    round((energy_kwh / nullif(reference_kwh, 0))::numeric, 4)                    as performance_ratio,
    round(coalesce(degraded_intervals::numeric / nullif(daylight_intervals, 0), 0), 4) as degraded_share
from agg
