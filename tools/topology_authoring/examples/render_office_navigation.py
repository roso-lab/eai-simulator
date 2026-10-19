"""Height-bounded collision evidence, never a robot traversability certificate."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
from numba import njit


@njit(cache=True)
def triangle_box(triangle, center, half):
    """Exact closed triangle/AABB SAT; also handles vertical XY projections."""
    v = triangle - center
    edges = np.empty((3, 3))
    for k in range(3):
        edges[k] = v[(k+1)%3]-v[k]
    axes = np.zeros((13, 3))
    for k in range(3):
        axes[k, k] = 1.
    axes[3] = np.cross(edges[0], edges[1])
    for k in range(3):
        for j in range(3):
            axes[4+3*k+j] = np.cross(edges[k], axes[j])
    for axis in axes:
        radius = (np.abs(axis)*half).sum()
        p = v @ axis
        if p.min() > radius+1e-10 or p.max() < -radius-1e-10:
            return False
    return True


@njit(cache=True)
def rasterize(triangles, xmin, ymin, nx, ny, resolution, floor, low, high):
    """Support at cell centers; all-touched triangle hits in a 3-D height band.

    No large-face filter, bbox filling, support masking of walls, or implicit
    robot dimensions. Geometry is surface evidence, not solid-volume occupancy.
    """
    support = np.zeros((ny, nx), np.bool_)
    occupied = np.zeros((ny, nx), np.bool_)
    half = np.array([resolution/2, resolution/2, (high-low)/2])
    for t in triangles:
        lower, upper = np.empty(3), np.empty(3)
        for k in range(3):
            lower[k], upper[k] = t[:,k].min(), t[:,k].max()
        normal = np.cross(t[1]-t[0], t[2]-t[0])
        norm = np.sqrt((normal*normal).sum())
        if norm < 1e-12:
            continue
        near_floor = (abs(normal[2]) >= .985*norm and
                      upper[2] >= floor-.015 and lower[2] <= floor+.015)
        in_band = upper[2] >= low and lower[2] <= high
        if not near_floor and not in_band:
            continue
        i0 = max(0, int(np.floor((lower[0]-xmin)/resolution))-1)
        i1 = min(nx-1, int(np.floor((upper[0]-xmin)/resolution)))
        j0 = max(0, int(np.floor((lower[1]-ymin)/resolution))-1)
        j1 = min(ny-1, int(np.floor((upper[1]-ymin)/resolution)))
        for j in range(j0, j1+1):
            for i in range(i0, i1+1):
                x, y = xmin+(i+.5)*resolution, ymin+(j+.5)*resolution
                if near_floor and not support[j,i]:
                    ab, ac = t[1]-t[0], t[2]-t[0]
                    det = ab[0]*ac[1]-ab[1]*ac[0]
                    u = ((x-t[0,0])*ac[1]-(y-t[0,1])*ac[0])/det
                    w = (ab[0]*(y-t[0,1])-ab[1]*(x-t[0,0]))/det
                    h = t[0,2]+u*ab[2]+w*ac[2]
                    support[j,i] = (u >= -1e-9 and w >= -1e-9 and u+w <= 1+1e-9
                                    and abs(h-floor) <= .015)
                if in_band and not occupied[j,i]:
                    occupied[j,i] = triangle_box(t, np.array([x,y,(low+high)/2]), half)
    return support, occupied


@njit(cache=True)
def _height_at_xy(triangle, x, y):
    """Return (inside, z) for a non-vertical triangle at one XY point."""
    ab = triangle[1] - triangle[0]
    ac = triangle[2] - triangle[0]
    det = ab[0] * ac[1] - ab[1] * ac[0]
    if abs(det) < 1e-12:
        return False, 0.0
    u = ((x - triangle[0, 0]) * ac[1] - (y - triangle[0, 1]) * ac[0]) / det
    v = (ab[0] * (y - triangle[0, 1]) - ab[1] * (x - triangle[0, 0])) / det
    inside = u >= -1e-9 and v >= -1e-9 and u + v <= 1.0 + 1e-9
    return inside, triangle[0, 2] + u * ab[2] + v * ac[2]


@njit(cache=True)
def rasterize_heightfield(triangles, xmin, ymin, nx, ny, resolution,
                          support_min_z, support_max_z, reference_z,
                          minimum_support_cosine, select_lowest,
                          obstacle_low, obstacle_high):
    """Rasterize one sloped support band and obstacles relative to local ground.

    This is a 2.5-D evidence model: each supported cell stores one selected
    ground height.  It cannot represent caves, bridges or two walkable surfaces
    in the same XY cell; those require separate level bands/ROIs.
    """
    ground_z = np.full((ny, nx), np.nan)
    ground_triangle = np.full((ny, nx), -1, np.int64)
    score = np.full((ny, nx), np.inf)
    support_candidate = np.zeros(len(triangles), np.bool_)

    for triangle_index in range(len(triangles)):
        triangle = triangles[triangle_index]
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        norm = np.sqrt((normal * normal).sum())
        if norm < 1e-12 or abs(normal[2]) < minimum_support_cosine * norm:
            continue
        lower_z = triangle[:, 2].min()
        upper_z = triangle[:, 2].max()
        if upper_z < support_min_z or lower_z > support_max_z:
            continue
        support_candidate[triangle_index] = True
        lower_x, upper_x = triangle[:, 0].min(), triangle[:, 0].max()
        lower_y, upper_y = triangle[:, 1].min(), triangle[:, 1].max()
        i0 = max(0, int(np.floor((lower_x - xmin) / resolution)) - 1)
        i1 = min(nx - 1, int(np.floor((upper_x - xmin) / resolution)))
        j0 = max(0, int(np.floor((lower_y - ymin) / resolution)) - 1)
        j1 = min(ny - 1, int(np.floor((upper_y - ymin) / resolution)))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                x = xmin + (i + .5) * resolution
                y = ymin + (j + .5) * resolution
                inside, height = _height_at_xy(triangle, x, y)
                if not inside or height < support_min_z or height > support_max_z:
                    continue
                candidate_score = height if select_lowest else abs(height - reference_z)
                if candidate_score < score[j, i]:
                    score[j, i] = candidate_score
                    ground_z[j, i] = height
                    ground_triangle[j, i] = triangle_index

    support = np.isfinite(ground_z)
    occupied = np.zeros((ny, nx), np.bool_)
    half_xy = resolution / 2
    for triangle_index in range(len(triangles)):
        triangle = triangles[triangle_index]
        lower_x, upper_x = triangle[:, 0].min(), triangle[:, 0].max()
        lower_y, upper_y = triangle[:, 1].min(), triangle[:, 1].max()
        lower_z, upper_z = triangle[:, 2].min(), triangle[:, 2].max()
        i0 = max(0, int(np.floor((lower_x - xmin) / resolution)) - 1)
        i1 = min(nx - 1, int(np.floor((upper_x - xmin) / resolution)))
        j0 = max(0, int(np.floor((lower_y - ymin) / resolution)) - 1)
        j1 = min(ny - 1, int(np.floor((upper_y - ymin) / resolution)))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if not support[j, i] or occupied[j, i]:
                    continue
                local_low = ground_z[j, i] + obstacle_low
                local_high = ground_z[j, i] + obstacle_high
                if upper_z < local_low or lower_z > local_high:
                    continue
                if triangle_index == ground_triangle[j, i]:
                    continue
                x = xmin + (i + .5) * resolution
                y = ymin + (j + .5) * resolution
                if support_candidate[triangle_index]:
                    inside, height = _height_at_xy(triangle, x, y)
                    if inside and abs(height - ground_z[j, i]) <= max(.025, obstacle_low):
                        continue
                center = np.array([x, y, (local_low + local_high) / 2])
                half = np.array([half_xy, half_xy, (local_high - local_low) / 2])
                occupied[j, i] = triangle_box(triangle, center, half)
    return support, occupied, ground_z


def _triangles(usd_path):
    from pxr import Usd
    from tools.topology_authoring.surfaces import SupportSurface
    surface = SupportSurface.from_stage(Usd.Stage.Open(str(usd_path)))
    return surface.points[surface.triangles]


def semantic_rows(document):
    rows = []
    confirmed_ids = {'entity:l1:stair-switchback', 'entity:l1:under-stair-seating',
                     'entity:l2:bar-table', 'entity:roof:panels', 'entity:roof:stair-continuity-unverified'}
    for number, ent in enumerate(document['semantics']['entities'], 1):
        rows.append(dict(ent, marker=f'S{number}', visual_confirmed=ent['id'] in confirmed_ids,
                         position_status='authored_approximate_not_visually_registered'))
        if ent['id']=='entity:roof:panels':
            rows[-1]['label'] = '屋面三组架空板（用途未定；洞口解释待复核）'
            rows[-1]['original_label'] = ent['label']
            rows[-1]['review_note'] = 'Flash saw raised boards in capture-01/roof.png; current textured capture-02/roof.png and geometry also show raised arrays. Do not infer holes from white cutaway alone.'
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('product', type=Path)
    parser.add_argument('output_root', type=Path)
    parser.add_argument('--usd', type=Path, default=Path(__file__).resolve().parents[3]/'usd/scene/Living and public space/office/office.usda')
    parser.add_argument('--resolution', type=float, default=.03)
    parser.add_argument('--min-height', type=float, default=.03)
    parser.add_argument('--max-height', type=float, default=.65)
    parser.add_argument('--annotations',type=Path,help='Editable semantic-annotations.json from a previous export')
    args = parser.parse_args()
    if not (np.isfinite([args.resolution,args.min_height,args.max_height]).all()
            and args.resolution > 0 and 0 < args.min_height < args.max_height):
        parser.error('require finite resolution > 0 and 0 < min-height < max-height')
    args.output_root.mkdir(parents=True, exist_ok=False)
    d = json.loads(args.product.read_text())
    from pxr import Usd
    stage=Usd.Stage.Open(str(args.usd))
    layer_hashes={str(Path(layer.realPath)):hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()
                  for layer in stage.GetUsedLayers() if layer.realPath}
    for index,path in enumerate(sorted(layer_hashes)):
        key=f'usd_layer_{index:04d}'
        if d.get('source',{}).get(key)!=layer_hashes[path]:
            raise ValueError(f'product source digest mismatch: {key} {path}')
    tri = _triangles(args.usd)
    plt.rcParams['font.family'] = ['Noto Sans CJK JP','DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    xmin,ymin = np.floor(tri[:,:,:2].min((0,1))/.1)*.1-.1
    xmax,ymax = np.ceil(tri[:,:,:2].max((0,1))/.1)*.1+.1
    nx,ny = np.ceil([(xmax-xmin)/args.resolution,(ymax-ymin)/args.resolution]).astype(int)
    xmax,ymax = xmin+nx*args.resolution,ymin+ny*args.resolution
    xx,yy = xmin+(np.arange(nx)+.5)*args.resolution,ymin+(np.arange(ny)+.5)*args.resolution
    semantics = json.loads(args.annotations.read_text()) if args.annotations else semantic_rows(d)
    nodes = {n['id']:n for n in d['nodes']}
    manifest = dict(source_usd=str(args.usd.resolve()),source_sha256=hashlib.sha256(args.usd.read_bytes()).hexdigest(),
                    product=str(args.product.resolve()),product_sha256=hashlib.sha256(args.product.read_bytes()).hexdigest(),
                    resolution_m=args.resolution,origin_xy=[xmin,ymin],
                    collision_height_above_floor_m=[args.min_height,args.max_height],
                    method='triangle_cell_prism_SAT_no_large_face_filter',source_layer_sha256=layer_hashes,
                    generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    support='cell center, near-horizontal surface within 0.015 m of declared floor',
                    limitations=['no robot footprint or swept clearance certification',
                                 'triangle surfaces, not watertight solid-volume reconstruction',
                                 'semantic positions are approximate, not Flash-registered'],levels={})
    grids = {}
    for level in d['levels']:
        lid,z = level['id'],level['support_z_m']
        support,occupied = rasterize(tri,xmin,ymin,nx,ny,args.resolution,z,z+args.min_height,z+args.max_height)
        grids[lid] = (support,occupied)
        state = np.full(support.shape,-1,np.int8)
        state[support] = 0
        state[occupied] = 1
        np.savez_compressed(args.output_root/f'{lid}-occupancy.npz',x=xx,y=yy,support=support,
                            occupied=occupied,state=state,origin_xy=[xmin,ymin],resolution_m=args.resolution,
                            height_band_z=[z+args.min_height,z+args.max_height])
        fig,(ax,info) = plt.subplots(1,2,figsize=(13,9),gridspec_kw={'width_ratios':[3,2]})
        ax.imshow(state,origin='lower',extent=[xmin,xmax,ymin,ymax],interpolation='nearest',
                  cmap=ListedColormap(['#bac4cf','#fafafa','#303740']),vmin=-1,vmax=1)
        for route in d['semantics']['routes']:
            pts = [nodes[n] for n in route['node_ids']]
            if not any(n.get('level_id')==lid for n in pts):
                continue
            xy = np.array([n['position_xyz'][:2] for n in pts])
            ax.plot(*xy.T,'--',color='#d62f39' if route.get('kind')=='stairs' else '#15803d',lw=1.7,marker='.',ms=3)
            if route.get('kind')!='stairs':
                for a,b in zip(xy,xy[1:]):
                    samples=np.linspace(a,b,max(2,int(np.linalg.norm(b-a)/(args.resolution/2))+1))
                    for point in samples:
                        i,j=np.floor((point-np.array([xmin,ymin]))/args.resolution).astype(int)
                        if 0<=j<ny and 0<=i<nx and occupied[j,i]:
                            ax.plot(*point,'x',color='#b52a91',ms=5,zorder=6)
        lines = ['语义观察与定位分别记录','绿色：Flash 确认物体/结构存在','橙色：身份或层属仍待核对',
                 '所有标记坐标均为原草稿近似位置。','编号不是精确物体边界；抓取位置未设定。','']
        for ent in semantics:
            if ent.get('level_id') != lid:
                continue
            x,y,_ = ent['position_xyz']
            color = '#00874f' if ent['visual_confirmed'] else '#bc6b08'
            ax.scatter(x,y,s=110,facecolors='none',edgecolors=color,linewidths=1.7,zorder=5)
            ax.annotate(ent['marker'],(x,y),xytext=(5,5),textcoords='offset points',color=color,fontsize=9,weight='bold')
            label=f"{ent['marker']}  {ent['label']}"
            lines.extend(label[i:i+22] for i in range(0,len(label),22))
            lines.extend(['  '+('视觉已确认；位置待配准' if ent['visual_confirmed'] else '待核对；位置待配准'),''])
        info.axis('off');info.text(0,.98,'\n'.join(lines),va='top',fontsize=10,linespacing=1.6)
        ax.set(xlabel='世界坐标 X (m)',ylabel='世界坐标 Y (m)',aspect='equal');ax.grid(alpha=.12)
        ax.legend(handles=[Patch(color='#303740',label='该高度带内的碰撞表面'),
                           Patch(color='#fafafa',ec='#999999',label='有支撑、该高度带未命中障碍'),
                           Patch(color='#bac4cf',label='未知 / 无本层支撑'),
                           Line2D([0],[0],color='#15803d',ls='--',label='平层路线草稿'),
                           Line2D([0],[0],color='#d62f39',ls='--',label='跨层楼梯路线草稿'),
                           Line2D([0],[0],color='#b52a91',marker='x',ls='',label='原平层路线与几何交叠点')],
                  loc='lower left',bbox_to_anchor=(1.15,0),fontsize=9)
        fig.suptitle(f'Office {lid} · 分层几何障碍与语义草稿',fontsize=17)
        fig.text(.5,.91,f'地面 z={z:.3f} m · 提取地面上 {args.min_height:.2f}–{args.max_height:.2f} m · 栅格 {args.resolution:.2f} m',ha='center')
        fig.text(.5,.015,'虚线路线未认证可行；灰区不自动视为可走。实际机器人需另行设置尺寸、净空与楼梯能力。',ha='center',fontsize=10)
        fig.subplots_adjust(top=.86,bottom=.10,wspace=.18)
        fig.savefig(args.output_root/f'{lid}-navigation.png',dpi=170);plt.close(fig)
        manifest['levels'][lid] = dict(floor_z_m=z,support_cells=int(support.sum()),occupied_cells=int(occupied.sum()),
                                      free_cells=int((state==0).sum()),unknown_cells=int((state==-1).sum()))
        print(lid,manifest['levels'][lid],flush=True)
    checks = []
    for route in d['semantics']['routes']:
        row = dict(id=route['id'],status='authored_draft_not_robot_validated')
        pts = [nodes[n] for n in route['node_ids']]
        if route.get('kind')=='stairs':
            row['check'] = 'stairs_require_3d_clearance_not_flat_floor_grid'
        else:
            hits = unsupported = samples = 0
            for a,b in zip(pts,pts[1:]):
                s,o = grids[a['level_id']]
                a,b = np.array(a['position_xyz']),np.array(b['position_xyz'])
                for p in np.linspace(a,b,max(2,int(np.linalg.norm(b-a)/(args.resolution/2))+1)):
                    i,j = int(np.floor((p[0]-xmin)/args.resolution)),int(np.floor((p[1]-ymin)/args.resolution))
                    samples += 1
                    if 0<=j<ny and 0<=i<nx:
                        hits += int(o[j,i]);unsupported += int(not s[j,i])
                    else:
                        unsupported += 1
            row.update(samples=samples,collision_samples=hits,unsupported_samples=unsupported,check='centerline_only_no_footprint')
        checks.append(row)
    manifest['route_checks'] = checks
    fig = plt.figure(figsize=(12,9));ax = fig.add_subplot(111,projection='3d')
    for e in d['edges']:
        xyz = np.array([nodes[e['from']]['position_xyz'],nodes[e['to']]['position_xyz']])
        ax.plot(*xyz.T,'--',color='#d62f39' if e.get('traversal_mode')=='stairs' else '#15803d')
    for n in nodes.values():
        ax.scatter(*n['position_xyz'],c='#316fa9',s=18)
    for ent in semantics:
        ax.scatter(*ent['position_xyz'],c='#00874f' if ent['visual_confirmed'] else '#bc6b08',s=40,marker='*')
        ax.text(*ent['position_xyz'],ent['marker'],fontsize=9)
    ax.set(title='Office 三维拓扑草稿 · 红：楼梯 / 绿：平层\n屋面节点保留孤立，未确认连接不补画',xlabel='X (m)',ylabel='Y (m)',zlabel='Z (m)')
    fig.text(.5,.025,'所有边均为编制草稿，未认证机器人可通行；S 编号对应各层语义图。',ha='center')
    fig.savefig(args.output_root/'office-topology-3d.png',dpi=170);plt.close(fig)
    (args.output_root/'semantic-annotations.json').write_text(json.dumps(semantics,ensure_ascii=False,indent=2)+'\n')
    (args.output_root/'navigation-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
