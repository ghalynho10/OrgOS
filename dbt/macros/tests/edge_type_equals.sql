{#- Spec 0006 AC-11 and the declared type column: `edge_type` is the table
    name for every edge table (expected: "'in_project'"), and the link type
    for issue_link (expected: link_type_name). Feature 9 reads this column,
    and spec 0007 AC-20 does not compare it, so it is tested here. -#}
{% test edge_type_equals(model, expected) %}
select edge_id, edge_type
from {{ model }}
where edge_type is distinct from {{ expected }}
{% endtest %}
