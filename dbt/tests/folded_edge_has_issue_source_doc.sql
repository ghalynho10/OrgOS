-- Spec 0007 AC-10: each of the five edge tables folded into model.issue keeps
-- its issue's source_doc_id, so folding the edge into a column loses no
-- provenance. A row here is a model_int defect (spec 0006 AC-23).
{%- set folded = ['in_project', 'assigned_to', 'reported_by', 'created_by', 'has_parent'] %}
{% for edge in folded %}
select '{{ edge }}' as edge_table, edge.edge_id, edge.source_doc_id, issue.source_doc_id as issue_source_doc_id
from {{ ref('int_' ~ edge) }} as edge
inner join {{ ref('int_issue') }} as issue on issue.issue_key = edge.src_id
where edge.source_doc_id <> issue.source_doc_id
{% if not loop.last %}union all{% endif %}
{%- endfor %}
