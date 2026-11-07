-- Spec 0006 AC-14 (spec 0004 AC-7): every amount the rule uses is an integer
-- string, so a value that is not an integer stops the build before
-- logged_time, naming the issue and the history. Empty means 0 and passes.
with side as (
    select
        issue_key, history_id, item_number, 'from' as side,
        {{ used_amount('from_value', 'from_string') }} as used_value
    from {{ ref('stg_time_history') }}
    union all
    select
        issue_key, history_id, item_number, 'to',
        {{ used_amount('to_value', 'to_string') }}
    from {{ ref('stg_time_history') }}
)

select issue_key, history_id, item_number, side, used_value
from side
where coalesce(used_value, '') !~ '^-?[0-9]*$'
