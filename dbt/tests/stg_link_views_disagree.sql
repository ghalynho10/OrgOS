-- Spec 0006 AC-10: where a link ID is stored on both issues, both views give
-- the same src_id, dst_id, link type and labels. A disagreement stops the
-- build before issue_link and names the link ID.
select
    link_id,
    count(*) as views,
    count(distinct (src_id, dst_id, link_type_name, outward_label, inward_label)) as versions
from {{ ref('stg_link_entry') }}
group by link_id
having count(distinct (src_id, dst_id, link_type_name, outward_label, inward_label)) > 1
