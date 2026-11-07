{#- One to one edge from an issue to the person in one of its role objects
    (spec 0006 edge tables). A null or missing person key gives no row
    (AC-8); the edge keeps the issue document as its source (spec 0007 AC-10). -#}
{% macro role_edge(edge_type, role) -%}
select
    '{{ edge_type }}:' || key as edge_id,
    key as src_id,
    fields -> '{{ role }}' ->> 'key' as dst_id,
    '{{ edge_type }}' as edge_type,
    mongo_id as source_doc_id,
    load_id
from {{ ref('stg_issue_doc') }}
where fields -> '{{ role }}' ->> 'key' is not null
{%- endmacro %}
