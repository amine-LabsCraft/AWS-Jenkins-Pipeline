-- B-INV2-S3 has a simulated electrical fault. The mart must send it to inspection, never to cleaning.
-- This guards the business rule against a refactor that would reorder the CASE branches.
select array_day_key, recommendation
from {{ ref('mart_cleaning_recommendations') }}
where array_id = 'B-INV2-S3'
  and recommendation in ('CLEAN_NOW', 'SCHEDULE')
