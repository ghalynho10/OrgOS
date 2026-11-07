-- model_int.logged_time (spec 0006 AC-14 to AC-16): one row per time entry,
-- person to issue. The amount is to minus from by the spec 0004 AC-7 rule,
-- two items of one history summed into one entry, negatives kept with their
-- sign. logged_at is the history's created, converted to UTC from its offset.
select
    'logged_time:' || history_id as edge_id,
    author_key as src_id,
    issue_key as dst_id,
    'logged_time' as edge_type,
    history_id,
    sum({{ item_seconds('from_value', 'from_string', 'to_value', 'to_string') }})::bigint as seconds,
    {{ to_utc_instant('created') }} as logged_at,
    mongo_id as source_doc_id,
    load_id
from {{ ref('stg_time_history') }}
group by history_id, author_key, issue_key, created, mongo_id, load_id
