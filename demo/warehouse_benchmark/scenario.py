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

EXPERIMENT_NOTES = {'primary_assets': ('cardbox_c', 'cardbox_a', 'wooden_crate_a'),
 'secondary_assets': ('pallet_b1', 'nvd_pallet_asm', 'safety_beam', 'nvd_trafficcone_a01'),
 'factors': ('cargo_mass', 'clearance', 'route_length', 'obstacle_count')}

TASK_BLUEPRINT = (('aerial_scan', 'iris', '扫描货架通道并发布入口状态'),
 ('navigate', 'm20', '进入货架左侧作业位'),
 ('navigate', 'carter', '进入货架右侧作业位'),
 ('joint_transport', 'm20+carter', '共同转移大件箱体代理到集货区'),
 ('transport_object', 'm20', '将优先纸箱代理送入集货区'),
 ('navigate', 'carter', '核对出库 dock 通道'),
 ('transport_object', 'm20', '将优先纸箱代理从集货区送到出库 dock'),
 ('aerial_scan', 'iris', '复核出库区库存与通道'),
 ('wait', 'carter', '保持出库确认窗口'),
 ('navigate', 'm20', '释放货架通道并回到扫描区'))

TASK_PROFILE = {'minimum_nodes': 8,
 'minimum_substantive_nodes': 5,
 'minimum_dependency_depth': 5,
 'required_skills': {'joint_transport', 'aerial_scan'},
 'description': 'rack scan -> coordinated aisle access -> heavy crate joint transport -> dock '
                'verification'}
