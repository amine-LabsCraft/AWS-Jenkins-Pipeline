-- Which panels should the robots clean today, and what does the dust cost?
--
-- Weather hits every array the same way on a given day, so each array is compared with the
-- best array of that day (usually one that was just cleaned). The gap is the soiling loss.
-- An array that is DEGRADED most of the day has an electrical fault: cleaning it would waste
-- water and robot time, so it goes to INSPECT instead.
-- Mirrors sunops/reference.py::cleaning_recommendations.
with daily as (
    select
        *,
        max(performance_ratio) over (partition by date_day) as best_pr
    from {{ ref('fct_daily_performance') }}
),

scored as (
    select
        *,
        round(performance_ratio / nullif(best_pr, 0), 4)                        as relative_pr,
        round(greatest(reference_kwh * (best_pr - performance_ratio), 0), 2)    as lost_energy_kwh
    from daily
),

final as (
    select
        *,
        round((1 - relative_pr) * 100, 2) as soiling_loss_pct
    from scored
)

select
    array_id,
    zone,
    date_day,
    array_day_key,
    performance_ratio,
    relative_pr,
    soiling_loss_pct,
    lost_energy_kwh,
    round(lost_energy_kwh * {{ var('tariff_mad_per_kwh') }}, 2)     as lost_revenue_mad,
    case
        when degraded_share > {{ var('inspect_degraded_share') }}  then 'INSPECT'
        when soiling_loss_pct >= {{ var('clean_now_loss_pct') }}   then 'CLEAN_NOW'
        when soiling_loss_pct >= {{ var('schedule_loss_pct') }}    then 'SCHEDULE'
        else 'OK'
    end                                                             as recommendation
from final
