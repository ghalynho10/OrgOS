-- model_int.project (spec 0006): one row per corpus project key. Built from
-- the distinct (key, name) pairs, so a second name for one key fails the
-- primary key instead of being collapsed (spec 0006 AC-31 as clarified by
-- spec 0009); the lowest mongo_id among its documents gives source_doc_id only.
with doc as (
    select
        mongo_id,
        load_id,
        fields -> 'project' ->> 'key' as project_key,
        fields -> 'project' ->> 'name' as project_name
    from {{ ref('stg_issue_doc') }}
),

attributes as (
    select distinct project_key, project_name, load_id
    from doc
),

first_doc as (
    select project_key, min(mongo_id collate "C") as source_doc_id
    from doc
    group by project_key
)

select
    attributes.project_key,
    attributes.project_name,
    first_doc.source_doc_id,
    attributes.load_id
from attributes
inner join first_doc on attributes.project_key = first_doc.project_key
