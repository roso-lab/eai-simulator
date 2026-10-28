"""Bounded original HARL training with custom environment dependency wiring.

Only runner construction is adapted because upstream hard-codes environment
names. run/collect/insert/compute/train/after_update/save and actor/critic Adam
optimizers are all inherited/imported unchanged. No source monkeypatches.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from .native_adapter import CONTRACT, SOURCE_COMMIT, NativeHAPPOAdapter, digest, load_native


def parameter_hash(actor):
    return digest(b"".join(k.encode() + v.detach().cpu().numpy().tobytes()
                          for k, v in sorted(actor.actor.state_dict().items())))


def train(output, *, source_root=None, updates=64, seed=11):
    if updates < 1:
        raise ValueError("at least one native optimizer update required")
    if set(range(seed, seed+4)) & {10011, 10012}:
        raise ValueError("training seeds must be disjoint from fixed holdout seeds")
    root, _ = load_native(source_root)
    import numpy as np
    import torch
    import yaml
    from tensorboardX import SummaryWriter
    from harl.runners.on_policy_ha_runner import OnPolicyHARunner
    from harl.algorithms.actors import ALGO_REGISTRY
    from harl.algorithms.critics.v_critic import VCritic
    from harl.common.buffers.on_policy_actor_buffer import OnPolicyActorBuffer
    from harl.common.buffers.on_policy_critic_buffer_ep import OnPolicyCriticBufferEP
    from harl.common.valuenorm import ValueNorm
    from harl.common.base_logger import BaseLogger
    from harl.envs.env_wrappers import ShareDummyVecEnv
    from harl.utils.envs_tools import set_seed
    from .macro_environment import EaiMacroEnvironment, SymbolicMacroExecutor
    from task.contracts import TaskRunRequest
    from task.runner import TaskRunner

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists():
        raise FileExistsError("refusing to overwrite an existing trained bundle")
    cfg = yaml.safe_load((root / "harl/configs/algos_cfgs/happo.yaml").read_text())
    cfg["seed"].update(seed_specify=True, seed=seed)
    cfg["device"].update(cuda=False, torch_threads=1)
    cfg["model"].update(hidden_sizes=[32, 32], use_recurrent_policy=True, data_chunk_length=4)
    cfg["train"].update(n_rollout_threads=4, episode_length=16, num_env_steps=updates*64,
                         log_interval=max(1, updates//4), eval_interval=updates, model_dir=None)
    cfg["eval"]["use_eval"] = False
    # Default HAPPO optimizer/loss/hyperparameters remain those of upstream.
    class MacroLogger(BaseLogger):
        def get_task_name(self):
            return CONTRACT["schema"]

    class EaiEnvironmentRunner(OnPolicyHARunner):
        def __init__(self):
            self.args = {"algo": "happo", "env": "eai_macro", "exp_name": "local-smoke"}
            self.algo_args, self.env_args = cfg, {"state_type": "EP", "contract": deepcopy(CONTRACT)}
            self.hidden_sizes = cfg["model"]["hidden_sizes"]
            self.rnn_hidden_size, self.recurrent_n = self.hidden_sizes[-1], cfg["model"]["recurrent_n"]
            self.action_aggregation = cfg["algo"]["action_aggregation"]
            self.state_type, self.share_param, self.fixed_order = "EP", False, cfg["algo"]["fixed_order"]
            set_seed(cfg["seed"])
            torch.set_num_threads(1)
            self.device = torch.device("cpu")
            self.run_dir, self.log_dir, self.save_dir = str(output), str(output/"logs"), str(output)
            self.writter = SummaryWriter(self.log_dir)
            self.envs = ShareDummyVecEnv([lambda s=seed+i: EaiMacroEnvironment(s) for i in range(4)])
            self.eval_envs, self.num_agents = None, 2
            args = {**cfg["model"], **cfg["algo"]}
            self.actor = [ALGO_REGISTRY["happo"](args, self.envs.observation_space[i],
                          self.envs.action_space[i], self.device) for i in range(2)]
            self.actor_buffer = [OnPolicyActorBuffer({**cfg["train"], **cfg["model"]},
                                self.envs.observation_space[i], self.envs.action_space[i]) for i in range(2)]
            self.critic = VCritic(args, self.envs.share_observation_space[0], self.device)
            self.critic_buffer = OnPolicyCriticBufferEP({**cfg["train"], **cfg["model"], **cfg["algo"]},
                                                       self.envs.share_observation_space[0])
            self.value_normalizer = ValueNorm(1, device=self.device)
            self.logger = MacroLogger(self.args, cfg, self.env_args, 2, self.writter, self.run_dir)

    runner = EaiEnvironmentRunner()
    before = [parameter_hash(a) for a in runner.actor]
    try:
        runner.run()
        runner.save()
        actors, proofs = [], []
        for i, a in enumerate(runner.actor):
            filename = f"actor_agent{i}.pt"
            checkpoint_hash = digest((output/filename).read_bytes())
            steps = [int(v["step"].item()) for v in a.actor_optimizer.state.values() if "step" in v]
            final = parameter_hash(a)
            if not steps or min(steps) <= 0 or final == before[i]:
                raise RuntimeError("native training did not update all actors")
            actors.append({"actor_index": i, "robot_id": CONTRACT["actors"][i]["robot_id"],
                           "file": filename, "sha256": checkpoint_hash})
            proofs.append({"actor_index": i, "initial_hash": before[i], "final_hash": final,
                           "optimizer_steps": min(steps), "checkpoint_sha256": checkpoint_hash})
        # Proof that the inherited algorithm methods are the original objects.
        methods = {name: getattr(type(runner), name) is getattr(OnPolicyHARunner, name)
                   for name in ("run", "collect", "insert", "compute", "train", "after_update", "save")}
        receipt = {"condition": "locally_trained_macro_smoke", "source_commit": SOURCE_COMMIT,
                   "contract": CONTRACT, "updates": updates, "environment_steps": updates*64,
                   "train_seeds": list(range(seed, seed+4)), "holdout_seeds": [10011, 10012],
                   "actors": proofs, "original_runner_methods": methods,
                   "algorithm_config": cfg, "physical_evidence": "N/A", "benchmark_performance": "not_evaluable",
                   "dependencies": {"torch": torch.__version__, "numpy": np.__version__}}
        (output/"training_receipt.json").write_text(json.dumps(receipt, indent=2)+"\n")
        manifest = {"schema": "heterocoord.happo.checkpoint.v1", "algorithm": "HAPPO",
                    "condition": "locally_trained_macro_smoke", "contract": CONTRACT,
                    "source_commit": SOURCE_COMMIT, "actor_args": {**cfg["model"], **cfg["algo"]},
                    "actors": actors, "training_receipt": {"file": "training_receipt.json",
                    "sha256": digest((output/"training_receipt.json").read_bytes())}}
        (output/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    finally:
        runner.close()

    holdout = []
    adapter = NativeHAPPOAdapter(output/"manifest.json", source_root=root)
    for holdout_seed in (10011, 10012):
        env = EaiMacroEnvironment(holdout_seed, split="holdout")
        for _ in range(8):
            env.reset()
            adapter.reset()
            executor = SymbolicMacroExecutor()
            task_runner = TaskRunner(executor=executor)
            rounds = []
            for tick in range(CONTRACT["horizon"]):
                result = task_runner.run(TaskRunRequest(env.package), adapter)
                actions = np.array([[item["action"]] for item in result.decision.provenance["actor_outputs"]])
                _, _, _, done, _, _ = env.step(actions)
                adapter.update_feedback(result.feedback, terminal=bool(done.all()))
                rounds.append({**result.to_dict(), "native_provenance": result.decision.provenance})
                if done.all():
                    break
            holdout.append({"seed": holdout_seed, "task_id": env.package.task_id,
                            "symbolic_completed_tasks": sorted(env.completed), "rounds": rounds,
                            "dispatches": executor.dispatched, "physical_evidence": "N/A"})
    evidence = {"condition": "locally_trained_macro_smoke", "episodes": holdout,
                "symbolic_all_completed": sum(len(e["symbolic_completed_tasks"]) == 2 for e in holdout),
                "episode_count": len(holdout), "simulation": "symbolic_macro_only",
                "benchmark_performance": "not_evaluable", "physical_evidence": "N/A"}
    (output/"holdout_taskrunner.json").write_text(json.dumps(evidence, indent=2)+"\n")
    return output/"manifest.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root")
    parser.add_argument("--output", required=True)
    parser.add_argument("--updates", type=int, default=64)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    print(train(args.output, source_root=args.source_root, updates=args.updates, seed=args.seed))


if __name__ == "__main__":
    main()
