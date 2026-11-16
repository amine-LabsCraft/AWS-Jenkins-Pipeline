select
    array_id,
    zone,
    inverter_id,
    capacity_kw,
    tilt_deg,
    commissioned_on,
    cleaning_weekday,
    case cleaning_weekday
        when 0 then 'Monday'
        when 1 then 'Tuesday'
        when 2 then 'Wednesday'
        when 3 then 'Thursday'
        when 4 then 'Friday'
        when 5 then 'Saturday'
        when 6 then 'Sunday'
    end                         as cleaning_day,
    cleaning_weekday is null    as robot_out_of_service
from {{ ref('arrays') }}
