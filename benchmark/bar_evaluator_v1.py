"""Obligation evidence for the bar diagnostic track, aligned with paper 0ffd470f.

These q records are inputs to future certified panels, not formal D1--D6 scores.
Unreached obligations remain zero; a late episode failure does not erase a
previously accepted P2 handoff.
"""
def evaluate_bar(result, episode):
    events = result['events']; done = result['completed_ticks']; records = []
    for obj in episode.objects:
        prefix = obj['object_id']
        ids = [prefix + suffix for suffix in ('_acquire', '_prepare', '_release', '_accept')]
        accept = next((e for e in events if e['kind']=='handoff_accept' and e['object_id']==prefix), None)
        reached = all(t in done for t in ids)
        correct = reached and accept is not None and all(done[a]<=done[b] for a,b in zip(ids,ids[1:]))
        records.append({'obligation_id':prefix+'_handoff', 'primitive':'P2', 'q':int(correct),
                        'status':'satisfied' if correct else 'violated' if accept else 'unreached',
                        'endpoint_tick':done.get(ids[-1])})
    for group in sorted({t.cooperative_group for t in episode.tasks if t.cooperative_group}):
        completion=next((e for e in events if e['kind']=='joint_action_completed' and e['group']==group),None)
        records.append({'obligation_id':group, 'primitive':'P3-J',
            'q':int(bool(completion and completion['support_valid'])),
            'status':'satisfied' if completion and completion['support_valid'] else 'unreached'})
    if any(t.task_id=='service_cover' for t in episode.tasks):
        trace=result['service_trace']
        valid=bool(trace and 'service_end' in done and all(t['covered'] or t['tolerated_registered_gap'] for t in trace)
                   and result['service_coverage']>=0.5 and result['max_service_gap_s']<=90)
        records.append({'obligation_id':'continuous_service','primitive':'P3-S','q':int(valid),
                        'status':'satisfied' if valid else 'violated' if trace else 'unreached'})
    for resource in episode.resources:
        requested=any(e.get('resource_id')==resource.resource_id for e in events if e['kind'] in
            {'resource_request','handoff_window_request','stair_entry_acquire'})
        # Custom paired leases have fixed resource identities.
        if resource.resource_id=='handoff_window': requested=any(e['kind']=='handoff_window_request' for e in events)
        if resource.resource_id=='stair_mutex': requested=any(e['kind']=='stair_entry_acquire' for e in events)
        released=not result['resource_state']['active'][resource.resource_id] and not result['resource_state']['queues'][resource.resource_id]
        records.append({'obligation_id':resource.resource_id, 'primitive':'P4',
            'q':int(requested and released and result['success']),
            'status':'satisfied' if requested and released and result['success'] else 'unreached'})
    return {'schema':'heterocoord.bar.obligations.v1','paper_commit':'0ffd470f','records':records,
        'dimensions':{'D1':'pending_first_full_committed_plan_and_certified_infeasible_support',
                      'D2':'diagnostic_C0_C3_rows_available_capacity_calibration_pending',
                      'D3':'logical_obligation_records_available_certified_support_pending',
                      'D4':'affected_work_and_persistent_loss_trace_available_paired_panel_pending',
                      'D5':'NA_no_certified_adaptation_unit',
                      'D6':'bits_and_time_available_independent_reference_scales_pending'},
        'paper_score_eligible':False}
