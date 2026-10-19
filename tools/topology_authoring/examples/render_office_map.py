"""Render a review preview from an editable topology product (no USD edits)."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import matplotlib.pyplot as plt

def main():
    p=argparse.ArgumentParser(); p.add_argument("product",type=Path); p.add_argument("output",type=Path); a=p.parse_args()
    data=json.loads(a.product.read_text(encoding="utf-8")); levels={x["id"]:x for x in data["levels"]}
    fig, axes=plt.subplots(1,len(levels),figsize=(15,5),constrained_layout=True); axes=[axes] if len(levels)==1 else axes
    colors={"1F":"#dceeff","2F":"#ffe7b8","3F":"#d9f4d0"}
    for ax,(lid,level) in zip(axes,levels.items()):
        ax.set_title(f'{lid} {level["label"]}\nz={level["support_z_m"]:.3f} m'); ax.set_aspect("equal")
        ax.set_xlim(-3.2,3.2); ax.set_ylim(-3.8,3.8); ax.grid(alpha=.2)
        for region in data.get("semantics",{}).get("regions",[]):
            if region.get("level_id")!=lid: continue
            xy=[p[:2] for p in region["polygon_xyz"]]; xs,ys=zip(*(xy+[xy[0]])); ax.fill(xs,ys,color=colors.get(lid,"#ddd"),alpha=.65); ax.plot(xs,ys,"k-"); ax.text(sum(xs[:-1])/4,sum(ys[:-1])/4,region["label"],ha="center",fontsize=8)
        for node in data["nodes"]:
            if node.get("level_id")!=lid: continue
            x,y,z=node["position_xyz"]; ax.scatter(x,y,c="#b31b34" if node["kind"]=="transition" else "#155e9e",s=22,zorder=4); ax.text(x,y,node["label"],fontsize=6)
        for route in data.get("semantics",{}).get("routes",[]):
            nodes={n["id"]:n for n in data["nodes"]}; pts=[nodes[n]["position_xyz"] for n in route["node_ids"] if n in nodes and nodes[n].get("level_id")==lid]
            if len(pts)>1:
                ax.plot([p[0] for p in pts],[p[1] for p in pts],"r--",lw=1.5,label=route["label"])
        ax.legend(fontsize=6,loc="upper right")
    fig.suptitle("Office semantic topology preview — coordinates in metres; red dashed = authored stair draft")
    fig.savefig(a.output,dpi=180); plt.close(fig)

if __name__=="__main__": main()
