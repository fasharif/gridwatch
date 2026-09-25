-- The 48 half-hour slots of a UK local day. On clock-change days a slot can occur twice
-- (October) or not at all (March); facts keep UTC timestamps as their key for that reason.
select
    slot as time_key,
    printf('%02d:%02d', slot // 2, (slot % 2) * 30) as start_time_label,
    slot // 2 as hour_of_day,
    (slot % 2) * 30 as minute_of_hour,
    case
        when slot < 14 then 'Night (00:00-06:59)'
        when slot < 24 then 'Morning (07:00-11:59)'
        when slot < 32 then 'Afternoon (12:00-15:59)'
        when slot < 38 then 'Evening peak (16:00-18:59)'
        else 'Evening (19:00-23:59)'
    end as day_part,
    slot between 32 and 37 as is_evening_peak
from range(48) as slots(slot)
