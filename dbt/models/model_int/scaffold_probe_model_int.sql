-- Scaffold probe, disabled unless the safety tests enable it with
-- --vars '{orgos_scaffold_probes: true}'. Proves schema naming and grants.
-- It declares no columns, so the folder's contract is off for it.
{{ config(enabled=var('orgos_scaffold_probes', false), contract={'enforced': false}) }}

select 1 as probe_id
