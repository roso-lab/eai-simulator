from tools.benchmark_branch.resources import ResourceProbe


def test_resource_admission_honors_explicit_worker_ceiling():
    probe = ResourceProbe(gpu_sample_interval_s=0)
    admitted, reason = probe.admission(32, {"max_workers": 8})
    assert admitted == 8
    assert "max_workers" in reason
