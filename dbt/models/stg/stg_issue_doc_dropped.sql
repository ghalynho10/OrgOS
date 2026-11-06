-- The copies the collapse dropped, each with the copy kept for its key
-- (spec 0009 AC-7). The figure gate compares this list with the known answers.
with raw_doc as (
    select mongo_id::text as mongo_id, key::text as key
    from {{ source('raw', 'jira_issue') }}
),

kept as (
    select key, mongo_id from {{ ref('stg_issue_doc') }}
)

select
    raw_doc.key,
    kept.mongo_id as kept_mongo_id,
    raw_doc.mongo_id as dropped_mongo_id
from raw_doc
inner join kept on raw_doc.key = kept.key
where raw_doc.mongo_id <> kept.mongo_id
