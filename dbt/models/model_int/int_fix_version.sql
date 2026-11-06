-- model_int.fix_version (spec 0006): one row per fix version found on corpus
-- issues. Built from the distinct (id, name, released) values, so a version id
-- with two values fails the primary key instead of being collapsed; the lowest
-- mongo_id among its documents gives source_doc_id (AC-31).
with version as (
    select
        doc.mongo_id,
        doc.load_id,
        fix.value ->> 'id' as version_id,
        fix.value ->> 'name' as version_name,
        (fix.value ->> 'released')::boolean as is_released
    from {{ ref('stg_issue_doc') }} as doc
    cross join lateral {{ array_elements("doc.fields -> 'fixVersions'") }} as fix (value)
),

attributes as (
    select distinct version_id, version_name, is_released, load_id
    from version
),

first_doc as (
    select version_id, min(mongo_id collate "C") as source_doc_id
    from version
    group by version_id
)

select
    attributes.version_id,
    attributes.version_name,
    attributes.is_released,
    first_doc.source_doc_id,
    attributes.load_id
from attributes
inner join first_doc on attributes.version_id = first_doc.version_id
