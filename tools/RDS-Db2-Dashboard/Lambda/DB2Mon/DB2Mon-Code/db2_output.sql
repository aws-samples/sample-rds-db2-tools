--START_EXSQL Removed as not implemented
/*START_LOCKW*/select tbspace, tabname, lock_obj_type,lock_name,lock_mode,lock_status,member,apphdl from mon_get_locks_start;
/*START_EXUTL*/select
  member,
  coord_member,
  application_handle,
  utility_start_time,
  utility_type,
  utility_operation_type,
  cast(substr(utility_detail,1,100) as varchar(100)) utility_detail
from
  mon_get_utility_start
order by
  utility_start_time asc
;
--END_EXSQL Removed as not implemented
/*END_LOCKW*/select tbspace, tabname,lock_obj_type,
  lock_name,
  lock_mode,
  lock_status,
  member,
  apphdl
from
  mon_get_locks_end
where
  lock_name in (select lock_name from mon_get_locks_end where lock_status = 'W')
order by
  lock_obj_type, lock_name, lock_status, lock_mode
;
/*END_EXUTL*/select
  member,
  coord_member,
  application_handle,
  utility_start_time,
  utility_type,
  utility_operation_type,
  cast(substr(utility_detail,1,100) as varchar(100)) utility_detail
from
  mon_get_utility_end
order by
  utility_start_time asc
;
/*DB_THRUP*/select
   min(ts_delta) ts_delta,
   member,
   decimal((sum(act_completed_total) / float(min(ts_delta))), 10, 1) as act_per_s,
   decimal((sum(total_app_commits) / float(min(ts_delta))), 10, 1) as cmt_per_s,
   decimal((sum(total_app_rollbacks) / float(min(ts_delta))), 10, 1) as rb_per_s,
   decimal((sum(deadlocks) / float(min(ts_delta))), 10, 1) as ddlck_per_s,
   decimal((sum(select_sql_stmts) / float(min(ts_delta))), 10, 1) as sel_p_s,
   decimal((sum(uid_sql_stmts) / float(min(ts_delta))), 10, 1) as uid_p_s,
   decimal((sum(rows_inserted) / float(min(ts_delta))), 10, 1) as rows_ins_p_s,
   decimal((sum(rows_updated) / float(min(ts_delta))), 10, 1) as rows_upd_p_s,
   decimal((sum(rows_returned) / float(min(ts_delta))), 10, 1) as rows_ret_p_s,
   decimal((sum(rows_modified) / float(min(ts_delta))), 10, 1) as rows_mod_p_s,
   decimal((sum(pkg_cache_inserts) / float(min(ts_delta))), 10, 1) as pkg_cache_ins_p_s,
   decimal((sum((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads)) / float(min(ts_delta))) , 10, 1) as p_rd_per_s
from
   mon_get_workload_diff
where
   ts_delta > 0
group by
   member
order by
   member asc
;

