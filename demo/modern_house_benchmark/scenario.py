"""Task design owned by this demo; capacities require a supplied policy."""

ASSET_KEYS = ('primitive_box',
 'safety_beam',
 'goal_marker',
 'mh_bookstack',
 'mh_bowl_brass',
 'mh_armchair',
 'mh_rocker',
 'nvd_chair_wire',
 'nvd_avocado01',
 'nvd_lemon_01',
 'nvd_orange_01')

EXPERIMENT_NOTES = {'primary_assets': ('mh_bookstack', 'mh_rocker', 'mh_armchair'),
 'secondary_assets': ('mh_bowl_brass',
                      'nvd_avocado01',
                      'nvd_lemon_01',
                      'nvd_orange_01',
                      'safety_beam'),
 'factors': ('cargo_mass', 'clearance', 'route_length', 'obstacle_count')}

TASK_BLUEPRINT = (('aerial_scan', 'iris', '侦察房间入口和家具周边通道'),
 ('navigate', 'm20', '到达家具主作业侧'),
 ('navigate', 'carter', '验证门口辅助作业侧'),
 ('joint_transport', 'm20+carter', '共同移动家具代理到室内停放区'),
 ('aerial_scan', 'iris', '复核家具移动后的通道净空'),
 ('transport_object', 'm20', '将小件家居代理移到房间暂存区'),
 ('navigate', 'carter', '验证最终房间入口'),
 ('aerial_scan', 'iris', '复核房间状态和物体位置'),
 ('wait', 'carter', '保持放置确认窗口'),
 ('navigate', 'carter', '复核机器人退出生活动线，到独立待机区'),
 ('navigate', 'm20', '作业机器人退出生活动线，到另一独立待机区'))

TASK_PROFILE = {'minimum_nodes': 8,
 'minimum_substantive_nodes': 5,
 'minimum_dependency_depth': 5,
 'required_skills': {'joint_transport', 'aerial_scan'},
 'description': 'room reconnaissance -> access verification -> joint furniture/object move -> room '
                'inspection'}
