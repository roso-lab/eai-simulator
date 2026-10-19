"""Self-contained deterministic DWAQ inference, including five-frame state."""
from pathlib import Path
import torch
from torch import nn


class DwaqPolicy(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(500,128),nn.ELU(),nn.Linear(128,64),nn.ELU())
        self.encode_mean_vel = nn.Linear(64,3)
        self.encode_mean_latent = nn.Linear(64,16)
        self.actor = nn.Sequential(nn.Linear(119,512),nn.ELU(),nn.Linear(512,256),nn.ELU(),
                                   nn.Linear(256,128),nn.ELU(),nn.Linear(128,29))

    def forward(self, frame: torch.Tensor, history: torch.Tensor, reset: torch.Tensor):
        # CircularBuffer training semantics: oldest first, append CURRENT frame
        # before encoding; the first observation after reset fills all five slots.
        shifted = torch.cat((history[:,100:],frame),dim=-1)
        next_history = torch.where(reset.reshape(-1,1),frame.repeat(1,5),shifted)
        features = self.encoder(next_history)
        latent = torch.cat((self.encode_mean_vel(features),self.encode_mean_latent(features)),dim=-1)
        actions = self.actor(torch.cat((latent,frame.clamp(-100.,100.)),dim=-1))
        return actions,next_history


def load_checkpoint(path=None):
    path = Path(path) if path else Path(__file__).with_name('model_287.pt')
    state = torch.load(path,map_location='cpu',weights_only=True)['model_state_dict']
    model = DwaqPolicy()
    model.load_state_dict({key:state[key] for key in model.state_dict()},strict=True)
    if not all(torch.isfinite(p).all() for p in model.parameters()):
        raise ValueError('Nonfinite checkpoint parameters')
    return model.eval()
