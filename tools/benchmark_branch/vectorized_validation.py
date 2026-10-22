"""Replay ordered slot ownership evidence without loading a simulator."""
from .vectorized import PARTICIPANTS, SCHEMA, SlotBinding
from .models import canonical_digest


def validate_vectorized(manifest, rows):
    errors = []
    ordered = [row for values in rows.values() for row in values if "vector_seq" in row]
    try:
        if any("vector_seq" not in row for name, values in rows.items() for row in values
               if (str(row.get("kind", "")).startswith("vector_")
                   or (row.get("branch_id") and name in {"steps", "observations", "episodes"}))):
            raise ValueError("vector evidence missing ordered sequence")
        if manifest.get("execution_scope") == "measured":
            raise ValueError("formal vectorized qualification is not yet supported")
        if any(type(row["vector_seq"]) is not int for row in ordered):
            raise ValueError("noninteger vector sequence")
        ordered.sort(key=lambda row: row["vector_seq"])
        if [row["vector_seq"] for row in ordered] != list(range(len(ordered))):
            raise ValueError("vector sequence missing or duplicated")
        if manifest.get("vectorized_contract") != SCHEMA or manifest.get("scheduler_integrated") is not True:
            raise ValueError("missing vectorized contract or scheduler integration")
        sessions, owners, generations, known, ticks, restored = {}, {}, {}, {}, {}, set()
        checkpoint_states = {}
        for row in rows.get("checkpoints", []):
            if row.get("base_checkpoint") and "vector_checkpoint_state" in row:
                state = row["vector_checkpoint_state"]
                if canonical_digest(state) != row.get("snapshot_digest"):
                    raise ValueError("frozen vector checkpoint digest mismatch")
                checkpoint_states[row["snapshot_digest"]] = state
        pending_steps = set()
        pending_observations = set()
        batches = 0
        for row in ordered:
            kind = row.get("kind")
            sid = row.get("shared_session_id")
            token = row.get("slot_token")
            if kind in {"vector_session_error", "vector_quarantined"}:
                errors.append("vectorized session was quarantined or failed")
            if kind == "vector_scene_opened":
                if sid in sessions or type(row.get("capacity")) is not int or row["capacity"] < 1:
                    raise ValueError("invalid vector scene lifecycle")
                sessions[sid] = row["capacity"]
                continue
            if kind == "vector_scene_closed":
                if sid not in sessions:
                    raise ValueError("closing unknown vectorized scene")
                sessions.pop(sid)
                if any(key[0] == sid for key in owners):
                    errors.append("scene close reconciled unfinished slot ownership")
                continue
            if kind in {"vector_slot_bound", "vector_slot_restored", "vector_slot_closed", "vector_batch"}:
                if sid not in sessions:
                    raise ValueError("slot lifecycle outside live scene")
                if row.get("peer_before") != row.get("peer_after"):
                    raise ValueError("peer state changed during slot operation")
                peers = {value for key, value in owners.items() if key[0] == sid}
                if kind == "vector_batch":
                    peers -= set(row.get("slot_tokens", ()))
                else:
                    peers.discard(token)
                if set(row.get("peer_before", {})) != peers:
                    raise ValueError("peer audit coverage is incomplete")
            if kind == "vector_slot_bound":
                slot, generation = row.get("environment_slot"), row.get("slot_generation")
                if type(slot) is not int or not 0 <= slot < sessions[sid] or type(generation) is not int:
                    raise ValueError("invalid slot/generation")
                key = sid, slot
                if key in owners or generation != generations.get(key, 0) + 1:
                    raise ValueError("slot concurrently owned or stale generation")
                binding = SlotBinding(sid, slot, generation, row["branch_id"])
                if token != binding.token or token in known or not PARTICIPANTS.issubset(row.get("participant_names", [])):
                    raise ValueError("slot token or participant coverage mismatch")
                if type(row.get("next_step")) is not int or row["next_step"] < 0:
                    raise ValueError("invalid initial step")
                if (not row.get("native_state_digest") or
                        row["native_state_digest"] != row.get("expected_participant_state_digest")):
                    raise ValueError("initial native state does not match checkpoint participants")
                frozen = checkpoint_states.get(row.get("checkpoint_digest"))
                if (not frozen or canonical_digest(frozen["participants"]) != row["native_state_digest"]
                        or frozen.get("next_step") != row["next_step"]):
                    raise ValueError("initial readback not bound to persisted checkpoint")
                owners[key] = token
                generations[key] = generation
                known[token] = binding
                ticks[token] = row["next_step"] - 1
                continue
            if kind == "vector_batch":
                if pending_steps or pending_observations:
                    raise ValueError("batch outputs missing before next batch")
                tokens = row.get("slot_tokens")
                if not isinstance(tokens, list) or not tokens or len(tokens) != len(set(tokens)):
                    raise ValueError("invalid batch slot tokens")
                for item in tokens:
                    binding = known.get(item)
                    if (binding is None or binding.session_id != sid
                            or owners.get((sid, binding.environment_slot)) != item or item not in restored):
                        raise ValueError("batch used stale/unrestored slot")
                batches += 1
                pending_steps.update(tokens)
                pending_observations.update(tokens)
                continue
            if token:
                binding = known.get(token)
                if binding is None or any(row.get(key) != value for key, value in binding.row().items()):
                    raise ValueError("cross-slot or stale output identity")
                if owners.get((sid, binding.environment_slot)) != token:
                    raise ValueError("output after slot close/reuse")
                if row.get("restore_passed") is not None:
                    if row.get("restore_passed") is not True or row.get("readback_digest") != row.get("checkpoint_digest"):
                        raise ValueError("vector restore readback failed")
                    if (not row.get("participant_state_digest") or
                            row["participant_state_digest"] != row.get("expected_participant_state_digest")
                            or not PARTICIPANTS.issubset(row.get("participant_versions", {}))):
                        raise ValueError("initial participant restore evidence incomplete")
                    restored.add(token)
                if kind == "vector_slot_closed":
                    if token in pending_steps or token in pending_observations:
                        raise ValueError("slot closed with missing batch output")
                    del owners[sid, binding.environment_slot]
                if kind == "vector_slot_restored":
                    if row.get("expected_digest") != row.get("readback_digest"):
                        raise ValueError("local restore digest mismatch")
                    if type(row.get("next_step")) is not int or row["next_step"] < 0:
                        raise ValueError("invalid restored clock")
                    ticks[token] = row["next_step"] - 1
                # The ledger name is added by the caller, not inferred from
                # arbitrary payloads carrying simulator_step.
                if row.get("_vector_ledger") == "steps":
                    if token not in pending_steps or token not in restored or row.get("simulator_step") != ticks[token] + 1:
                        raise ValueError("step before restore or inconsistent tick")
                    ticks[token] = row["simulator_step"]
                    pending_steps.remove(token)
                    if "action" not in row or "applied_action" not in row:
                        raise ValueError("vector step lacks control trace")
                if row.get("_vector_ledger") == "observations":
                    if (token not in pending_observations or token in pending_steps
                            or row.get("simulator_step") != ticks[token] or "observations" not in row):
                        raise ValueError("observation missing, duplicated or inconsistent with step")
                    pending_observations.remove(token)
            elif row.get("branch_id") and row.get("_vector_ledger") in {"steps", "observations", "events"}:
                raise ValueError("vector branch output lacks slot binding")
        if sessions or owners or pending_steps or pending_observations:
            errors.append("vectorized ownership remains active")
        if not known or not batches:
            errors.append("no managed vectorized execution evidence")
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(f"invalid vectorized isolation evidence: {exc}")
    return errors
