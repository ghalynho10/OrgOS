-- Scaffold probe, disabled unless the safety tests enable it with
-- --vars '{orgos_scaffold_probes: true}'. Proves schema naming and grants.
{{ config(enabled=var('orgos_scaffold_probes', false)) }}

select 1 as probe_id