/*DB_TIMEB*/select
   member,
   integer(sum(total_rqst_time)) as total_rqst_tm,
   decimal(sum(total_compile_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_compile,
   decimal(sum(total_section_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_section,
   decimal(sum(total_section_sort_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_sort,
decimal(sum(total_col_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_col,
decimal(sum(total_col_synopsis_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_col_synop,
decimal(sum(total_commit_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_commit,
decimal(sum(total_rollback_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_rback,
decimal(sum(total_connect_request_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_conn,
decimal(sum(total_routine_user_code_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_rtn_usr_code,
decimal(sum(total_backup_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_backup,
decimal(sum(total_index_build_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_idx_bld,
   decimal(sum(total_runstats_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_runstats,
   decimal(sum(total_reorg_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_reorg,
   decimal(sum(total_load_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_load
from
   mon_get_workload_diff
group by
   member
order by
   member asc
;
/*DB_WAITT*/select
   w.member,
   integer(sum(total_rqst_time)) as total_rqst_tm,
   integer(sum(total_wait_time)) as total_wait_tm,
   integer(sum(total_cpu_time)) as total_cpu_tm,
   decimal((sum(total_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_rqst_wait,
   decimal((sum(lock_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_lock,
   decimal((sum(lock_wait_time_global) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_glb_lock,
   decimal((sum(total_extended_latch_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_ltch,
   decimal((sum(log_disk_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_lg_dsk,
   decimal((sum(log_buffer_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_lg_buf,
   decimal((sum(reclaim_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_rclm,
   decimal((sum(cf_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_cf,
   decimal((sum(prefetch_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_pftch,
   decimal((sum(diaglog_write_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_diag,
   decimal((sum(audit_file_write_wait_time) / double(sum(total_rqst_time))) * 100, 5, 2) as pct_aud_w,
   decimal((sum(audit_subsystem_wait_time) / double(sum(total_rqst_time))) * 100, 5, 2) as pct_aud_ss,
   decimal((sum(evmon_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_evmon,
   -- fcm_recv_wait_time = fcm_message_recv_wait_time + fcm_tq_recv_wait_time (similarly for send)
   -- decimal((sum(fcm_message_recv_wait_time + fcm_message_send_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_fcm_msg,
   -- decimal((sum(fcm_tq_recv_wait_time + fcm_tq_send_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_fcm_tq,
   decimal((sum(comm_exit_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_commexit,
   decimal((sum(lob_prefetch_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_lob_pftch,
   decimal((sum(ext_table_recv_wait_time + ext_table_send_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_extbl,
   decimal((sum(fed_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_fed,
   decimal((sum(pool_read_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_pool_r,
   decimal((sum(pool_write_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_pool_w,
   decimal((sum(direct_read_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_dir_r,
   decimal((sum(direct_write_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_dir_w,
   decimal((sum(fcm_recv_wait_time + fcm_send_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_fcm,
   decimal((sum(tcpip_send_wait_time + tcpip_recv_wait_time) / float(sum(total_rqst_time))) * 100, 5, 2) as pct_tcpip,
   decimal((sum(ida_send_wait_time + ida_recv_wait_time) / double(sum(total_rqst_time))) * 100, 5, 2) as pct_ida
from
   mon_get_workload_diff w
group by
   w.member
order by
   w.member asc
;
/*DB_PROCT*/select
   member,
   integer(sum(total_rqst_time)) as total_rqst_tm,
   integer(sum(total_rqst_time) - sum(total_wait_time)) as total_proc_time,
   decimal(sum(total_compile_proc_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_comp_proc,
   decimal(sum(total_section_proc_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_sect_proc,
   decimal(sum(total_section_sort_proc_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_sect_sort_proc,
   decimal(sum(total_commit_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_commit,
   decimal(sum(total_rollback_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_rback,
decimal(sum(total_col_proc_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_col_proc,
decimal(sum(total_connect_request_proc_time) / float(sum(total_rqst_time)) * 100, 5, 2) as pct_conn_proc
from
   mon_get_workload_diff
group by
   member
order by
   member asc
;
/*DB_SORT*/
select
   member,
   integer(sum(total_sorts)) total_sorts,
   integer(sum(sort_overflows)) sort_overflows,
   sum(total_section_sort_time) tot_sect_sort_tm,
   sum(total_section_sort_proc_time) tot_sect_sort_proc_tm
   ,
   sum(sort_shrheap_allocated) sort_shrheap_allocated,
   integer(sum(total_hash_joins)) total_hsjn,
   integer(sum(hash_join_overflows)) hsjn_ovfl,
   integer(sum(post_threshold_hash_joins)) pst_thr_hsjn,
   integer(sum(post_shrthreshold_hash_joins)) pst_shrthr_hsjn
from
   mon_get_workload_diff
group by
   member
order by
   member asc
;
/*SQL_TOPEXECT*/
select
   member,
   integer(num_exec_with_metrics) as num_exec,
   m.coord_stmt_exec_time,
   decimal(m.coord_stmt_exec_time / double(num_exec_with_metrics), 10, 2) as avg_coord_exec_time,
   decimal( (m.coord_stmt_exec_time / double(total_coord_stmt_exec_time)) * 100, 5, 2 ) as pct_coord_stmt_exec_time,
   m.total_act_time,
   total_cpu_time,
   total_cpu_time / num_exec_with_metrics as avg_cpu_time,
   case when total_act_time > 0
      then decimal( (total_act_wait_time / double(total_act_time)) * 100, 5, 2 )
      else 0
   end as pct_wait_time,
   decimal(total_section_time / double(num_exec_with_metrics), 20, 2) as avg_sect_time,
decimal(total_col_time / double(num_exec_with_metrics), 20, 2) as avg_col_time,
   effective_isolation as iso,
   replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
   mon_get_pkg_cache_stmt_diff m,
   (select sum(coord_stmt_exec_time) as total_coord_stmt_exec_time from mon_get_pkg_cache_stmt_diff where coord_stmt_exec_time > 0),
   (select executable_id, coord_stmt_exec_time
    from mon_get_pkg_cache_stmt_diff
    where coord_stmt_exec_time <> 0
    order by coord_stmt_exec_time desc
    limit 100) c
where
   (total_act_time <> 0 or m.coord_stmt_exec_time <> 0) and num_exec_with_metrics <> 0 and c.executable_id = m.executable_id
order by
   -- Order by c.coord_stmt_exec_time not m.coord_stmt_exec_time (c has coord value, m has member value which could be zero)
   -- Doing this has the nice effect of grouping member executions of the same statement together.
   c.coord_stmt_exec_time desc, total_act_time desc, member asc
;
/*SQL_TOPEXECP*/
select
   member,
   count(*) num_stmts,
   integer(sum(num_exec_with_metrics)) total_exec,
   sum(m.coord_stmt_exec_time) coord_stmt_exec_time,
   decimal(sum(m.coord_stmt_exec_time) / double(sum(num_exec_with_metrics)), 10, 2) as avg_coord_exec_time,
   decimal( (sum(m.coord_stmt_exec_time) / double(max(total_coord_stmt_exec_time))) * 100, 5, 2 ) as pct_coord_stmt_exec_time,
   sum(m.total_act_time) total_act_time,
   sum(total_cpu_time) total_cpu_time,
   sum(total_cpu_time) / sum(num_exec_with_metrics) as avg_cpu_time,
   case when sum(total_act_time) > 0
      then decimal( (sum(total_act_wait_time) / double(sum(total_act_time))) * 100, 5, 2 )
      else 0
   end as pct_wait_time,
   decimal(sum(total_section_time) / double(sum(num_exec_with_metrics)), 20, 2) as avg_sect_time,
   decimal(sum(total_col_time) / double(sum(num_exec_with_metrics)), 20, 2) as avg_col_time,
   ( select replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
     from mon_get_pkg_cache_stmt_diff mgpcs4planid
     where mgpcs4planid.member = m.member and mgpcs4planid.planid = m.planid
     limit 1 ) as stmt_text
from
   mon_get_pkg_cache_stmt_diff m,
   (select sum(coord_stmt_exec_time) as total_coord_stmt_exec_time from mon_get_pkg_cache_stmt_diff where coord_stmt_exec_time > 0),
   (select planid, sum(coord_stmt_exec_time) as total_coord_stmt_exec_time_planid
    from mon_get_pkg_cache_stmt_diff
    where coord_stmt_exec_time <> 0
    group by planid
    order by sum(coord_stmt_exec_time) desc
    limit 100) c
where
   (total_act_time <> 0 or m.coord_stmt_exec_time <> 0) and num_exec_with_metrics <> 0 and c.planid = m.planid
group by
   member, m.planid
order by
   sum(c.total_coord_stmt_exec_time_planid) desc, total_act_time desc, member asc
;
/*PKG_EXECT*/
select
    member,
    cast(substr(package_name,1,20) as varchar(20)) as package_name,
    sum(num_exec_with_metrics) as num_stmts_exec,
    sum(coord_stmt_exec_time) as coord_stmt_exec_time
from
    mon_get_pkg_cache_stmt_diff
where
    coord_stmt_exec_time > 0
group by
    member, package_name
order by
    coord_stmt_exec_time desc
limit 100
;
/*SQL_TOPWAITT*/
select
   member,
   decimal((total_act_wait_time / double(total_act_time)) * 100, 5, 2) as pct_wait,
   decimal((log_disk_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lg_dsk,
   decimal((log_buffer_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lg_buf,
   decimal((lock_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lock,
   decimal((lock_wait_time_global / double(total_act_time)) * 100, 5, 2) as pct_glb_lock,
   decimal((total_extended_latch_wait_time / double(total_act_time)) * 100, 5, 2) as pct_ltch,
   decimal((reclaim_wait_time / double(total_act_time)) * 100, 5, 2) as pct_rclm,
   decimal((cf_wait_time / double(total_act_time)) * 100, 5, 2) as pct_cf,
   decimal((prefetch_wait_time / double(total_act_time)) * 100, 5, 2) as pct_pftch,
   decimal((diaglog_write_wait_time / double(total_act_time)) * 100, 5, 2) as pct_diag,
   decimal((pool_read_time / double(total_act_time)) * 100, 5, 2) as pct_pool_r,
   decimal((direct_read_time / double(total_act_time)) * 100, 5, 2) as pct_dir_r,
   decimal((direct_write_time / double(total_act_time)) * 100, 5, 2) as pct_dir_w,
   decimal(((fcm_recv_wait_time+fcm_send_wait_time) / double(total_act_time)) * 100, 5, 2) as pct_fcm,
   decimal((audit_file_write_wait_time / double(total_act_time)) * 100, 5, 2) as pct_aud_w,
   decimal((audit_subsystem_wait_time / double(total_act_time)) * 100, 5, 2) as pct_aud_ss,
   decimal((evmon_wait_time / double(total_act_time)) * 100, 5, 2) as pct_evmon,
   decimal((comm_exit_wait_time / double(total_act_time)) * 100, 5, 2) as pct_commexit,
   decimal((lob_prefetch_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lob_pftch,
   decimal(((ext_table_recv_wait_time + ext_table_send_wait_time) / double(total_act_time)) * 100, 5, 2) as pct_extbl,
   decimal((fed_wait_time / double(total_act_time)) * 100, 5, 2) as pct_fed,
   decimal(((ida_send_wait_time + ida_recv_wait_time) / double(total_act_time)) * 100, 5, 2) as pct_ida,
   replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
   mon_get_pkg_cache_stmt_diff m,
   (select executable_id, coord_stmt_exec_time
    from mon_get_pkg_cache_stmt_diff
    where coord_stmt_exec_time <> 0
    order by coord_stmt_exec_time desc
    limit 100) c
where
   total_act_time <> 0 and num_exec_with_metrics <> 0 and c.executable_id = m.executable_id
order by
   c.coord_stmt_exec_time desc, total_act_time desc, member asc
;
/*SQL_TOPWAITW*/
select
   member,
   decimal((total_act_wait_time / double(total_act_time)) * 100, 5, 2) as pct_wait,
   decimal((log_disk_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lg_dsk,
   decimal((log_buffer_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lg_buf,
   decimal((lock_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lock,
   decimal((lock_wait_time_global / double(total_act_time)) * 100, 5, 2) as pct_glb_lock,
   decimal((total_extended_latch_wait_time / double(total_act_time)) * 100, 5, 2) as pct_ltch,
   decimal((reclaim_wait_time / double(total_act_time)) * 100, 5, 2) as pct_rclm,
   decimal((cf_wait_time / double(total_act_time)) * 100, 5, 2) as pct_cf,
   decimal((prefetch_wait_time / double(total_act_time)) * 100, 5, 2) as pct_pftch,
   decimal((diaglog_write_wait_time / double(total_act_time)) * 100, 5, 2) as pct_diag,
   decimal((pool_read_time / double(total_act_time)) * 100, 5, 2) as pct_pool_r,
   decimal((direct_read_time / double(total_act_time)) * 100, 5, 2) as pct_dir_r,
   decimal((direct_write_time / double(total_act_time)) * 100, 5, 2) as pct_dir_w,
   decimal(((fcm_recv_wait_time+fcm_send_wait_time) / double(total_act_time)) * 100, 5, 2) as pct_fcm,
   decimal((audit_file_write_wait_time / double(total_act_time)) * 100, 5, 2) as pct_aud_w,
   decimal((audit_subsystem_wait_time / double(total_act_time)) * 100, 5, 2) as pct_aud_ss,
   decimal((evmon_wait_time / double(total_act_time)) * 100, 5, 2) as pct_evmon,
   decimal((comm_exit_wait_time / double(total_act_time)) * 100, 5, 2) as pct_commexit,
   decimal((lob_prefetch_wait_time / double(total_act_time)) * 100, 5, 2) as pct_lob_pftch,
   decimal(((ext_table_recv_wait_time + ext_table_send_wait_time) / double(total_act_time)) * 100, 5, 2) as pct_extbl,
   decimal((fed_wait_time / double(total_act_time)) * 100, 5, 2) as pct_fed,
   decimal(((ida_send_wait_time + ida_recv_wait_time) / double(total_act_time)) * 100, 5, 2) as pct_ida,
   replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
   mon_get_pkg_cache_stmt_diff m,
   (select executable_id, sum(total_act_wait_time) sum_members_total_act_wait_time
    from mon_get_pkg_cache_stmt_diff
		group by executable_id
    order by sum(total_act_wait_time) desc
    limit 100) c
where
   total_act_wait_time <> 0 and num_exec_with_metrics <> 0 and c.executable_id = m.executable_id
order by
   sum_members_total_act_wait_time desc, total_act_wait_time desc, member asc
;
/*SQL_TOPIOSTA*/
select
   member,
   integer(num_exec_with_metrics) num_exec,
   decimal(pool_data_l_reads/double(num_exec_with_metrics), 16,1) as avg_d_lrd,
   decimal(pool_data_p_reads/double(num_exec_with_metrics), 10,1) as avg_d_prd,
   decimal(pool_index_l_reads/double(num_exec_with_metrics), 16,1) as avg_i_lrd,
   decimal(pool_index_p_reads/double(num_exec_with_metrics), 10,1) as avg_i_prd,
   decimal(pool_temp_data_p_reads/double(num_exec_with_metrics), 10,1) as avg_td_prd,
   decimal(pool_temp_index_p_reads/double(num_exec_with_metrics), 10,1) as avg_ti_prd,
decimal(pool_col_l_reads/double(num_exec_with_metrics), 16,1) as avg_col_lrd,
decimal(pool_col_p_reads/double(num_exec_with_metrics), 10,1) as avg_col_prd,
   decimal(direct_read_reqs/double(num_exec_with_metrics), 8,1) avg_dir_r_rqs,
   decimal(direct_write_reqs/double(num_exec_with_metrics), 8,1) avg_dir_w_rqs,
   replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
   mon_get_pkg_cache_stmt_diff m,
   (select executable_id, coord_stmt_exec_time
    from mon_get_pkg_cache_stmt_diff
    where coord_stmt_exec_time <> 0
    order by coord_stmt_exec_time desc
    limit 100) c
where
   (total_act_time <> 0 or m.coord_stmt_exec_time <> 0) and num_exec_with_metrics <> 0 and c.executable_id = m.executable_id
order by
   c.coord_stmt_exec_time desc, total_act_time desc, member asc
;
/*SQL_TOPROWS*/
select
  member,
  integer(num_exec_with_metrics) as num_exec,
  decimal(rows_modified/double(num_exec_with_metrics), 12,1) as avg_rows_mod,
  decimal(rows_read/double(num_exec_with_metrics), 12,1) as avg_rows_read,
  decimal(rows_returned/double(num_exec_with_metrics), 12,1) as avg_rows_ret,
col_synopsis_rows_inserted,
  replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
  mon_get_pkg_cache_stmt_diff m,
  (select executable_id, coord_stmt_exec_time
   from mon_get_pkg_cache_stmt_diff
   where coord_stmt_exec_time <> 0
   order by coord_stmt_exec_time desc
   limit 100) c
where
  (total_act_time <> 0 or m.coord_stmt_exec_time <> 0) and num_exec_with_metrics <> 0 and c.executable_id = m.executable_id
order by
  c.coord_stmt_exec_time desc, total_act_time desc, member asc
;
/*SQL_TOPSORT*/
select
  member,
  case when total_act_time > 0
    then decimal((total_section_sort_time / double(total_act_time)) * 100, 5, 2)
    else 0
  end as pct_sort_time,
  decimal(total_sorts / double(num_exec_with_metrics), 8,1) as avg_tot_sorts,
  decimal(sort_overflows / double(num_exec_with_metrics), 8,1) as avg_sort_ovflws,
  integer(active_sorts_top) active_sorts_top,
  integer(sort_heap_top) sort_heap_top,
  replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
  mon_get_pkg_cache_stmt_diff m,
  (select executable_id, coord_stmt_exec_time
   from mon_get_pkg_cache_stmt_diff
   where coord_stmt_exec_time <> 0
   order by coord_stmt_exec_time desc
   limit 100) c
where
  (total_act_time <> 0 or m.coord_stmt_exec_time <> 0) and num_exec_with_metrics <> 0 and c.executable_id = m.executable_id
order by
  c.coord_stmt_exec_time desc, total_act_time desc, member asc
;
/*INF_EXPLN*/
select
  member,
  executable_id,
  planid,
  stmtid,
  semantic_env_id,
  replace(replace(cast(substr(stmt_text,1,200) as varchar(200)), chr(10), ' '), chr(13), ' ') as stmt_text
from
        mon_get_pkg_cache_stmt_diff
where
        coord_stmt_exec_time <> 0
order by
        coord_stmt_exec_time desc
limit 100
;
/*DB_SYSRE*/
select
   member,
   cast(substr(os_name,1,8) as varchar(8)) as os,
   cast(substr(host_name,1,16) as varchar(16)) host_name,
   cast(substr(os_version,1,8) as varchar(8)) os_ver,
   cast(substr(os_release,1,8) as varchar(8)) os_rel,
   smallint(cpu_total) cpu_tot,
   smallint(cpu_online) cpu_onl,
   smallint(cpu_configured) cpu_cfg,
   integer(cpu_speed) cpu_speed,
   smallint(cpu_hmt_degree) cpu_hmt,
   integer(memory_total) memory_total,
   integer(memory_free) memory_free,
   decimal(cpu_load_short,6,1) cpu_load_shrt,
   decimal(cpu_load_medium,6,1) cpu_load_med,
   decimal(cpu_load_long,6,1) cpu_load_lng,
   decimal(cpu_usage_total,6,1) cpu_usage_tot,
   integer(swap_pages_in) swap_pages_in,
   integer(swap_pages_out) swap_pages_out
from
   env_get_system_resources_diff
order by
   member
;
/*DB_LOGWR*/
select
   member,
   num_log_write_io,
   case when ts_delta > 0
      then decimal( double(num_log_write_io) / ts_delta, 10, 4 )
      else null
   end as log_write_io_per_s,
   case when ts_delta > 0
      then decimal( double(log_writes*4096/1024/1024) / ts_delta, 10, 4 )
      else null
   end as log_write_MB_per_s,
   log_write_time,
   case when num_log_write_io > 0
      then decimal( double(log_write_time) / num_log_write_io, 10, 4 )
      else null
   end as log_write_time_per_io_ms,
   num_log_buffer_full
from
   mon_get_transaction_log_diff
order by
   member asc
;
/*DB_LOGRE*/
select
   member,
   integer(num_log_read_io) num_log_read_io,
   case when ts_delta > 0
      then decimal( double(num_log_read_io) / ts_delta, 10, 4 )
      else null
   end as log_read_io_per_s,
   integer(log_reads) log_reads,
   integer(log_read_time) log_read_time,
   case when num_log_read_io > 0
      then decimal( double(log_read_time) / num_log_read_io, 10, 4 )
      else null
   end as log_read_time_per_io_ms,
   integer(num_log_data_found_in_buffer) num_log_data_in_buffer,
   integer(cur_commit_log_buff_log_reads) cur_com_log_buff_log_reads,
   integer(cur_commit_disk_log_reads) cur_com_disk_log_reads
from
   mon_get_transaction_log_diff
order by
   member asc
;
/*DB_LOGST*/
select
   member,
   num_log_write_io,
   num_log_part_page_io,
   case when num_log_part_page_io > 0
      then decimal( double(num_log_part_page_io) / num_log_write_io, 10, 4 )
      else null
   end as log_part_page_ratio,
   case when log_hadr_waits_total > 0
      then decimal( double(log_hadr_wait_time) / log_hadr_waits_total, 10, 4 )
      else null
   end as avg_log_hadr_wait_time
from
   mon_get_transaction_log_diff
order by
   member asc
;
/*TSP_DSKIO*/
select
   member,
   cast(substr(tbsp_name,1,20) as varchar(20)) as tbsp_name,
   -- Reads
   (pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) as num_reads,
   case when ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads)) > 0
      then decimal( pool_read_time / double((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads)), 10, 2 )
      else null
   end as avg_read_time,
   direct_read_reqs,
   case when direct_read_reqs > 0
      then decimal( direct_read_time / direct_read_reqs, 10, 2 )
      else null
   end as avg_drct_read_time,
   -- Writes
   (pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) as num_writes,
   case when ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes)) > 0
      then decimal( pool_write_time / double((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes)), 10, 2 )
      else null
   end as avg_write_time,
   direct_write_reqs,
   case when direct_write_reqs > 0
      then decimal( direct_write_time / direct_write_reqs, 10, 2 )
      else null
   end as avg_drct_write_time
from
   mon_get_tablespace_diff
where
   ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) + direct_read_reqs + (pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) + direct_write_reqs) >= ts_delta
order by
   ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) + direct_read_reqs + (pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) + direct_write_reqs) desc
;
/*TSP_DSKIOSYNC*/
select
   member,
   cast(substr(tbsp_name,1,20) as varchar(20)) as tbsp_name,
   -- Reads
   ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) as num_reads,
   case when (((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads))) > 0
      then decimal( (pool_read_time - pool_async_read_time) / double(((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads))), 10, 2 )
      else null
   end as avg_sync_read_time,
   -- Writes
   ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)) as num_writes,
   case when (((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes))) > 0
      then decimal( (pool_write_time - pool_async_write_time) / double(((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes))), 10, 2 )
      else null
   end as avg_sync_write_time
from
   mon_get_tablespace_diff
where
   (((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) + ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes))) >= ts_delta
order by
   (((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) + ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes))) desc
;
/*TSP_SKIOASYNC*/
select
   member,
   cast(substr(tbsp_name,1,20) as varchar(20)) as tbsp_name,
   -- Reads
   (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads) as num_reads,
   case when ((pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) > 0
      then decimal( pool_async_read_time / double((pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)), 5, 2 )
      else null
   end as avg_async_read_time,
   -- Writes
   (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes) as num_writes,
   case when ((pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)) > 0
      then decimal( pool_async_write_time / double((pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)), 5, 2 )
      else null
   end as avg_async_write_time
from
   mon_get_tablespace_diff
where
   ((pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads) + (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)) >= ts_delta
order by
   ((pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads) + (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)) desc
;
/*DB_EXTBM*/
select
   member,
   sum(ext_table_recvs_total) as ext_table_recvs_total,
   sum(ext_table_recv_wait_time) as ext_table_recv_wait_time,
   sum(ext_table_recv_volume) as ext_table_recv_volume,
   sum(ext_table_read_volume) as ext_table_read_volume,
   sum(ext_table_sends_total) as ext_table_sends_total,
   sum(ext_table_send_wait_time) as ext_table_send_wait_time,
   sum(ext_table_send_volume) as ext_table_send_volume,
   sum(ext_table_write_volume) as ext_table_write_volume,
   decimal((sum(ext_table_recv_volume) / float(min(ts_delta))), 10, 1) as recv_per_s,
   decimal((sum(ext_table_read_volume) / float(min(ts_delta))), 10, 1) as read_per_s,
   decimal((sum(ext_table_send_volume) / float(min(ts_delta))), 10, 1) as send_per_s,
   decimal((sum(ext_table_write_volume) / float(min(ts_delta))), 10, 1) as write_per_s
from
   mon_get_workload_diff
where
   ext_table_recvs_total + ext_table_sends_total >= ts_delta
group by
   member
order by
   member asc
;
/*LTC_WAITT*/
select
   member,
   cast(substr(latch_name,1,60) as varchar(60)) as latch_name,
   total_extended_latch_wait_time as tot_ext_latch_wait_time_ms,
   total_extended_latch_waits as tot_ext_latch_waits,
   decimal( double(total_extended_latch_wait_time) / total_extended_latch_waits, 10, 2 ) as time_per_latch_wait_ms
from
   mon_get_extended_latch_wait_diff
where
  total_extended_latch_waits >= ts_delta
order by
   total_extended_latch_wait_time desc
;
/*DB_DLCKS*/
select
   member,
   integer(sum(deadlocks)) as deadlocks,
   integer(sum(lock_timeouts)) as lock_timeouts,
   integer(sum(lock_escals)) as lock_escals

   ,
   integer(sum(lock_timeouts - lock_timeouts_global)) as lock_timeouts_local,
   integer(sum(lock_timeouts_global)) as lock_timeouts_global,
   integer(sum(lock_escals_maxlocks)) as lock_escals_maxlocks,
   integer(sum(lock_escals_locklist)) as lock_escals_locklist,
   integer(sum(lock_escals_global)) as lock_escals_global

from
   mon_get_workload_diff
group by
   member
order by
   member asc
;
/*TBL_ROWMC*/
select
  mgt.member,
  cast(substr(mgt.tabname,1,32) as varchar(32)) as tabname,
  smallint(mgt.data_partition_id) data_part_id,
  smallint(mgt.tbsp_id) tbsp_id,
  sum(mgt.rows_read) rows_read,
  sum(mgt.rows_inserted) rows_inserted,
  sum(mgt.rows_updated) rows_updated,
  sum(mgt.rows_deleted) rows_deleted,
  min(systab.append_mode) append,
  integer(sum(mgt.direct_read_reqs))dir_read_reqs,
  integer(sum(mgt.direct_write_reqs))dir_write_reqs,
  integer(sum(mgt.object_data_p_reads))obj_data_p_rds,
  case when sum(mgt.object_data_l_reads) > 0
    then decimal(float(sum(mgt.object_data_l_reads - mgt.object_data_p_reads)) / sum(mgt.object_data_l_reads) * 100, 5, 2)
    else null
  end as table_hr,
  sum(mgt.col_object_l_pages)col_object_l_pages,
  integer(sum(mgt.overflow_accesses))ovfl_accesses,
  integer(sum(mgt.overflow_creates))ovfl_creates,
  integer(sum(mgt.page_reorgs))page_reorgs,
  max(systab.pctpagessaved) pctpagessaved
from
  mon_get_table_diff mgt,
  systab
where
  mgt.tabname = systab.tabname and
  mgt.tabschema = systab.tabschema
group by
  mgt.member,mgt.tabname,mgt.tabschema,mgt.data_partition_id,mgt.tbsp_id    -- Roll up over tab_file_id as it's an unimportant difference
having
  sum(rows_read + rows_inserted + rows_updated + rows_deleted + page_reorgs) >= max(ts_delta)
order by
  mgt.tabname asc
;
/*IDX_OPS*/
select
  mgi.member,
  cast(substr(mgi.tabschema,1,16) as varchar(16)) as tabschema,
  cast(substr(mgi.tabname,1,32) as varchar(32)) as tabname,
  cast(substr(sysidx.indname,1,32) as varchar(32)) as indname,
  mgi.data_partition_id as data_part_id,
  mgi.iid,
  mgi.index_scans,
  mgi.index_only_scans,
    mgi.index_jump_scans,
  mgi.key_updates,
  mgi.pseudo_deletes,
  mgi.del_keys_cleaned
from
  mon_get_index_diff mgi, sysidx
where
  mgi.tabschema = sysidx.tabschema and
  mgi.tabname = sysidx.tabname and
  mgi.iid = sysidx.iid and
  (mgi.index_scans + mgi.index_only_scans + mgi.key_updates + mgi.pseudo_deletes
    + mgi.index_jump_scans
  ) >= ts_delta
order by
  (mgi.index_scans + mgi.index_only_scans + mgi.key_updates + mgi.pseudo_deletes
    + mgi.index_jump_scans
  ) desc
;
/*IDX_SPLITS*/
select
  mgi.member,
  cast(substr(mgi.tabschema,1,16) as varchar(16)) as tabschema,
  cast(substr(mgi.tabname,1,32) as varchar(32)) as tabname,
  cast(substr(sysidx.indname,1,32) as varchar(32)) as indname,
  mgi.data_partition_id as data_part_id,
  mgi.iid,
  decimal((mgi.root_node_splits + mgi.int_node_splits + mgi.boundary_leaf_node_splits + mgi.nonboundary_leaf_node_splits) / float(ts_delta), 10, 1) as splits_per_s,
  mgi.root_node_splits,
  mgi.int_node_splits,
  mgi.boundary_leaf_node_splits,
  mgi.nonboundary_leaf_node_splits
from
  mon_get_index_diff mgi, sysidx
where
  mgi.tabschema = sysidx.tabschema and
  mgi.tabname = sysidx.tabname and
  mgi.iid = sysidx.iid and
  (mgi.root_node_splits + mgi.int_node_splits + mgi.boundary_leaf_node_splits + mgi.nonboundary_leaf_node_splits) > 0
order by
  (mgi.root_node_splits + mgi.int_node_splits + mgi.boundary_leaf_node_splits + mgi.nonboundary_leaf_node_splits) desc
;
/*IDX_PAGEUSE*/
select
  mgi.member,
  cast(substr(mgi.tabschema,1,16) as varchar(16)) as tabschema,
  cast(substr(mgi.tabname,1,32) as varchar(32)) as tabname,
  cast(substr(sysidx.indname,1,32) as varchar(32)) as indname,
  mgi.data_partition_id as data_part_id,
  mgi.iid,
  mgi.page_allocations,
  mgi.pseudo_empty_pages,
  mgi.empty_pages_reused,
  mgi.empty_pages_deleted,
  mgi.pages_merged
from
  mon_get_index_diff mgi, sysidx
where
  mgi.tabschema = sysidx.tabschema and
  mgi.tabname = sysidx.tabname and
  mgi.iid = sysidx.iid and
  (mgi.page_allocations + mgi.pseudo_empty_pages + mgi.empty_pages_reused + mgi.empty_pages_deleted + mgi.pages_merged) > 0
order by
  (mgi.page_allocations + mgi.pseudo_empty_pages + mgi.empty_pages_reused + mgi.empty_pages_deleted + mgi.pages_merged) desc
;
/*IDX_READS*/
select
  mgi.member,
  cast(substr(mgi.tabschema,1,16) as varchar(16)) as tabschema,
  cast(substr(mgi.tabname,1,32) as varchar(32)) as tabname,
  cast(substr(sysidx.indname,1,32) as varchar(32)) as indname,
  mgi.data_partition_id as data_part_id,
  mgi.iid,
  mgi.object_index_l_reads as l_reads,
  mgi.object_index_p_reads as p_reads,
  mgi.object_index_gbp_l_reads as gbp_l_reads,
  mgi.object_index_gbp_p_reads as gbp_p_reads,
  mgi.object_index_gbp_invalid_pages as gbp_invalid,
  mgi.object_index_lbp_pages_found as lbp_found,
  mgi.object_index_gbp_indep_pages_found_in_lbp as indep_lbp_found
from
  mon_get_index_diff mgi, sysidx
where
  mgi.tabschema = sysidx.tabschema and
  mgi.tabname = sysidx.tabname and
  mgi.iid = sysidx.iid and
  (mgi.object_index_l_reads + mgi.object_index_p_reads) >= ts_delta
order by
  (mgi.object_index_l_reads + mgi.object_index_p_reads) desc
;
/*TBL_DATSH*/
select
  member,
  cast(substr(tabname,1,40) as varchar(40)) as tabname,
  data_partition_id as data_part_id,
  data_sharing_state_change_time,
  data_sharing_state,
  data_sharing_remote_lockwait_count,
  data_sharing_remote_lockwait_time
from
  mon_get_table_diff
where
  rows_read + rows_inserted + rows_updated + rows_deleted + page_reorgs >= ts_delta
  and data_sharing_state_change_time is not null
order by
  tabname asc
;
/*DB_SIZE*/
select
  member,
  (sum( (tbsp_used_pages) * (tbsp_page_size) ) / 1024 / 1024) as db_mb_used
from
   mon_get_tablespace_end
group by
  member
order by
  member asc
;
/*TSP_SIZE*/
select
  member,
  cast(substr(tbsp_name,1,32) as varchar(32)) as tbsp_name,
  tbsp_id,
  tbsp_page_size,
  tbsp_used_pages,
  decimal( (double(tbsp_used_pages) * tbsp_page_size) / 1024 / 1024, 10, 2 ) as tbsp_mb_used,
  decimal( (double(tbsp_page_top) * tbsp_page_size) / 1024 / 1024, 10, 2) as tbsp_mb_hwm,
  tbsp_extent_size,
  case fs_caching when 2 then 'Default off' when 1 then 'Explicit off' else 'Explicit on' end fs_caching,
  tbsp_prefetch_size
from
  mon_get_tablespace_end
order by
  member asc, tbsp_used_pages desc
;
/*TSP_USAGE*/
select
  member,
  cast(substr(tbsp_name,1,30) as varchar(30)) as tbsp_name,
  tbsp_used_pages,
  decimal( (double(tbsp_used_pages) * tbsp_page_size) / 1024 / 1024, 10, 2 ) as tbsp_mb_used
from
  mon_get_tablespace_diff
where
  tbsp_used_pages > 0
order by
  member asc, tbsp_used_pages desc
;
/*BPL_STATS*/
select
   member,
   cast(substr(tbsp_name,1,20) as varchar(20)) as tbsp_name,
   -- Data pages
   pool_data_l_reads + pool_temp_data_l_reads as data_l_reads,
   pool_data_p_reads + pool_temp_data_p_reads as data_p_reads,
   pool_data_p_reads + pool_temp_data_p_reads - pool_async_data_reads as sync_data_p_reads,
   case when (pool_data_l_reads + pool_temp_data_l_reads) > 1000
      then decimal(float(pool_data_l_reads + pool_temp_data_l_reads - (pool_data_p_reads + pool_temp_data_p_reads - pool_async_data_reads))/(pool_data_l_reads + pool_temp_data_l_reads)*100,5,2)
      else null
   end as data_hr,
   case when (pool_data_l_reads + pool_temp_data_l_reads) > 1000
      then
         decimal((pool_data_lbp_pages_found - pool_async_data_lbp_pages_found) / float(pool_data_l_reads + pool_temp_data_l_reads) * 100, 5, 2)
      else null
   end as data_lbp_hr,
   -- Columnar pages
   pool_col_l_reads + pool_temp_col_l_reads as col_l_reads,
   pool_col_p_reads + pool_temp_col_p_reads as col_p_reads,
   pool_col_p_reads + pool_temp_col_p_reads - pool_async_col_reads as sync_col_p_reads,
   case when (pool_col_l_reads + pool_temp_col_l_reads) > 1000
      then decimal(float(pool_col_l_reads + pool_temp_col_l_reads - (pool_col_p_reads + pool_temp_col_p_reads - pool_async_col_reads))/(pool_col_l_reads + pool_temp_col_l_reads)*100,5,2)
      else null
   end as col_hr,
   -- Index pages
   pool_index_l_reads + pool_temp_index_l_reads as index_l_reads,
   pool_index_p_reads + pool_temp_index_p_reads as index_p_reads,
   pool_index_p_reads + pool_temp_index_p_reads - pool_async_index_reads as sync_index_p_reads,
   case when (pool_index_l_reads + pool_temp_index_l_reads) > 1000
      then decimal(float(pool_index_l_reads + pool_temp_index_l_reads - (pool_index_p_reads + pool_temp_index_p_reads - pool_async_index_reads))/(pool_index_l_reads + pool_temp_index_l_reads)*100,5,2)
      else null
   end as index_hr,
   case when (pool_index_l_reads + pool_temp_index_l_reads) > 1000
      then
         decimal((pool_index_lbp_pages_found - pool_async_index_lbp_pages_found ) / float(pool_index_l_reads + pool_temp_index_l_reads) * 100, 5, 2)
      else null
   end as index_lbp_hr
from
   mon_get_tablespace_diff
where
   (pool_data_l_reads + pool_temp_data_l_reads + pool_index_l_reads + pool_temp_index_l_reads + pool_xda_l_reads + pool_temp_xda_l_reads + pool_col_l_reads + pool_temp_col_l_reads) >= ts_delta
order by
   pool_read_time desc
;
/*TSP_PRFST*/
select
  member,
  cast(substr(tbsp_name,1,30) as varchar(30)) as tbsp_name,
  pool_async_data_reads,
  pool_data_p_reads,
  case when pool_data_p_reads > 0 then decimal(pool_async_data_reads / float(pool_data_p_reads) * 100, 5, 2) else null end as pct_data_pftch,
  pool_async_index_reads,
  pool_index_p_reads,
  case when pool_index_p_reads > 0 then decimal(pool_async_index_reads / float(pool_index_p_reads) * 100, 5, 2) else null end as pct_index_pftch,
  pool_async_col_reads,
  pool_col_p_reads,
  case when pool_col_p_reads > 0 then decimal(pool_async_col_reads / float(pool_col_p_reads) * 100, 5, 2) else null end as pct_col_pftch,
  prefetch_wait_time,
  prefetch_waits,
  unread_prefetch_pages
from
  mon_get_tablespace_diff
where
  (pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) >= ts_delta
order by
  member asc, (pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) desc
;

/*TSP_IOST*/
select
  member,
  cast(substr(tbsp_name,1,30) as varchar(30)) as tbsp_name,
  vectored_ios,
  pages_from_vectored_ios,
  case when vectored_ios > 0 then decimal(pages_from_vectored_ios / float(vectored_ios), 5, 2) else null end as avg_vio_sz,
  block_ios,
  pages_from_block_ios,
  case when block_ios > 0 then decimal(pages_from_block_ios / float(block_ios), 5, 2) else null end as avg_bio_sz
from
  mon_get_tablespace_diff
where
  (vectored_ios + block_ios) >= ts_delta
order by
  member asc, (vectored_ios + block_ios) desc
;
/*TSP_BPMAP*/
select tbsp_name,datatype,bpname from TSP_BPMAP_OUT;
/*BPL_SIZES*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   pagesize,
   bp_cur_buffsz as num_pages,
   decimal(double(pagesize) * bp_cur_buffsz / 1024 / 1024, 10, 2) as size_mb,
   automatic
from
  mon_get_bufferpool_diff
order by
   member
;
/*BPL_HITRA*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   case when (pool_data_l_reads) > 0
      then
         decimal( ( double(pool_data_lbp_pages_found - pool_async_data_lbp_pages_found ) / (pool_data_l_reads + pool_temp_data_l_reads) ) * 100, 5, 2 )
      else 0
   end as row_data_lbp_hitratio,
   case when (pool_col_l_reads) > 0
      then
         decimal( ( double(pool_col_lbp_pages_found - pool_async_col_lbp_pages_found ) / (pool_col_l_reads + pool_temp_col_l_reads) ) * 100, 5, 2 )
      else 0
   end as col_data_lbp_hitratio,
   case when (pool_index_l_reads) > 0
      then
         decimal( ( double(pool_index_lbp_pages_found - pool_async_index_lbp_pages_found ) / (pool_index_l_reads + pool_temp_index_l_reads) ) * 100, 5, 2 )
      else 0
   end as index_lbp_hitratio
from
   mon_get_bufferpool_diff
where
   (pool_data_l_reads + pool_temp_data_l_reads + pool_index_l_reads + pool_temp_index_l_reads + pool_xda_l_reads + pool_temp_xda_l_reads + pool_col_l_reads + pool_temp_col_l_reads) > 0
order by
   (pool_data_l_reads + pool_temp_data_l_reads + pool_index_l_reads + pool_temp_index_l_reads + pool_xda_l_reads + pool_temp_xda_l_reads + pool_col_l_reads + pool_temp_col_l_reads) desc
;
/*BPL_READS*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   pool_data_l_reads,
   pool_data_p_reads,
   pool_index_l_reads,
   pool_index_p_reads,
   pool_col_l_reads,
   pool_col_p_reads,
   pool_read_time,
   case when ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads)) > 0
      then decimal( pool_read_time / double((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads)), 5, 2 )
      else null
   end as avg_read_time
from
   mon_get_bufferpool_diff
where
   (pool_data_l_reads + pool_temp_data_l_reads + pool_index_l_reads + pool_temp_index_l_reads + pool_xda_l_reads + pool_temp_xda_l_reads + pool_col_l_reads + pool_temp_col_l_reads) > 0
order by
   (pool_data_l_reads + pool_temp_data_l_reads + pool_index_l_reads + pool_temp_index_l_reads + pool_xda_l_reads + pool_temp_xda_l_reads + pool_col_l_reads + pool_temp_col_l_reads) desc
;
/*BPL_RDSYNC*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   (pool_data_p_reads - pool_async_data_reads) as pool_sync_data_reads,
   (pool_index_p_reads - pool_async_index_reads) as pool_sync_index_reads,
   (pool_col_p_reads - pool_async_col_reads) as pool_sync_col_reads,
   (pool_read_time - pool_async_read_time) as pool_sync_read_time,
   case when (((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads))) > 0
      then decimal( (pool_read_time - pool_async_read_time) / double(((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads))), 5, 2 )
      else null
   end as avg_sync_read_time
from
   mon_get_bufferpool_diff
where
   ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) > 0
order by
   ((pool_data_p_reads + pool_temp_data_p_reads + pool_index_p_reads + pool_temp_index_p_reads + pool_xda_p_reads + pool_temp_xda_p_reads + pool_col_p_reads + pool_temp_col_p_reads) - (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) desc
;
/*BPL_RDASYNC*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   pool_async_data_reads,
   pool_async_index_reads,
   pool_async_col_reads,
   pool_async_read_time,
   case when ((pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)) > 0
      then decimal( pool_async_read_time / double((pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads)), 5, 2 )
      else null
   end as avg_async_read_time
from
   mon_get_bufferpool_diff
where
   (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads) > 0
order by
   (pool_async_data_reads + pool_async_index_reads + pool_async_xda_reads + pool_async_col_reads) desc
;
/*BPL_WRITE*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   pool_data_writes,
   pool_index_writes,
   pool_xda_writes,
   pool_col_writes,
   pool_write_time,
   case when ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes)) > 0
      then decimal( pool_write_time / double((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes)), 5, 2 )
      else null
   end as avg_write_time
from
   mon_get_bufferpool_diff
where
   (pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) > 0
order by
   (pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) desc
;
/*BPL_WRSYNC*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   (pool_data_writes - pool_async_data_writes) as pool_sync_data_writes,
   (pool_index_writes - pool_async_index_writes) as pool_sync_index_writes,
   (pool_xda_writes - pool_async_xda_writes) as pool_sync_xda_writes,
   (pool_col_writes - pool_async_col_writes) as pool_sync_col_writes,
   (pool_write_time - pool_async_write_time) as pool_sync_write_time,
   case when (((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes))) > 0
      then decimal( (pool_write_time - pool_async_write_time) / double(((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes))), 5, 2 )
      else null
   end as avg_write_time
from
   mon_get_bufferpool_diff
where
   ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)) > 0
order by
   ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes) - (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)) desc
;
/*BPL_WRASYNC*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   pool_async_data_writes,
   pool_async_index_writes,
   pool_async_xda_writes,
   pool_async_col_writes,
   pool_async_write_time,
   case when ((pool_data_writes + pool_index_writes + pool_xda_writes + pool_col_writes)) > 0
      then decimal( pool_async_write_time / double((pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes)), 5, 2 )
      else null
   end as avg_write_time
from
   mon_get_bufferpool_diff
where
   (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes) > 0
order by
   (pool_async_data_writes + pool_async_index_writes + pool_async_xda_writes + pool_async_col_writes) desc
;
/*CON_WAITT*/
select
   member,
   cast(substr(application_name,1,20) as varchar(20)) as app_name,
   cast(substr(client_applname,1,20) as varchar(20)) as client_name,
   integer(application_handle) as handle,
   decimal((client_idle_wait_time / double(total_rqst_time)), 10, 2) as idle_rq_ratio,
   decimal((total_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_rqst_wt,
   decimal((lock_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_lock,
   decimal((log_disk_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_lg_dsk,
   decimal((log_buffer_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_lg_buf,
   decimal((lock_wait_time_global / double(total_rqst_time)) * 100, 5, 2) as pct_glb_lock,
   decimal((total_extended_latch_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_ltch,
   decimal((reclaim_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_rclm,
   decimal((cf_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_cf,
   decimal((prefetch_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_pftch,
   decimal((diaglog_write_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_diag,
   -- fcm_recv_wait_time = fcm_message_recv_wait_time + fcm_tq_recv_wait_time (similarly for send)
   -- decimal(((fcm_message_recv_wait_time + fcm_message_send_wait_time) / float(total_rqst_time)) * 100, 5, 2) as pct_fcm_msg,
   -- decimal(((fcm_tq_recv_wait_time + fcm_tq_send_wait_time) / float(total_rqst_time)) * 100, 5, 2) as pct_fcm_tq,
   decimal((audit_file_write_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_aud_w,
   decimal((audit_subsystem_wait_time / double(total_rqst_time)) * 100, 5, 2) as pct_aud_ss,
   decimal((evmon_wait_time / float(total_rqst_time)) * 100, 5, 2) as pct_evmon,
   decimal((comm_exit_wait_time / float(total_rqst_time)) * 100, 5, 2) as pct_commexit,
   decimal((lob_prefetch_wait_time / float(total_rqst_time)) * 100, 5, 2) as pct_lob_pftch,
   decimal(((ext_table_recv_wait_time + ext_table_send_wait_time) / float(total_rqst_time)) * 100, 5, 2) as pct_extbl,
   decimal((fed_wait_time / float(total_rqst_time)) * 100, 5, 2) as pct_fed,
   decimal((pool_read_time / double(total_rqst_time)) * 100, 5, 2) as pct_pool_r,
   decimal((direct_read_time / float(total_rqst_time)) * 100, 5, 2) as pct_dir_r,
   decimal((direct_write_time / float(total_rqst_time)) * 100, 5, 2) as pct_dir_w,
   decimal(((fcm_recv_wait_time + fcm_send_wait_time) / float(total_rqst_time)) * 100, 5, 2) as pct_fcm,
   decimal(((ida_send_wait_time + ida_recv_wait_time) / double(total_rqst_time)) * 100, 5, 2) as pct_ida
from
   mon_get_connection_diff
where
   total_rqst_time <> 0
order by
   member asc, total_wait_time / double(total_rqst_time) desc
;
/*CON_STATS*/
select
   member,
   cast(substr(application_name,1,20) as varchar(20)) as app_name,
   cast(substr(client_applname,1,20) as varchar(20)) as client_name,
   integer(application_handle) as handle,
   total_app_commits,
   total_app_rollbacks,
   deadlocks,
   rows_modified,
   rows_read,
   rows_returned,
   total_sorts
,connection_reusability_status reuse_status,
cast(substr(reusability_status_reason,1,32) as varchar(32)) reusability_status_reason
from
   mon_get_connection_diff
where
   total_rqst_time <> 0
order by
   member asc, total_wait_time / double(total_rqst_time) desc
;
/*CON_PAGRW*/
select
   member,
   cast(substr(application_name,1,20) as varchar(20)) as app_name,
   cast(substr(client_applname,1,20) as varchar(20)) as client_name,
   integer(application_handle) as handle,
   total_app_commits,
   total_app_rollbacks,
   pool_data_l_reads,
   pool_data_p_reads,
   pool_index_l_reads,
   pool_index_p_reads
, pool_col_l_reads, pool_col_p_reads from
   mon_get_connection_diff
where
   total_rqst_time <> 0
order by
   member asc, total_wait_time / double(total_rqst_time) desc
;
/*WLB_SLIST*/
select
  member,
  cached_timestamp,
  cast(substr(hostname,1,20) as varchar(20)) as hostname,
  port_number,
  ssl_port_number,
  priority
from
  mon_get_serverlist_end
order by
  member asc, priority desc
;
/*CFG_REGVA*/
select
   member,
   cast(substr(reg_var_name,1,50) as varchar(50)) as reg_var_name,
   cast(substr(reg_var_value,1,50) as varchar(50)) as reg_var_value,
   level
from
   env_get_reg_variables_end
order by
  reg_var_name,member
;
/*CFG_DB*/
select
   member,
   cast(substr(name,1,24) as varchar(24)) as name,
   case when value_flags = 'NONE' then '' else value_flags end flags,
   cast(substr(value,1,64) as varchar(64)) as current_value
from
   db_get_cfg_end
order by
  name asc, member asc
;
/*CFG_DBM*/
select
   cast(substr(name,1,24) as varchar(24)) as name,
   case when value_flags = 'NONE' then '' else value_flags end flags,
   cast(substr(value,1,64) as varchar(64)) as current_value
from
   dbmcfg_end
order by
   name asc
;
/*INS_INFO*/
select
   cast(substr(inst_name,1,20) as varchar(20)) as inst_name,
   num_dbpartitions,
   num_members,
   cast(substr(service_level,1,20) as varchar(20)) as service_level,
   cast(substr(bld_level,1,40) as varchar(40)) as bld_level,
   cast(substr(ptf,1,40) as varchar(40)) as ptf
from
   env_inst_info_end
;
/*DB_MEMST*/
select
   member,
   cast(substr(memory_set_type,1,20) as varchar(20)) as set_type,
   cast(substr(db_name,1,20) as varchar(20)) as dbname,
   decimal( double(memory_set_used) / 1024, 10, 2 ) as memory_set_used_mb,
   decimal( double(memory_set_used_hwm) / 1024, 10, 2 ) as memory_set_used_hwm_mb
from
   mon_get_memory_set_end
order by
   member asc, memory_set_used desc
;
/*DB_MEMPL*/
select
   member,
   cast(substr(memory_pool_type,1,20) as varchar(20)) as memory_pool_type,
   cast(substr(db_name,1,20) as varchar(20)) as db_name,
   decimal( double(memory_pool_used) / 1024, 10, 2 ) as memory_pool_used_mb,
   decimal( double(memory_pool_used_hwm) / 1024, 10, 2 ) as memory_pool_used_hwm_mb
from
   mon_get_memory_pool_end
order by
   member asc, memory_pool_used desc
;
/*DB_SEQIN*/
select
   cast(substr(seqschema,1,40) as varchar(40)) as seqschema,
   cast(substr(seqname,1,40) as varchar(40)) as seqname,
   seqtype,
   cache,
   ord
from
  db2sequences
;
/*CF_GBPIO*/
select
   member,
   cast(substr(tbsp_name,1,20) as varchar(20)) as tbsp_name,
   -- Data pages
   case when pool_data_gbp_l_reads > 0
      then decimal( (double(pool_data_gbp_l_reads - pool_data_gbp_p_reads) / pool_data_gbp_l_reads) * 100, 5, 2 )
      else null
   end as data_gbp_hitratio,
   case when (pool_data_l_reads + pool_temp_data_l_reads) > 0
      then decimal( (double(pool_data_lbp_pages_found - pool_async_data_lbp_pages_found) / (pool_data_l_reads + pool_temp_data_l_reads)) * 100, 5, 2 )
      else null
   end as data_lbp_hitratio,
   case when pool_data_gbp_l_reads > 0
      then decimal( (double(pool_data_gbp_invalid_pages - pool_async_data_gbp_invalid_pages) / pool_data_gbp_l_reads) * 100, 5, 2 )
      else null
   end as pct_data_invalid,
   -- Index pages
   case when pool_index_gbp_l_reads > 0
      then decimal( (double(pool_index_gbp_l_reads - pool_index_gbp_p_reads) / pool_index_gbp_l_reads) * 100, 5, 2 )
      else null
   end as index_gbp_hitratio,
   case when (pool_index_l_reads + pool_temp_index_l_reads) > 0
      then decimal( (double(pool_index_lbp_pages_found - pool_async_index_lbp_pages_found) / (pool_index_l_reads + pool_temp_index_l_reads)) * 100, 5, 2 )
      else null
   end as index_lbp_hitratio,
   case when pool_index_gbp_l_reads > 0
      then decimal( (double(pool_index_gbp_invalid_pages - pool_async_index_gbp_invalid_pages) / pool_index_gbp_l_reads) * 100, 5, 2 )
      else null
   end as pct_index_invalid
from
   mon_get_tablespace_diff
where
   pool_data_gbp_l_reads + pool_index_gbp_l_reads >= ts_delta
order by
   pool_data_gbp_l_reads + pool_data_gbp_p_reads + pool_index_gbp_l_reads + pool_index_gbp_p_reads desc
;
/*CF_GBPHR*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   -- Data pages
   pool_data_gbp_l_reads,
   pool_data_gbp_p_reads,
   case when pool_data_gbp_l_reads > 0
      then decimal( ( double(pool_data_gbp_l_reads - pool_data_gbp_p_reads) / pool_data_gbp_l_reads ) * 100 , 5, 2 )
      else null
   end as data_gbp_hitratio,
   -- Index pages
   pool_index_gbp_l_reads,
   pool_index_gbp_p_reads,
   case when pool_index_gbp_l_reads > 0
      then decimal( ( double(pool_index_gbp_l_reads - pool_index_gbp_p_reads) / pool_index_gbp_l_reads ) * 100, 5, 2 )
      else null
   end as index_gbp_hitratio
from
   mon_get_bufferpool_diff
where
   (pool_data_gbp_l_reads ) > 0 or (pool_index_gbp_l_reads ) > 0
order by
   pool_read_time desc
;
/*CF_GBPIV*/
select
   member,
   cast(substr(bp_name,1,20) as varchar(20)) as bp_name,
   -- Data pages
   case when pool_data_gbp_l_reads > 0
      then decimal( ( double(pool_data_gbp_invalid_pages - pool_async_data_gbp_invalid_pages) / pool_data_gbp_l_reads ) * 100, 5, 2 )
      else null
   end as pct_data_gbp_invalid,
   -- Index pages
   case when pool_index_gbp_l_reads > 0
      then decimal( ( double(pool_index_gbp_invalid_pages - pool_async_index_gbp_invalid_pages) / pool_index_gbp_l_reads ) * 100, 5, 2 )
      else null
   end as pct_index_gbp_invalid
from
   mon_get_bufferpool_diff
where
   (pool_data_gbp_l_reads) > 0 or (pool_index_gbp_l_reads) > 0
order by
   pool_read_time desc
;
/*CF_GBPDP*/
select
  member,
  cast(substr(tbsp_name,1,30) as varchar(30)) as tbsp_name,
  pool_async_data_gbp_l_reads,
  pool_async_data_gbp_p_reads,
  pool_async_data_lbp_pages_found,
  pool_async_data_gbp_invalid_pages,
  pool_async_data_gbp_indep_pages_found_in_lbp
from
  mon_get_tablespace_diff
where
  pool_async_data_gbp_l_reads >= ts_delta
order by
  member asc, pool_async_data_gbp_l_reads desc
;
/*CF_GBPIP*/
select
  member,
  cast(substr(tbsp_name,1,30) as varchar(30)) as tbsp_name,
  pool_async_index_gbp_l_reads,
  pool_async_index_gbp_p_reads,
  pool_async_index_lbp_pages_found,
  pool_async_index_gbp_invalid_pages,
  pool_async_index_gbp_indep_pages_found_in_lbp
from
  mon_get_tablespace_diff
where
  pool_async_index_gbp_l_reads >= ts_delta
order by
  member asc, pool_async_index_gbp_l_reads desc
;
/*CF_GBPFL*/
select
   member,
   num_gbp_full
from
   mon_get_group_bufferpool_diff
order by
   member asc
;
/*PAG_RCM*/
select
   member,
   cast(substr(tabschema,1,20) as varchar(20)) as tabschema,
   cast(substr(tabname,1,40) as varchar(40)) as tabname,
   cast(substr(objtype,1,10) as varchar(10)) as objtype,
   data_partition_id as data_part_id,
   iid,
   (page_reclaims_x + page_reclaims_s) as page_reclaims,
   reclaim_wait_time
from
   mon_get_page_access_info_diff
where
  reclaim_wait_time > 0
order by
  reclaim_wait_time desc
;
/*PAG_RCSMP*/
select
   member,
   cast(substr(tabschema,1,20) as varchar(20)) as tabschema,
   cast(substr(tabname,1,40) as varchar(40)) as tabname,
   cast(substr(objtype,1,10) as varchar(10)) as objtype,
   data_partition_id as data_part_id,
   iid,
   (spacemappage_page_reclaims_x + spacemappage_page_reclaims_s) as smp_page_reclaims,
   spacemappage_reclaim_wait_time as smp_page_reclaim_wait_time
from
   mon_get_page_access_info_diff
where
  spacemappage_reclaim_wait_time > 0
order by
   spacemappage_reclaim_wait_time desc
;
/*CF_RTTIM*/
select
   member,
   id,
   cast(substr(cf_cmd_name,1,30) as varchar(30)) as cf_cmd_name,
   total_cf_requests,
   decimal( double(total_cf_wait_time_micro) / total_cf_requests, 15, 2 ) as avg_cf_request_time_micro
from
   mon_get_cf_wait_time_diff
where
   total_cf_requests > 0
order by
   id asc, total_cf_requests desc
;

/*DB_CLACT*/
select
   t.member,
   t.count as total_clients,
   t.client_idle_wait_time total_ciwt,
   t.total_rqst_time total_rqst,
   t.idle_rqst_ratio tot_ciwt_rq_ratio,
   a.count as active_clients,
   a.rqst_per_s active_rq_per_s,
   a.client_idle_wait_time active_ciwt,
   a.total_rqst_time active_rqst,
   a.idle_rqst_ratio active_ciwt_rq_ratio
from
   (  select
             member,
             count(*) count,
             sum(client_idle_wait_time) client_idle_wait_time,
             sum(total_rqst_time) total_rqst_time,
             case when sum(total_rqst_time) > 0 then decimal(float(sum(client_idle_wait_time)) / sum(total_rqst_time), 10, 2) else null end as idle_rqst_ratio
          from
             mon_get_connection_diff
          group by
             member
       ) t,
   (  select
             member,
             count(*) count,
             decimal(float(sum(rqsts_completed_total)) / min(ts_delta), 10, 2) rqst_per_s,
             sum(client_idle_wait_time) client_idle_wait_time,
             sum(total_rqst_time) total_rqst_time,
             case when sum(total_rqst_time) > 0 then decimal(float(sum(client_idle_wait_time)) / sum(total_rqst_time), 10, 2) else null end as idle_rqst_ratio
          from
             mon_get_connection_diff
          where
             (rqsts_completed_total >= ts_delta or                                       -- At least one request per second, or ...
             (total_rqst_time > 0 and client_idle_wait_time / total_rqst_time < 2))      -- long requests, at least half as long as the client idle wait time
             and total_rqst_time > 0                                                     -- must have done some work
             and client_idle_wait_time <= total_rqst_time * 10                           -- idle time must not dwarf request time (filters admin/system connections at workload stop)
          group by
             member
       ) a
where
   a.member = t.member
;
/*CF_CMDCT*/ -- Modified from original SQL to accomodate rollup

select
   member,
   id,
   total_cf_requests
from
   (SELECT member, id, SUM(total_cf_requests) as total_cf_requests
FROM mon_get_cf_wait_time_diff
GROUP BY member, id
UNION ALL
SELECT member, NULL, SUM(total_cf_requests) as total_cf_requests
FROM mon_get_cf_wait_time_diff
GROUP BY member
UNION ALL
SELECT NULL, NULL, SUM(total_cf_requests) as total_cf_requests
FROM mon_get_cf_wait_time_diff) order by member asc, id asc, total_cf_requests desc;
;



/*CF_CMDTM*/
select
   d.id,
   cast(substr(cf_cmd_name,1,50) as varchar(50)) as cf_cmd_name,
   total_cf_requests,
   decimal( (total_cf_requests / double(total_total_cf_requests)) * 100, 5, 2 ) as pct_total_cf_cmd,
   decimal( double(total_cf_cmd_time_micro) / total_cf_requests, 15, 2 ) as avg_cf_request_time_micro
from
   mon_get_cf_cmd_diff as d,
   ( select id, sum(total_cf_requests) as total_total_cf_requests
     from mon_get_cf_cmd_diff
     group by id ) as c
where
   d.total_cf_requests > 0 and d.id = c.id
order by
   d.id asc, total_cf_requests desc
;
/*CF_CMDTO*/
select
   id,
   sum(total_cf_requests) as total_cf_requests
from
   mon_get_cf_cmd_diff
group by
   id
order by
   id asc
;
/*CF_SYSRE*/
select
   id,
   cast(substr(name,1,30) as varchar(30)) as name,
   cast(substr(value,1,50) as varchar(50)) as value,
   cast(substr(unit,1,10) as varchar(10)) as unit
from
   env_cf_sys_resources_end
order by
   id asc, name asc
;
/*CF_SIZE*/
select
   id,
   cast(substr(host_name,1,40) as varchar(40)) as hostname,
   decimal(((double(current_cf_mem_size) * 4) / 1024), 10, 2) as curr_mem_mb,
   decimal(((double(configured_cf_mem_size) * 4) / 1024), 10, 2) as conf_mem_mb,
   decimal(((double(current_cf_gbp_size) * 4) / 1024), 10, 2) as curr_gbp_mb,
   decimal(((double(configured_cf_gbp_size) * 4) / 1024), 10, 2) as conf_gbp_mb,
   decimal(((double(target_cf_gbp_size) * 4) / 1024), 10, 2) as trgt_gbp_mb,
   decimal(((double(current_cf_lock_size) * 4) / 1024), 10, 2) as curr_lock_mb,
   decimal(((double(configured_cf_lock_size) * 4) / 1024), 10, 2) as conf_lock_mb,
   decimal(((double(target_cf_lock_size) * 4) / 1024), 10, 2) as trgt_lock_mb,
   decimal(((double(current_cf_sca_size) * 4) / 1024), 10, 2) as curr_sca_mb,
   decimal(((double(configured_cf_sca_size) * 4) / 1024), 10, 2) as conf_sca_mb,
   decimal(((double(target_cf_sca_size) * 4) / 1024), 10, 2) as trgt_sca_mb
from
   mon_get_cf_end
order by
   id asc
;
/*BLU_PAGDI*/
select
   member,
   sum(total_peds) total_peds,
   sum(post_threshold_peds) post_threshold_peds,
   sum(disabled_peds) disabled_peds,
   sum(total_peas) total_peas,
   sum(post_threshold_peas) post_threshold_peas
from
   mon_get_workload_diff
where
   total_peds >= ts_delta
group by
   member
order by
   member
;
