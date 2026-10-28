"""Read-only native worker diagnosis; writes stacks without altering algorithm."""
import faulthandler
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path('/home/airs/hetrocoord')
spec = importlib.util.spec_from_file_location('fmc_io', ROOT / 'algorithm/system/coordination/fmc_ata/native_adapter.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
folder = Path(__file__).parent

def observe():
    time.sleep(5)
    faulthandler.dump_traceback(all_threads=True)
    for frame in sys._current_frames().values():
        while frame:
            obj = frame.f_locals.get('solver')
            if obj is not None and hasattr(obj, 'tasks_algorithm'):
                snapshot = {'nclo': obj.mailer.time_mailer.get_clock(),
                    'sent': obj.mailer.msg_sent_counter,
                    'mailer_inbox_empty': obj.mailer.inbox.is_buffer_empty(),
                    'pending_mailer_messages': len(obj.mailer.msg_box),
                    'tasks': [{'id': a.simulation_entity.id_, 'finished': a.is_finish_phase_II,
                               'counters': repr(a.counter_of_converges_dict),
                               'price_delta': repr(a.price_delta), 'allocation': repr(a.x_jk_normal)} for a in obj.tasks_algorithm],
                    'players': [{'id': a.simulation_entity.id_, 'schedule': repr(a.simulation_entity.schedule)} for a in obj.players_algorithm]}
                (folder / 'single_stalled_state.json').write_text(json.dumps(snapshot, indent=2))
                return
            frame = frame.f_back

threading.Thread(target=observe, daemon=True).start()
result = module._native_worker(json.loads((folder / 'single_payload.json').read_text()))
(folder / 'single_worker_output.json').write_text(json.dumps(result, indent=2))
