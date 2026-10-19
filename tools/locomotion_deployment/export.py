"""Export the bundled checkpoints; no Isaac Sim or training checkout required."""
from pathlib import Path
import sys
import torch
import onnx

ROOT=Path(__file__).resolve().parents[2]
CONTROLLERS=ROOT/'source/EAI_assets/EAI_assets/controller'
sys.path.insert(0,str(CONTROLLERS))
from g1_dwaq.policy import load_checkpoint

def main():
    torch.set_num_threads(2)
    g1=load_checkpoint()
    folder=CONTROLLERS/'g1_dwaq'
    torch.jit.script(g1).save(str(folder/'policy.pt'))
    torch.onnx.export(g1,(torch.zeros(1,100),torch.zeros(1,500),torch.ones(1,dtype=torch.bool)),
        str(folder/'policy.onnx'),input_names=['frame','history','reset'],output_names=['actions','next_history'],
        dynamic_axes={name:{0:'batch'} for name in ['frame','history','reset','actions','next_history']},
        opset_version=17,dynamo=False)
    folder=CONTROLLERS/'go2_extreme_parkour/exported'
    go2=torch.jit.load(str(folder/'policy.pt'),map_location='cpu').eval()
    torch.onnx.export(go2,torch.zeros(1,753),str(folder/'policy_dynamic.onnx'),
        input_names=['obs'],output_names=['actions'],dynamic_axes={'obs':{0:'batch'},'actions':{0:'batch'}},
        opset_version=17,dynamo=False)
    for path in [CONTROLLERS/'g1_dwaq/policy.onnx',folder/'policy_dynamic.onnx']:
        onnx.checker.check_model(onnx.load(str(path)))
        print(path)

if __name__=='__main__':main()
