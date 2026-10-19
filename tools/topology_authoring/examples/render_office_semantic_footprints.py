"""Bind editable room/object semantics to measured occupied masks and polygons."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import Polygon, Patch
import numpy as np
from shapely.geometry import box, mapping
from shapely.ops import unary_union


def rectangle(bounds):
    x0,y0,x1,y1=bounds
    if not np.isfinite(bounds).all() or not (x0<x1 and y0<y1):
        raise ValueError('invalid semantic bounds')
    return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]


def runs_and_geometry(mask, origin, resolution):
    """Exact grid-aligned boundary, including disjoint pieces and holes."""
    runs=[];pieces=[]
    for j,row in enumerate(mask):
        edges=np.diff(np.r_[False,row,False].astype(int))
        for start,end in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
            runs.append([int(j),int(start),int(end)])
            pieces.append(box(origin[0]+start*resolution,origin[1]+j*resolution,
                              origin[0]+end*resolution,origin[1]+(j+1)*resolution))
    if not pieces:raise ValueError('semantic selector has no occupied cells')
    geometry=unary_union(pieces)
    if abs(geometry.area-mask.sum()*resolution**2)>1e-8:
        raise ValueError('semantic footprint area differs from mask')
    return runs,mapping(geometry)


def build_level(grid, objects, regions):
    x,y=grid['x'],grid['y'];X,Y=np.meshgrid(x,y)
    instance=np.full(grid['occupied'].shape,-1,np.int16);instance[grid['occupied']]=0
    room=np.zeros(instance.shape,np.int16)
    for index,row in enumerate(regions,1):
        x0,y0,x1,y1=row['bounds_xy'];rectangle(row['bounds_xy'])
        mask=(X>=x0)&(X<=x1)&(Y>=y0)&(Y<=y1)
        if np.any(room[mask]):raise ValueError('overlapping room selectors')
        room[mask]=index
    masks=[]
    for index,row in enumerate(objects,1):
        x0,y0,x1,y1=row['bounds_xy'];rectangle(row['bounds_xy'])
        mask=grid['occupied']&(X>=x0)&(X<=x1)&(Y>=y0)&(Y<=y1)
        if not mask.any():raise ValueError(f"no obstacle geometry for {row['id']}")
        if np.any(instance[mask]>0):raise ValueError(f"overlapping object labels: {row['id']}")
        instance[mask]=index;masks.append(mask)
    return instance,room,masks


def draw_level(ax,grid,objects,regions,instance,room,title):
    x,y=grid['x'],grid['y'];res=float(grid['resolution_m'])
    extent=[x[0]-res/2,x[-1]+res/2,y[0]-res/2,y[-1]+res/2]
    colors=np.full((*instance.shape,3),to_rgb('#dbe0e6'))
    colors[grid['support']]=to_rgb('#f8fafb')
    for i,r in enumerate(regions,1):
        colors[(room==i)&grid['support']]=.87+.13*np.array(to_rgb(r['color']))
    colors[grid['occupied']]=to_rgb('#424b56')
    for i,o in enumerate(objects,1):colors[instance==i]=to_rgb(o['color'])
    ax.imshow(colors,origin='lower',extent=extent,interpolation='nearest')
    for r in regions:
        ax.add_patch(Polygon(rectangle(r['bounds_xy']),closed=True,fill=False,ec=r['color'],lw=1,ls='--',alpha=.85))
        ax.text(*r['label_xy'],r['label'],ha='center',va='center',fontsize=10,weight='bold',color=r['color'],
                bbox=dict(boxstyle='round,pad=.25',fc='white',ec='none',alpha=.87),zorder=6)
    for index,o in enumerate(objects,1):
        points=np.argwhere(instance==index)
        xy=np.c_[x[points[:,1]],y[points[:,0]]]
        label=np.array(o['label_xy']);target=xy[np.argmin(((xy-label)**2).sum(1))]
        inside=np.linalg.norm(target-label)<res
        ax.annotate(o['label'],xy=target,xytext=label,ha='center',va='center',fontsize=8,
                    color='white' if inside else '#233342',
                    bbox=dict(boxstyle='round,pad=.22',fc=o['color'] if inside else 'white',ec=o['color'],alpha=.96),
                    arrowprops=None if inside else dict(arrowstyle='-',color=o['color'],lw=1),zorder=7)
    ax.set(title=title,xlabel='世界坐标 X (m)',ylabel='世界坐标 Y (m)',aspect='equal')
    ax.set_xlim(-3.1,3.1);ax.set_ylim(-3.7,3.7)
    ax.tick_params(labelsize=8);ax.grid(alpha=.12)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--annotations',type=Path,required=True);p.add_argument('--grid-root',type=Path,required=True)
    p.add_argument('--topology',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True)
    a=p.parse_args();a.output_root.mkdir(parents=True,exist_ok=False)
    config=json.loads(a.annotations.read_text());original=json.loads(a.topology.read_text())
    evidence=Path(config['evidence_root']);capture=json.loads((evidence/'capture.json').read_text())
    manifest=json.loads((a.grid_root/'navigation-manifest.json').read_text())
    if not capture['source_unchanged']:raise ValueError('capture source changed')
    if any(digest not in capture['source_digests'].values() for digest in manifest['source_layer_sha256'].values()):
        raise ValueError('capture/map source mismatch')
    for path,digest in manifest['source_layer_sha256'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:raise ValueError('live source mismatch')
    plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    doc=copy.deepcopy(original);doc['semantics']['regions']=[];doc['semantics']['entities']=[]
    doc['semantics']['interaction_poses']=[]
    doc['semantics']['extensions']['semantic_geometry']={'schema':'eai.semantic_raster','version':1,
        'objects_file':'semantic-footprints.json','unknown_obstacle_instance':0,
        'non_obstacle_instance':-1,'source_grid_root':str(a.grid_root.resolve())}
    # Keep graph geometry unchanged; remove the old incorrect room naming.
    for n in doc['nodes']:
        if n['id']=='l2:meeting-area':n['label']='二层走廊原草稿点（非会议室）'
        if n['id']=='roof:tower-stair':n['label']='原屋面草稿点（非已定位楼梯）'
    for route in doc['semantics']['routes']:
        if route['id']=='route:2F-stairs-to-meeting':route['label']='二层楼梯前走廊原路线（未验证）'
    all_features=[];reports={};panels=[];exports=[]
    for level in doc['levels']:
        lid,z=level['id'],level['support_z_m']
        grid=dict(np.load(a.grid_root/f'{lid}-occupancy.npz'))
        objects=[r for r in config['objects'] if r['level_id']==lid]
        regions=[r for r in config['regions'] if r['level_id']==lid]
        instance,room,masks=build_level(grid,objects,regions)
        np.savez_compressed(a.output_root/f'{lid}-semantic-grid.npz',**grid,instance_id=instance,region_id=room)
        for index,r in enumerate(regions,1):
            polygon=rectangle(r['bounds_xy'])
            doc['semantics']['regions'].append(dict(id=r['id'],kind=r['kind'],label=r['label'],aliases=[],level_id=lid,
                polygon_xyz=[[x,y,z] for x,y in polygon],height_m=.8,entrance_node_ids=[],
                properties={'confidence':r['confidence'],'evidence':str(evidence/f'{lid}-material-top.png'),
                            'boundary_status':'authored_from_floor_plan','raster_region_id':index}))
            all_features.append(dict(type='Feature',geometry=mapping(box(*r['bounds_xy'])),
                                     properties=dict(r,feature_type='room_region',raster_id=index)))
        for index,(o,mask) in enumerate(zip(objects,masks),1):
            runs,geom=runs_and_geometry(mask,grid['origin_xy'],float(grid['resolution_m']))
            indices=np.argwhere(mask);cx=float(grid['x'][indices[:,1]].mean());cy=float(grid['y'][indices[:,0]].mean())
            record=dict(o,instance_id=index,occupied_cell_count=int(mask.sum()),mask_rle_row_start_end=runs,
                        footprint_geojson=geom,raster_file=f'{lid}-semantic-grid.npz')
            exports.append(record)
            doc['semantics']['entities'].append(dict(id=o['id'],kind=o['kind'],label=o['label'],aliases=[],level_id=lid,
                region_id=o.get('region_id'),position_xyz=[cx,cy,z],source_ref=None,
                properties={'confidence':o['confidence'],'evidence':str(evidence/f'{lid}-material-top.png'),
                            'geometry_binding':{'raster_file':record['raster_file'],'instance_id':index,'footprint_geojson':geom},
                            'position_source':'centroid_of_bound_occupied_cells','pose_status':'task_interactions_deferred'}))
            all_features.append(dict(type='Feature',geometry=geom,properties=dict(o,feature_type='obstacle_instance',raster_id=index)))
        reports[lid]={'objects':len(objects),'regions':len(regions),'occupied_cells':int(grid['occupied'].sum()),
                      'labeled_occupied_cells':int((instance>0).sum()),'unknown_occupied_cells':int((instance==0).sum())}
        panels.append((grid,objects,regions,instance,room,lid))
        fig,ax=plt.subplots(figsize=(10,11))
        draw_level(ax,*panels[-1][:-1],f'Office {lid} · 区域与障碍物语义地图')
        fig.text(.5,.04,'浅色虚线框：功能区域  ·  彩色实体：绑定障碍轮廓的语义  ·  深灰：未细分障碍',ha='center',fontsize=10)
        fig.text(.5,.018,'区域功能依据可见布局命名；茶水间、洗手台尚未确认。路线请单独查看拓扑草稿。',ha='center',fontsize=9)
        fig.tight_layout(rect=[0,.065,1,.97]);fig.savefig(a.output_root/f'{lid}-semantic-map.png',dpi=180);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(20,10))
    for ax,panel in zip(axes,panels):draw_level(ax,*panel[:-1],f'Office {panel[-1]}')
    fig.suptitle('Office · 把语义绑定到房间区域和障碍物轮廓',fontsize=19)
    fig.text(.5,.04,'浅色虚线框 = 功能区域    彩色实体 = 物体语义    深灰 = 未细分障碍    蓝灰 = 无本层支撑 / 未知',ha='center',fontsize=12)
    fig.text(.5,.012,'房间功能按布局推断，边界可编辑；未把无证据区域标为茶水间或洗手台。',ha='center',fontsize=11)
    fig.tight_layout(rect=[0,.07,1,.96]);fig.savefig(a.output_root/'office-semantic-overview.png',dpi=170);plt.close(fig)
    from tools.topology_authoring.model import TopologyDocument
    from tools.topology_authoring.product import generate_product,load_product
    product=TopologyDocument.from_dict(doc)
    generate_product(a.output_root/'topology.json',product)
    load_product(a.output_root/'topology.json')
    (a.output_root/'semantic-footprints.json').write_text(json.dumps(dict(config,objects=exports),ensure_ascii=False,indent=2)+'\n')
    (a.output_root/'semantic-footprints.geojson').write_text(json.dumps({'type':'FeatureCollection','features':all_features},ensure_ascii=False,indent=2)+'\n')
    report={'levels':reports,'validation_errors':[],'source_sha256':manifest['source_layer_sha256'],
            'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'annotations_sha256':hashlib.sha256(a.annotations.read_bytes()).hexdigest(),
            'registration':'orthographic world XY, selectors intersect occupied grid',
            'visual_reviewer':'Codex coordinator; Flash runtime unavailable this turn',
            'limitations':config['notes']}
    (a.output_root/'semantic-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
