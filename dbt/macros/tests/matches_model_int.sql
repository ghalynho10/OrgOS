{#- Spec 0007 AC-20 and AC-21: a model table holds exactly the rows of its
    model_int source, no more and no fewer, compared with EXCEPT ALL in both
    directions so a lost or doubled row shows too. `columns` maps each served
    expression (a column, a renamed column, or the rebuilt edge_id) to the
    model_int column it must equal; `served_where` keeps only the non null
    rows of a folded edge column. Each failing row says which side holds it.
    Called by hand with a copy relation as `model`, it shows a changed row
    (verify.md). -#}
{% test matches_model_int(model, int_model, columns, served_where=none) %}
with served as (
    select
    {%- for expression, name in columns.items() %}
        {{ expression }} as {{ name }}{{ "," if not loop.last }}
    {%- endfor %}
    from {{ model }}
    {%- if served_where %}
    where {{ served_where }}
    {%- endif %}
),

shared as (
    select {{ columns.values() | join(", ") }}
    from {{ int_model }}
),

only_in_model_int as (
    select * from shared
    except all
    select * from served
),

only_in_model as (
    select * from served
    except all
    select * from shared
)

select 'only in model_int' as side, * from only_in_model_int
union all
select 'only in model' as side, * from only_in_model
{% endtest %}
