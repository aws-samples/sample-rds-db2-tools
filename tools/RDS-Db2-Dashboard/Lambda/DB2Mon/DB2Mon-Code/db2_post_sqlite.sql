update env_get_system_resources_diff set ts_delta = (select max(ts_delta) from env_get_system_resources_diff) where ts_delta is null;
update mon_get_bufferpool_diff set ts_delta = (select max(ts_delta) from mon_get_bufferpool_diff) where ts_delta is null;
update mon_get_cf_cmd_diff set ts_delta = (select max(ts_delta) from mon_get_cf_cmd_diff) where ts_delta is null;
update mon_get_cf_wait_time_diff set ts_delta = (select max(ts_delta) from mon_get_cf_wait_time_diff) where ts_delta is null;
-- For volatile-key tables (connection, pkg_cache_stmt), unmatched rows carry raw cumulative
-- values from the NOT EXISTS branch. These cause ratio blow-ups after workload restarts.
-- Delete them before ts_delta is patched so they are identifiable (ts_delta still NULL).
delete from mon_get_connection_diff where ts_delta is null;
delete from mon_get_pkg_cache_stmt_diff where ts_delta is null;
update mon_get_connection_diff set ts_delta = (select max(ts_delta) from mon_get_connection_diff) where ts_delta is null;
-- Delete matched connection rows where the diff values are implausible:
-- client_idle_wait_time > ts_delta * 1000ms means the connection was idle for longer
-- than the entire monitoring interval -- impossible for a genuine diff, indicates
-- handle reuse where start=fresh connection, end=connection after workload stop.
delete from mon_get_connection_diff
where client_idle_wait_time > ts_delta * 1000
   or total_rqst_time < 0
   or client_idle_wait_time < 0;
update mon_get_extended_latch_wait_diff set ts_delta = (select max(ts_delta) from mon_get_extended_latch_wait_diff) where ts_delta is null;
update mon_get_group_bufferpool_diff set ts_delta = (select max(ts_delta) from mon_get_group_bufferpool_diff) where ts_delta is null;
update mon_get_index_diff set ts_delta = (select max(ts_delta) from mon_get_index_diff) where ts_delta is null;
update mon_get_page_access_info_diff set ts_delta = (select max(ts_delta) from mon_get_page_access_info_diff) where ts_delta is null;
update mon_get_pkg_cache_stmt_diff set ts_delta = (select max(ts_delta) from mon_get_pkg_cache_stmt_diff) where ts_delta is null;
-- Same handle-reuse guard for pkg cache: coord_stmt_exec_time cannot exceed ts_delta * 1000ms
delete from mon_get_pkg_cache_stmt_diff
where coord_stmt_exec_time > ts_delta * 1000
   or coord_stmt_exec_time < 0
   or total_act_time < 0;
update mon_get_table_diff set ts_delta = (select max(ts_delta) from mon_get_table_diff) where ts_delta is null;
update mon_get_tablespace_diff set ts_delta = (select max(ts_delta) from mon_get_tablespace_diff) where ts_delta is null;
update mon_get_transaction_log_diff set ts_delta = (select max(ts_delta) from mon_get_transaction_log_diff) where ts_delta is null;
update mon_get_workload_diff set ts_delta = (select max(ts_delta) from mon_get_workload_diff) where ts_delta is null;
