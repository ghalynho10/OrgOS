-- model_int.person (spec 0006 AC-30, AC-31): every person in an assignee,
-- reporter or creator object, plus every time entry author. Role persons are
-- built from the distinct (key, login, active) rows, so a second value for one
-- key fails the primary key; the lowest mongo_id among the documents holding
-- the person in a role gives source_doc_id. A person seen only as a time entry
-- author has null login_name and is_active, and the lowest mongo_id among
-- their entries' documents, which count only when no role document exists.
with role_object as (
    select doc.mongo_id, doc.load_id, role.person
    from {{ ref('stg_issue_doc') }} as doc
    cross join lateral (
        values (doc.fields -> 'assignee'), (doc.fields -> 'reporter'), (doc.fields -> 'creator')
    ) as role (person)
    where role.person ->> 'key' is not null
),

role_attributes as (
    select distinct
        person ->> 'key' as person_key,
        person ->> 'name' as login_name,
        (person ->> 'active')::boolean as is_active,
        load_id
    from role_object
),

role_first_doc as (
    select person ->> 'key' as person_key, min(mongo_id collate "C") as source_doc_id
    from role_object
    group by person ->> 'key'
),

history_only as (
    select
        author_key as person_key,
        min(mongo_id collate "C") as source_doc_id,
        load_id
    from {{ ref('stg_time_history') }}
    where
        author_key is not null
        and author_key not in (select person_key from role_first_doc)
    group by author_key, load_id
)

select
    role_attributes.person_key,
    role_attributes.login_name,
    role_attributes.is_active,
    role_first_doc.source_doc_id,
    role_attributes.load_id
from role_attributes
inner join role_first_doc on role_attributes.person_key = role_first_doc.person_key

union all

select
    person_key,
    null::text as login_name,
    null::boolean as is_active,
    source_doc_id,
    load_id
from history_only
