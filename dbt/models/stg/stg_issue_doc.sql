-- One document per issue key (spec 0009 AC-7, spec 0005 AC-26): of the copies
-- of a key, the one with the lowest mongo_id, compared as bytes (collate "C").
-- The copy test in dbt/tests proves every dropped copy is identical to the kept
-- one. Text columns are cast from dlt's varchar to text for the contracts.
with raw_doc as (
    select * from {{ source('raw', 'jira_issue') }}
),

kept as (
    select key, min(mongo_id collate "C") as mongo_id
    from raw_doc
    group by key
)

select
    raw_doc.mongo_id::text as mongo_id,
    raw_doc.key::text as key,
    raw_doc.id::text as id,
    raw_doc.self::text as self,
    raw_doc.expand::text as expand,
    raw_doc.fields,
    raw_doc.changelog,
    raw_doc._dlt_load_id::text as load_id
from raw_doc
inner join kept on raw_doc.mongo_id = kept.mongo_id collate "C"
