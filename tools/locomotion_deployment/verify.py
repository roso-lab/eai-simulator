"""Numerical exports, batched isolation, reset and observation contract checks."""
from pathlib import Path
import sys,json,hashlib
from types import SimpleNamespace
import torch
import numpy as np
import onnxruntime as ort

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'source/EAI_assets/EAI_assets/controller'
sys.path.insert(0,str(BASE))
from g1_dwaq.policy import load_checkpoint
from g1_dwaq.deployment import locomotion_frame,critic_height_scan

def main():
    torch.set_num_threads(2);torch.manual_seed(109)
    report={'batches':{},'checks':[]}
    g1=load_checkpoint();jit=torch.jit.load(str(BASE/'g1_dwaq/policy.pt'))
    go2=torch.jit.load(str(BASE/'go2_extreme_parkour/exported/policy.pt'))
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    sess1=ort.InferenceSession(str(BASE/'g1_dwaq/policy.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    sess2=ort.InferenceSession(str(BASE/'go2_extreme_parkour/exported/policy_dynamic.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    old=ort.InferenceSession(str(BASE/'go2_extreme_parkour/exported/policy.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    for n in (1,4,32):
        frame=torch.randn(n,100);history=torch.randn(n,500);reset=torch.arange(n)%2==0
        max1=max2=0.
        for step in range(7):
            with torch.no_grad():a,h=g1(frame,history,reset);b,jh=jit(frame,history,reset)
            oa,oh=sess1.run(None,dict(frame=frame.numpy(),history=history.numpy(),reset=reset.numpy()))
            torch.testing.assert_close(a,b);np.testing.assert_allclose(oa,a.numpy(),rtol=1e-4,atol=1e-5)
            np.testing.assert_array_equal(oh,h.numpy());torch.testing.assert_close(jh,h)
            for i in range(n):
                with torch.no_grad():single,_=g1(frame[i:i+1],history[i:i+1],reset[i:i+1])
                torch.testing.assert_close(single,a[i:i+1],rtol=1e-4,atol=1e-5)
            max1=max(max1,float(np.max(np.abs(oa-a.numpy()))))
            history=h;frame=torch.randn(n,100);reset=torch.arange(n)%3==step
        x=torch.randn(n,753)
        with torch.no_grad():a=go2(x).numpy()
        b=sess2.run(None,{'obs':x.numpy()})[0]
        c=np.concatenate([old.run(None,{old.get_inputs()[0].name:x[i:i+1].numpy()})[0] for i in range(n)])
        np.testing.assert_allclose(a,b,rtol=1e-4,atol=1e-5);np.testing.assert_allclose(c,b,rtol=1e-4,atol=1e-5)
        report['batches'][n]={'g1_max_abs_error':max1,'go2_max_abs_error':float(np.max(np.abs(a-b))),'go2_vs_original_onnx_error':float(np.max(np.abs(c-b)))}
    # Named slices, gait origin, actual per-joint scales and explicit scan exclusion.
    data=SimpleNamespace(root_ang_vel_b=torch.tensor([[1.,2.,3.]]),projected_gravity_b=torch.tensor([[0.,0.,-1.]]),
        joint_pos=torch.arange(29).float()[None],default_joint_pos=torch.zeros(1,29),
        joint_vel=torch.arange(29).float()[None]*2,default_joint_vel=torch.zeros(1,29))
    f=locomotion_frame(data,list(range(29)),torch.ones(1,29)*4,torch.tensor([[.2,.3,.4]]),torch.zeros(1,dtype=torch.long))
    torch.testing.assert_close(f[0,9:38],torch.arange(29).float())
    torch.testing.assert_close(f[0,38:67],torch.arange(29).float()*2)
    torch.testing.assert_close(f[0,67:96],torch.ones(29)*4)
    torch.testing.assert_close(f[0,98:100],torch.tensor([1.,-1.]))
    heights=critic_height_scan(torch.tensor([[0.,0.,1.]]),torch.zeros(1,187,3))
    assert heights.shape==(1,187) and (heights==.5).all() and f.shape==(1,100)
    report['checks']=['G1 7 sequential updates with partial resets and batched isolation','G1 named frame slices and phase origin','G1 scan excluded from actor','Go2 dynamic ONNX vs original TorchScript and fixed-batch ONNX']
    report['sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE/'g1_dwaq/policy.pt',BASE/'g1_dwaq/policy.onnx',BASE/'go2_extreme_parkour/exported/policy_dynamic.onnx']}
    out=Path(__file__).with_name('verification.json');out.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
