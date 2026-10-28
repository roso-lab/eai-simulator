"""FMC native schedule to two existing UR5 executors in one Isaac scene.

This bounded fixture has two independent manipulation tasks. It does not add
fake competitors to a single-task input, invent a DAG, or change FMC stopping.
The execution layer is reused from the existing two-UR5 fixture; no BT provider
or ROS service is used. Default prepares inputs; --execute runs the experiment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from algorithm.system.coordination.native_runtime import sha256_file
from benchmark.native_algorithms import build_native_adapter
from task.contracts import EaiTaskPackage, TaskRunRequest, OrchestrationLimits
from task.runner import TaskRunner
from tools.algorithm_adapters.isaac_bt_multi_robot import (
    IsaacTwoUR5Host, case_document as base_case, write_json, MOVES, BASES,
    STARTS, SEMANTICS, SOURCE_FILES,
)


def case_document():
    case = base_case()
    case['task_id'] = 'fmc-two-ur5-independent-missions'
    case['metadata'] = {'fmc_ata': {'domain': 'independent_single_mission_single_player'},
        'attachment_semantics': SEMANTICS,
        'planning_units': 'declared meters, seconds and unit importance; no geometry scaling',
        'planning_duration_scope': 'workload/productivity nominal; not measured manipulation duration',
        'travel_scope': 'native point-player travel model; fixed UR5 execution does not validate travel timing'}
    for robot in case['robots']:
        robot['capabilities'] = ['manipulate']
        robot['metadata']['fmc_ata'] = {'speed': 1., 'productivity': 1., 'status': 'idle'}
    case['tasks'] = [item for item in case['tasks'] if item['task_id'] in MOVES]
    for item in case['tasks']:
        item['metadata'].pop('bt_native')
        item['metadata']['fmc_ata'] = {'position': item['metadata']['position'],
                                     'workload': 30., 'importance': 1., 'max_players': 1}
    return case


def validate_initial(decision):
    assignments = decision.assignments
    if ({a.task_id for a in assignments} != set(MOVES) or len(assignments) != 2
            or len({a.robot_id for a in assignments}) != 2):
        raise ValueError('this physical fixture requires two native queue heads on different arms')
    for a in assignments:
        if a.robot_id not in BASES or math.dist(BASES[a.robot_id][:2], STARTS[a.task_id][:2]) > .8:
            raise ValueError('native-selected fixed arm is unreachable; no host reassignment')


class FMCPhysicalHost(IsaacTwoUR5Host):
    """Same measured manipulation and shared ticks; evaluate exactly two tasks."""
    def evaluate(self, **kwargs):
        passed = (set(self.receipts) == set(MOVES)
                  and all(r['passed'] is True for r in self.receipts.values())
                  and self.concurrency.report()['passed'])
        return {'status': 'success' if passed else 'failure', 'attachment_semantics': SEMANTICS,
                'scope': 'two_independent_native_FMC_assignments_to_same_scene_UR5_execution',
                'benchmark_scores_available': False, 'native_dynamic_replanning': False,
                'concurrency': self.concurrency.report()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--native-python', type=Path)
    parser.add_argument('--native-timeout-s', type=float, default=10.)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.native_timeout_s) or args.native_timeout_s <= 0:
        parser.error('--native-timeout-s must be finite and positive')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('--output must be new or empty')
    args.output.mkdir(parents=True, exist_ok=True)
    task_path = args.output / 'task.json'
    write_json(task_path, case_document())
    task = EaiTaskPackage.from_json(task_path)
    paths = set(SOURCE_FILES) | {'tools/algorithm_adapters/isaac_fmc_multi_robot.py',
        'algorithm/system/coordination/fmc_ata/native_adapter.py',
        'algorithm/system/coordination/fmc_ata/execution.py', 'benchmark/native_algorithms.py'}
    hashes = {p: sha256_file(ROOT / p) for p in sorted(paths)}
    hashes['task.json'] = sha256_file(task_path)
    report = {'schema': 'heterocoord.fmc_multi_robot.v1',
        'started_at_utc': datetime.now(timezone.utc).isoformat(), 'source_sha256': hashes,
        'passed': False, 'physical_success': None, 'physical_execution_started': False,
        'mode': 'execute' if args.execute else 'prepare', 'attachment_semantics': SEMANTICS,
        'native_feedback': None, 'feedback_scope': 'host_schedule_consumption_not_native_FMC_update',
        'benchmark_scores_available': False}
    host = session = adapter = None
    try:
        if args.execute:
            from algorithm.system.coordination.fmc_ata.execution import NativeFMCScheduleSession, NativeFMCFeedbackExecutor
            adapter = build_native_adapter('fmc_ata', source_root=args.source_root,
                python_executable=args.native_python, timeout_seconds=args.native_timeout_s)
            session = NativeFMCScheduleSession(adapter)
            host = FMCPhysicalHost(args.output, gui=args.gui)
            def initial(package, robots):
                decision = session(package, robots)
                write_json(args.output / 'native_decision.json', {'condition': decision.condition.to_dict(),
                    'assignments': [vars(a) for a in decision.assignments], 'provenance': decision.provenance})
                validate_initial(decision)
                return decision
            result = TaskRunner(orchestration_executor=NativeFMCFeedbackExecutor(session, host), evaluator=host).run_orchestrated(
                TaskRunRequest(task), initial, replan=session.replan,
                limits=OrchestrationLimits(max_rounds=4, max_dispatches=2))
            passed = (result.verdict['status'] == 'success'
                      and set(result.task_states.values()) == {'completed'}
                      and session.snapshot()['status'] == 'completed')
            report.update(passed=passed, physical_success=result.verdict['status'] == 'success',
                          status='accepted' if passed else 'not_accepted', result=result.to_dict())
        else:
            from algorithm.system.coordination.fmc_ata.native_adapter import NativeFMCATAAdapter
            write_json(args.output / 'native_payload.json', NativeFMCATAAdapter(args.source_root)._encode(task, task.robots))
            report.update(status='prepared_not_executed')
    except Exception as exc:
        (args.output / 'error.txt').write_text(traceback.format_exc())
        report.update(status='error', error=str(exc), error_type=type(exc).__name__,
                      native_error=getattr(exc, 'native_result', None),
                      native_source_provenance=getattr(exc, 'native_source_provenance', None))
    finally:
        if session:
            report['host_schedule_session'] = session.snapshot()
            session.close()
        if host:
            report.update(physical_execution_started=host.started, physics_ticks=host.tick,
                          physical_concurrency=host.concurrency.report())
            if hasattr(host, 'clearances'):
                write_json(args.output / 'clearance.json', {k: v.report() for k,v in host.clearances.items()})
            try:
                host.close()
            except Exception as exc:
                report.update(passed=False, status='error', host_cleanup_error=str(exc))
        report['input_files_unchanged'] = all(sha256_file(task_path if p == 'task.json' else ROOT / p) == h for p,h in hashes.items())
        if not report['input_files_unchanged']:
            report.update(passed=False, status='input_changed')
        for filename in ('native_decision.json', 'move_left.json', 'move_right.json', 'shared_ticks.jsonl', 'scene_audit.json', 'clearance.json'):
            path = args.output / filename
            if path.is_file():
                report[filename + '_sha256'] = sha256_file(path)
        write_json(args.output / 'receipt.json', report)
    print(json.dumps({k: report.get(k) for k in ('status','passed','physical_execution_started','physical_success')}, indent=2), flush=True)
    code = 0 if report['status'] in ('accepted', 'prepared_not_executed') else 1
    if host:
        host.shutdown_application(code)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
