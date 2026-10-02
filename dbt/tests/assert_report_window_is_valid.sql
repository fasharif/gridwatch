select *
from {{ ref('rpt_report_window') }}
where start_date > end_date
