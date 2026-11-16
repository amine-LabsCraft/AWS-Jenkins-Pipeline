-- An array cannot produce more than an ideal array under the same sunlight (5 % sensor margin).
select array_day_key, energy_kwh, reference_kwh
from {{ ref('fct_daily_performance') }}
where reference_kwh > 0
  and energy_kwh > reference_kwh * 1.05
