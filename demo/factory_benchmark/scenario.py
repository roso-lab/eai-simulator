"""Task design owned by this demo; capacities require a supplied policy."""

ASSET_KEYS = ('primitive_box',
 'safety_beam',
 'goal_marker',
 'cardbox_d',
 'cardbox_c',
 'cardbox_b',
 'cardbox_a',
 'wooden_crate_a',
 'pallet_b1',
 'nvd_heavydutytrafficcone_a01',
 'nvd_popupcone_a01',
 'nvd_trafficcone_a01',
 'nvd_pallet_asm')

EXPERIMENT_NOTES = {'primary_assets': ('cardbox_d', 'cardbox_c', 'cardbox_a', 'wooden_crate_a'),
 'secondary_assets': ('primitive_box', 'safety_beam', 'nvd_heavydutytrafficcone_a01', 'pallet_b1'),
 'factors': ('cargo_mass', 'clearance', 'route_length', 'obstacle_count')}

TASK_BLUEPRINT = (('aerial_scan', 'cf2x', '建立封锁区/货物可见性信息'),
 ('navigate', 'm20', '按扫描结果到达起始侧'),
 ('navigate', 'carter', '按扫描结果到达另一侧'),
 ('joint_transport', 'm20+carter', '共同抬运超单机容量的重件到中转区'),
 ('transport_object', 'm20', '将另一件轻量验证物体移到装配位'),
 ('transport_object', 'carter', '从中转位接续轻量物体到交付位'),
 ('navigate', 'm20', '返回并确认通道状态'),
 ('transport_object', 'carter', '完成轻量物体末端放置和状态复核'))

TASK_PROFILE = {'minimum_nodes': 8,
 'minimum_substantive_nodes': 5,
 'minimum_dependency_depth': 5,
 'required_skills': {'joint_transport', 'aerial_scan'},
 'description': 'aerial inventory -> two ground approaches -> heavy joint transfer -> inspection'}

