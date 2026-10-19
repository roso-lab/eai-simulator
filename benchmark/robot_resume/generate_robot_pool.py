#!/usr/bin/env python3
"""Generate the review-only robot pool from the local catalog; never spawn robots.

Run: PYTHONPATH=.:source/EAI python3 benchmark/robot_resume/generate_robot_pool.py
The fixed record contract is shared by profiles and fully expanded variants.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source/EAI"))
sys.path.insert(0, str(ROOT))
from EAI.hmrs_env.env_diy import catalog as C
from tools.scenario_generation.robot_dossier import ROBOT_MORPHOLOGY

OUT = Path(__file__).resolve().parent
LIMIT_FILE = OUT / 'manipulator_capabilities.json'
LIMITS = json.loads(LIMIT_FILE.read_text())["robots"]
BUILDER = ROOT / 'source/EAI_hmrs/EAI_hmrs/env_builder.py'
tree = ast.parse(BUILDER.read_text())
IMPORTS = next(ast.literal_eval(n.value) for n in tree.body
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'CONTROLLER_CFG_IMPORTS' for t in n.targets))
PORTABLE_IMPORTS = {
    key.value
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == 'PORTABLE_CONTROLLER_CFGS'
            for target in node.targets)
    and isinstance(node.value, ast.Dict)
    for key in node.value.keys
    if isinstance(key, ast.Constant) and isinstance(key.value, str)
}
REGISTERED_CONTROLLERS = set(IMPORTS) | PORTABLE_IMPORTS

# Project pool policy: UR5 is standalone with a fixed Robotiq gripper.
# The low-level engineering catalog can retain historical asset variants.
POOL_MANIPULATORS = ('z1',)
LEGACY = {'G1_BOX_GRASP_CFG', 'G1_BOX_TRANSPORT_CFG', 'PEPPER_BOX_GRASP_CFG'}
GO2_STABLE_CFG = 'GO2_MIXED_STAIRS_CFG'
GO2_OFFROAD_CFG = 'GO2_EXTREME_PARKOUR_TEACHER_CFG'
BUILTIN_CAMERA_MODELS = {
    'simple_car': 'builtin_dual_camera_suite',
    'cf2x': 'builtin_monocular_camera',
    'iris': 'builtin_monocular_camera',
    'pegasus': 'builtin_monocular_camera',
}
DEFAULT_D455_ROBOTS = {
    'carter', 'pepper', 'go2', 'b2', 'm20', 'scout', 'mushr_v2', 'coco', 'lite3',
}
LABELS = {
    'carter': 'Carter 差速轮式机器人', 'simple_car': 'SimpleCar 平面移动机器人',
    'pepper': 'Pepper 全向底盘双臂机器人', 'go2': 'Go2 四足机器人',
    'b2': 'B2 四足机器人', 'm20': 'M20 轮腿机器人', 'scout': 'Scout 差速轮式机器人',
    'mushr_v2': 'MuSHR Nano v2 车式转向机器人', 'g1': 'G1 人形机器人',
    'cf2x': 'CF2X 四旋翼机器人', 'iris': 'Iris 四旋翼机器人',
    'pegasus': 'Pegasus X4 四旋翼机器人', 'lite3': 'Lite3 四足机器人',
    'coco': 'Coco 车式转向机器人', 'ur5': '独立 UR5 机械臂机器人',
    'z1': '独立 Z1 机械臂候选机器人',
}
ATTACH_LABELS = {
    'orsus': 'Orsus 传感器载荷', 'realsense_d455': 'RealSense D455 相机',
    'lidar': '独立 LiDAR', 'octagon_aio_c01': 'OCTAGON-AIO-C01 传感器载荷',
    'mid360': '内建雷达选为 Mid-360', 'ur5': 'UR5 + Robotiq 2F-85',
    'z1': 'Z1 + Z1 夹具',
}
STATUS_ZH = {
    'catalog_registered': '目录已注册，实例尚未绑定',
    'registered_blocked': '已注册，但运行时资格被阻止',
    'conditional': '附条件：仅有静止挂载证据',
    'rejected': '当前挂载筛查拒绝',
    'declared_unwired': '旧目录声明，当前构建器未接线',
    'pending_integration': '独立/专用配置待接入本次机器人池',
}
SUPPORT_ZH = {'supported': '支持', 'unsupported': '不支持',
              'unknown': '未确认', 'not_applicable': '不适用'}
AVAIL_ZH = {'pending_binding': '待实例绑定', 'pending_configuration_validation': '待当前组合验证',
            'pending_integration': '待接入', 'deployment_blocked': '部署被阻止',
            'not_supported': '不支持', 'provider_pending': '公共提供器尚未实现'}
FAMILY_ZH = {'wheeled': '轮式', 'quadruped': '四足', 'wheel_legged': '轮腿',
             'biped': '双足', 'aerial': '空中', 'manipulator': '机械臂'}
MODE_ZH = {'differential': '差速驱动', 'planar_root_velocity': '平面根速度控制',
           'virtual_holonomic_joints': '虚拟关节全向驱动', 'ackermann': '车式转向',
           'legged': '足式运动', 'legged_plus_wheels': '足式与轮式复合运动',
           'multirotor_thrust_torque': '多旋翼推力/力矩控制',
           'multirotor_rotor': '多旋翼飞行', 'fixed_base': '固定基座'}
ORIGIN_ZH = {'none': '无', 'attached': '外挂', 'integrated': '原生集成', 'primary_robot': '独立机械臂机器人'}
STAIR_BASIS_ZH = {'fixed_base_or_aerial': '固定基座或空中配置，不使用地面楼梯能力',
                  'user_confirmed_current_project': '用户明确的当前项目限制',
                  'user_confirmed_stair_capable_controller': '用户确认当前稳定 Go2 控制器具备上楼能力；下楼尚未确认',
                  'experimental_policy_not_route_qualified': '实验性越野策略已接线，但运动不稳定且没有当前路线专项资格',
                  'historical_partial_not_qualification': '历史楼梯结果只部分成功，不构成可靠楼梯资格',
                  'draft_policy_for_current_plain_wheeled_configuration': '本稿对当前普通轮式配置采用的不支持策略',
                  'loaded_configuration_requires_validation': '携臂后的完整配置尚待验证',
                  'no_route_specific_validation': '缺少当前路线和控制器的专项验证'}
BASE_NOTES = {
    'carter': [],
    'simple_car': ['平面根速度控制；当前 ROS cmd_vel 桥接不保留横向速度。'],
    'pepper': ['原生双臂，不套用 UR5/Z1 的载荷或高度数值。',
               '全向底盘通过虚拟关节目标实现；底盘固定根与移动底盘坐标不能混用。'],
    'go2': ['楼梯资格必须区分稳定版/越野版控制器、方向、阶梯几何与负载。'],
    'b2': ['B2 使用自身资产和控制器，不继承 Go2 的参数或资格。'],
    'm20': ['轮腿形态不自动代表具备当前路线的上下楼能力。'],
    'scout': [],
    'mushr_v2': ['车式转向，不按全向横移或原地旋转机器人分配动作。'],
    'g1': ['项目默认具备抓取能力；DWAQ 行走策略控制 29 个身体关节并将 24 个 Inspire 手指保持为零，抓放需要切换到已标定抓放运行模式。'],
    'cf2x': ['当前 DronePickPlaceAdapter 仅支持 PegasusIris/PegasusX4，不支持 CF2X。'],
    'iris': ['飞行能力与地面楼梯通行无关；飞行净空、负载和姿态约束需单独确认。'],
    'pegasus': ['飞行能力与地面楼梯通行无关；飞行净空、负载和姿态约束需单独确认。'],
    'lite3': ['特殊限制：当前项目中的 Lite3 不能上楼，也不能下楼。'],
    'coco': ['车式转向，不按全向横移或原地旋转机器人分配动作。'],
    'ur5': ['固定安装的独立机器人，不依赖移动底盘，不具备导航或自行移动能力。'],
    'z1': ['存在独立 Z1 资产；固定基座和控制接线尚需显式配置，不能从资产存在推断可部署。'],
}


def skill(skill_id, support, availability, backend, operations, requirements, limitations,
          support_basis='source_analysis'):
    return dict(skill_id=skill_id, support=support, availability=availability,
                support_basis=support_basis,
                backend=backend, operations=operations,
                requirements_zh=requirements, limitations_zh=limitations)


def stairs(host, attached, fixed, controller):
    if fixed or host in {'cf2x', 'iris', 'pegasus'}:
        return dict(ascent='not_applicable', descent='not_applicable',
                    basis='fixed_base_or_aerial', display='when_relevant')
    if host == 'lite3':
        return dict(ascent='unsupported', descent='unsupported',
                    basis='user_confirmed_current_project', display='always')
    if host in {'carter', 'simple_car', 'pepper', 'scout', 'mushr_v2', 'coco'}:
        return dict(ascent='unsupported', descent='unsupported',
                    basis='draft_policy_for_current_plain_wheeled_configuration',
                    display='when_relevant')
    if host == 'go2' and not attached and controller == GO2_STABLE_CFG:
        return dict(ascent='supported', descent='unknown',
                    basis='user_confirmed_stair_capable_controller', display='always')
    if host == 'go2' and controller == GO2_OFFROAD_CFG:
        return dict(ascent='unknown', descent='unknown',
                    basis='experimental_policy_not_route_qualified', display='always')
    if host == 'g1' and controller == 'G1_DWAQ_CFG':
        return dict(ascent='unknown', descent='unknown',
                    basis='historical_partial_not_qualification', display='always')
    return dict(ascent='unknown', descent='unknown',
                basis='loaded_configuration_requires_validation' if attached else 'no_route_specific_validation',
                display='always' if attached else 'when_relevant')


def make_record(host, arm, controller, *, role='mobile_platform', attachments=None):
    if arm == 'ur5_bare' or (arm == 'ur5' and role != 'standalone_arm'):
        raise ValueError('Project pool excludes bare UR5 and all mounted UR5 combinations')
    attachments = list(attachments if attachments is not None else ([arm] if arm else []))
    if any(a in C.tool_catalog() for a in attachments):
        raise ValueError('HeteroCoord experiment resumes do not enumerate authoring tools')
    standalone = role == 'standalone_arm'
    fixed_g1 = role == 'fixed_g1_calibrated'
    fixed = standalone or fixed_g1
    attached = bool(arm) and not standalone
    model = arm
    native = host in {'pepper', 'g1'} and not arm
    origin = 'primary_robot' if standalone else 'attached' if attached else 'integrated' if native else 'none'
    gripper = ('robotiq_2f85' if arm == 'ur5' else 'z1_gripper' if arm == 'z1'
               else 'inspire_right_hand' if fixed_g1
               else 'native_hands' if native else None)
    manip_ctrl = ('UR5_IK_CFG' if model == 'ur5' else 'Z1_IK_CFG' if model == 'z1'
                  else 'G1LiveRuntime' if fixed_g1 else None)
    tag = 'standalone_fixed_' + arm if standalone else 'fixed_inspire_calibrated' if fixed_g1 else arm or 'no_external_arm'
    profile_id = '.'.join([host, tag, controller or 'host_runtime'])
    notes = list(BASE_NOTES[host])
    error = None
    if host in C.robot_keys() and not fixed_g1:
        try:
            C.validate_attachment_types(host, attachments)
            accepted = True
        except ValueError as exc:
            accepted, error = False, str(exc)
    else:
        accepted = None
    status = 'catalog_registered'
    reason = '目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。'
    scope = '源码接线与声明；本简历未新增物理验证。'
    if attached:
        status, reason = C.manipulator_deployment_status(host, arm)
        scope = '当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。'
        notes += ['外挂机械臂属于本组合机器人，不另占一个机器人池成员。',
                  '移动、持物行走及上下楼均须按携臂后的完整配置重新验证。']
    if controller in LEGACY:
        status = 'declared_unwired'
        reason = '目录保留该旧控制器名称，但当前 CONTROLLER_CFG_IMPORTS 无此项，不能由现有构建器加载。'
    if standalone or fixed_g1:
        status = 'pending_integration'
        reason = ('用户明确要求独立 UR5；存在独立资产与控制/验证工具，尚非 Env DIY 的顶层 robot key。'
                  if standalone and host == 'ur5' else
                  '存在独立 Z1 资产，但尚非 Env DIY 顶层 robot key；固定基座安装与后端待确认。'
                  if standalone else
                  '公共 G1 抓放后端要求专用 fixed-base Inspire 标定资产，不等同于默认 G1 行走配置。')
        scope = '专用资产/后端存在；不代表已接入任意场景的机器人池。'
    if controller and 'MIXED_STAIRS' in controller:
        notes.append('存在 stairs 控制器只说明控制接线，不代表当前负载具备已验证的上下楼资格。')
    controller_variant = None
    controller_maturity = 'standard'
    known_instability = None
    fall_recovery = 'unknown'
    runtime_blockers = []
    if controller == GO2_STABLE_CFG:
        controller_variant = 'normal_stable'
        controller_maturity = 'stable'
        notes.append('Go2 正常稳定版本；使用原有 GO2_MIXED_STAIRS_CFG。')
    elif controller == GO2_OFFROAD_CFG:
        controller_variant = 'experimental_offroad'
        controller_maturity = 'experimental_unstable'
        known_instability = '用户已明确该越野版本运动不稳定；任务分配必须保留该限制。'
        fall_recovery = 'unsupported'
        notes += ['实验性越野版本；运动不稳定，不能作为稳定版的透明替代。',
                  '753 维 privileged Teacher 依赖 132 射线真实场景高度扫描和两帧足端接触历史。',
                  '不包含站起或跌倒恢复控制器，也不是视觉 Student 或全局路径规划器。']
    elif controller == 'G1_DWAQ_CFG':
        controller_variant = 'dwaq_locomotion'
        controller_maturity = 'deployment_baseline_limited'
        fall_recovery = 'unsupported'
        status = 'registered_blocked'
        reason = ('控制器和匹配资产已经注册；纯 G1 环境使用合同要求的 0.001 s × 20。'
                  'G1 与其他机器人混合时缺少多速率方案或共同物理时序重验，构建器会明确拒绝。')
        scope = '模型/适配器接线与纯 G1 配置时序已接入；混合机器人部署资格仍被阻止。'
        runtime_blockers += [
            '纯 G1 使用 sim.dt=0.001、decimation=20；与任何非 G1 控制器的混合环境当前拒绝构建。',
            'DWAQ 行走与固定基座 G1PickPlaceAdapter 的策略切换和关节写入仲裁尚未形成统一运行模式。',
        ]
        notes += ['DWAQ 历史 School entrance 长楼梯上行仅 19/32 成功，不声明可靠楼梯能力。',
                  '不包含跌倒恢复；切换策略或 episode 重置时必须清空五帧历史。']
    if controller == 'MUSHR_RWD_ACKERMANN_CFG':
        notes.append('RWD 控制器选择不同底盘资产变体；不与默认 MuSHR 资产配置混为一个简历身份。')
    if controller == 'G1_BOX_GRASP_CFG':
        notes.append('旧双臂抱箱自动序列，输入为 placeholder；不是当前公共固定基座抓放适配器。')
    if controller == 'G1_BOX_TRANSPORT_CFG':
        notes.append('旧行走+抱箱+自动路线组合；不能将其能力并入新的固定基座 G1 抓放简历。')
    if controller == 'PEPPER_BOX_GRASP_CFG':
        notes.append('旧固定基座双臂抱箱自动序列，不是当前 PEPPER_PICK_PLACE_CFG 接线。')
    if fixed_g1:
        notes += ['固定基座；仅标定的右侧 Inspire 手与对象绑定；此配置无行走能力。',
                  '仅针对已标定对象和直立轴向抓放，不支持任意物体姿态。']

    morphology, mode, domain = ('manipulator', 'fixed_base', 'stationary') if standalone else ROBOT_MORPHOLOGY[host]
    if fixed_g1:
        mode, domain = 'fixed_base', 'stationary'
    navigation_support = 'unsupported' if fixed else 'unknown' if controller in LEGACY else 'supported'
    navigation_availability = ('not_supported' if fixed else 'deployment_blocked' if status in {'rejected', 'declared_unwired', 'registered_blocked'}
                               else 'pending_configuration_validation' if attached else 'pending_binding')
    nav_req = ['实例导航适配器', '该机器人可达的拓扑地图和目标节点', '按当前配置过滤路径、净空和楼梯边']
    if attached:
        nav_req.append('携臂整机的移动稳定性与当前运动控制器验证')
    nav_lim = ['公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。']
    if fixed:
        nav_lim = ['固定/独立机械臂配置不提供移动导航。']
    elif host in {'iris', 'pegasus'} and controller and 'ROTOR' in controller:
        nav_lim.append('旋翼控制命令不能直接接收 XYZ 目标；需要由位置目标转换到旋翼命令的适配器。')
        navigation_support = 'unknown'
        navigation_availability = 'pending_configuration_validation'

    pp_support, backend, operations = 'unsupported', None, []
    pp_basis = 'source_analysis'
    pp_req, pp_lim = [], []
    execution = 'none'
    if arm:
        pp_support, backend, operations = 'supported', 'ArmPickPlaceAdapter', ['pick', 'place']
        pp_req = ['实例绑定到机械臂运行时', '有效夹具和安装位姿', '对象与目标可达且恢复轨迹可达',
                  '应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径']
        pp_lim = ['局部静止操作；不支持任意对象姿态。',
                  '理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。']
        execution = 'idealized_arm_attach_release'
    elif fixed_g1:
        pp_support, backend, operations = 'supported', 'G1PickPlaceAdapter', ['pick', 'place']
        pp_req = ['固定基座 Inspire 标定资产', '右手及对象的标定与 ObjectBinding', '实例 G1LiveRuntime 与执行器注册']
        pp_lim = ['限标定对象与直立轴向姿态。', '此配置无行走能力；不套用 UR5/Z1 限制。']
        execution = 'calibrated_fixed_g1_binding'
    elif host == 'pepper' and controller == 'PEPPER_PICK_PLACE_CFG':
        pp_support, backend, operations = 'supported', 'PepperPickPlaceAdapter', ['pick', 'place']
        pp_req = ['复合控制器与实例适配器注册', '双臂链可用', '物理初始化前设置对象碰撞过滤', '适配器当前要求单环境视图']
        pp_lim = ['双臂局部 IK 抓放与理想化对象跟随。', '不控制任意对象姿态；不套用 UR5/Z1 限制。']
        execution = 'bilateral_ik_idealized_object_follow'
    elif host in {'iris', 'pegasus'} and controller and 'POSITION' in controller:
        pp_support, backend, operations = 'supported', 'DronePickPlaceAdapter', ['pick', 'place']
        pp_req = ['PegasusIris/PegasusX4 的 goal_position 接线', 'Owned FixedJoint 运输桥与实例适配器注册',
                  '对象/飞行锚点、净空与飞行负载验证']
        pp_lim = ['物理 FixedJoint 挂接/释放，不代表机械手抓取。', '不控制任意物体姿态；飞行负载上限未知。']
        execution = 'aerial_owned_fixed_joint'
    elif native:
        pp_support, pp_basis = 'supported', 'user_confirmed_project_capability'
        if host == 'g1':
            backend, operations = 'G1PickPlaceAdapter', ['pick', 'place']
            pp_req = ['同一 G1 实例切换到经标定的抓放模式', 'Inspire 手、对象绑定与 G1LiveRuntime 接线']
            pp_lim = ['行走与固定抓放当前使用不同的资产/运行方式，合并为同一机器人身份不等于可同时执行。',
                      '抓放模式限已标定对象与直立轴向姿态；持物行走和边走边操作仍待验证。']
            execution = 'calibrated_grasp_mode_pending_integration'
        else:
            operations = ['pick']
            pp_req = ['接入并验证与当前原生手臂、资产和运动控制器兼容的抓取后端']
            pp_lim = ['用户确认的默认抓取能力；当前配置尚未接通公共执行后端。',
                      '放置、持物行走和边走边操作仍需各自的后端与资格验证。']
            execution = 'project_declared_grasp_pending_backend'
    else:
        pp_lim = ['当前配置无受支持的公共抓放后端。']
    pp_availability = ('deployment_blocked' if status in {'rejected', 'declared_unwired', 'registered_blocked'}
                       else 'not_supported' if pp_support == 'unsupported'
                       else 'pending_integration' if status == 'pending_integration'
                       or pp_basis == 'user_confirmed_project_capability'
                       else 'pending_configuration_validation' if attached else 'pending_binding')
    if host == 'g1' and native and pp_support == 'supported':
        # The locomotion timing blocker does not erase the separately available
        # fixed-base grasp mode; unified mode arbitration is still pending.
        pp_availability = 'pending_integration'

    sensors = [a for a in attachments if a in C.attachment_catalog() and C.attachment_catalog()[a].category == 'sensor']
    tools = [a for a in attachments if a in C.tool_catalog()]
    builtin = (['builtin_camera_suite', 'builtin_lidar_suite'] if host in {'cf2x', 'iris', 'pegasus'}
               else ['builtin_dual_camera_suite', 'mid360' if 'mid360' in sensors else 'octagon_aio_c01']
               if host == 'simple_car' else [])
    camera_model = BUILTIN_CAMERA_MODELS.get(host) or ('realsense_d455' if host in DEFAULT_D455_ROBOTS else None)
    camera_status = ('integrated' if host in BUILTIN_CAMERA_MODELS else
                     'builder_registered_default' if host in DEFAULT_D455_ROBOTS else 'pending_adaptation')
    limit = LIMITS.get(model, {})
    skills = [
        skill('eai.navigation.node_route', navigation_support, navigation_availability,
              None if fixed else 'NavigationControllerAdapter', ['navigate_to_node'] if not fixed else [], nav_req, nav_lim),
        skill('eai.manipulation.pick_place', pp_support, pp_availability, backend, operations, pp_req, pp_lim,
              support_basis=pp_basis),
    ]
    if standalone:
        suffix = ATTACH_LABELS[arm]
    elif fixed_g1:
        suffix = '固定基座 Inspire 手标定抓放'
    elif arm:
        suffix = '携带 ' + ATTACH_LABELS[arm]
    else:
        suffix = '无外挂臂' + ('（保留原生双臂）' if native else '')
    if controller_variant == 'normal_stable':
        suffix += ' / 正常稳定版'
    elif controller_variant == 'experimental_offroad':
        suffix += ' / 实验性越野版（运动不稳定）'
    elif controller_variant == 'dwaq_locomotion':
        suffix += ' / DWAQ 行走版'
    controller_binding = ('host_specific_runtime' if fixed_g1 else 'builder_import_registered' if controller in REGISTERED_CONTROLLERS
                          else 'not_registered')
    selector = ('specialized_not_catalog_key' if fixed else 'per_robot_catalog' if controller in C.controller_cfgs_for_robot(host)
                else 'direct_json_builder')
    return {
        'resume_id': profile_id,
        'profile_id': profile_id,
        'name_zh': LABELS[host] + ' / ' + suffix,
        'record_type': 'configuration_profile',
        'identity': {'platform_type': host, 'host_platform': host if attached else None,
                     'configuration_role': role, 'robot_id': None, 'configuration_id': None, 'generation': None},
        'deployment': {'status': status, 'attachment_validation': accepted, 'catalog_error': error,
                       'controller_binding': controller_binding, 'selection_path': selector,
                       'controller_maturity': controller_maturity,
                       'known_instability_zh': known_instability,
                       'fall_recovery': fall_recovery,
                       'runtime_blockers_zh': runtime_blockers,
                       'reason_zh': reason, 'evidence_scope_zh': scope, 'runtime_qualified': False},
        'configuration': {'motion_controller': None if fixed else controller,
                          'controller_variant': controller_variant,
                          'manipulator_controller': manip_ctrl,
                          'external_manipulator': arm if attached else None,
                          'primary_manipulator': arm if standalone else None,
                          'attachments': [] if standalone else attachments, 'sensor_attachments': sensors,
                          'builtin_sensor_suite': builtin,
                          'default_sensor_suite': [camera_model] if camera_model else [],
                          'tools': tools, 'mount_pose': None,
                          'asset_variant_zh': 'MuSHR 后驱资产变体' if controller == 'MUSHR_RWD_ACKERMANN_CFG'
                          else 'G1 fixed-base Inspire 标定资产' if fixed_g1
                          else '独立机械臂资产；基座固定方式待实例确认' if standalone else '按当前平台与控制器构建'},
        'mobility': {'family': morphology, 'mode': mode, 'domain': domain,
                     'navigation_dimension': None if fixed else 'xyz' if domain == 'aerial' else 'xy',
                     'stationary_configuration': fixed, 'stairs': stairs(host, attached, fixed, controller),
                     'speed_limit_mps': None, 'min_turning_radius_m': None,
                     'holding_navigation': 'not_applicable' if fixed else 'unknown',
                     'simultaneous_locomotion_manipulation': 'not_applicable' if fixed or origin == 'none' else 'unknown'},
        'manipulation': {'origin': origin, 'arm_count': 1 if arm else 2 if native else 0,
                         'model': model if arm else host + '_native' if native else None,
                         'end_effector': gripper, 'execution_mode': execution,
                         'payload_kg': limit.get('payload_kg'), 'max_pickplace_z_m': limit.get('max_pickplace_z_m'),
                         'height_frame': limit.get('max_pickplace_z_frame') if arm else None,
                         'height_limit_point': None, 'payload_mass_basis': None,
                         'min_pickplace_z_m': None, 'radial_workspace_m': None,
                         'arbitrary_object_orientation': 'unsupported' if backend else 'unknown' if native else 'not_applicable',
                         'limits_source': 'user_manipulator_limits' if arm else None},
        'sensing': {'camera_required': True,
                    'camera_is_default_robot_property': True,
                    'camera_present': camera_model is not None,
                    'camera_presence_basis': ('source_integrated' if host in BUILTIN_CAMERA_MODELS
                                              else 'user_confirmed_default_with_builder_mount' if host in DEFAULT_D455_ROBOTS
                                              else 'required_pending_adaptation'),
                    'camera_model': camera_model, 'camera_mount_pose': None,
                    'camera_integration_status': camera_status,
                    'camera_source_meets_tool_gate': camera_model is not None,
                    'camera_publication': 'pending_binding',
                    'autonomous_perception_skill': 'not_declared',
                    'note_zh': 'HeteroCoord 实验默认配备相机，不作为可选工具展开。型号、安装位姿与图像流待实例绑定；默认配备声明不代表已有目录相机来源门禁通过，也不自动产生识别、定位或语义理解技能。'},
        'skills': skills,
        'runtime_state': {'membership': None, 'health': None, 'battery_fraction': None,
                          'current_node': None, 'held_object_ref': None, 'available_skill_ids': []},
        'special_notes_zh': notes,
        'source_refs': ['catalog', 'builder', 'morphology', 'skill_registry', 'arm_backend', 'pepper_backend', 'drone_backend', 'navigation_backend']
                       + (['user_manipulator_limits'] if arm else [])
                       + (['user_lite3_stairs'] if host == 'lite3' else [])
                       + ['user_pool_policy', 'user_experiment_policy']
                       + (['g1_fixed_backend', 'g1_pick_place_backend'] if host == 'g1' else [])
                       + (['g1_dwaq_integration', 'g1_dwaq_contract', 'g1_dwaq_policy',
                           'global_sim_timing']
                          if controller == 'G1_DWAQ_CFG' else [])
                       + (['go2_extreme_integration', 'go2_extreme_deployment',
                           'go2_extreme_provenance', 'go2_extreme_dynamic_policy']
                          if controller == GO2_OFFROAD_CFG else [])
                       + (['standalone_ur5_asset' if host == 'ur5' else 'standalone_z1_asset'] if standalone else []),
    }


def valid_sets(host):
    # Resume identities distinguish the body and its external arm only.
    # Camera is universal; sensor hardware details are instance configuration.
    choices = [a for a in POOL_MANIPULATORS if C.attachment_catalog()[a].supports(host)]
    result = []
    for count in range(len(choices) + 1):
        for values in itertools.combinations(choices, count):
            try:
                C.validate_attachment_types(host, values)
            except ValueError:
                continue
            result.append(list(values))
    return result


def controller_choices(host):
    if host == 'go2':
        return [GO2_STABLE_CFG, GO2_OFFROAD_CFG]
    return [C.default_controller_cfg(host)]


def closed_schema(sample):
    """All keys required, no additional properties; nullable types explicit below."""
    if isinstance(sample, dict):
        return {'type': 'object', 'additionalProperties': False,
                'required': list(sample), 'properties': {k: closed_schema(v) for k, v in sample.items()}}
    if isinstance(sample, list):
        return {'type': 'array', 'items': closed_schema(sample[0]) if sample else {'type': 'string'}}
    if sample is None:
        return {'type': ['string', 'null']}
    if isinstance(sample, bool):
        return {'type': 'boolean'}
    if isinstance(sample, (int, float)):
        return {'type': 'number'}
    return {'type': 'string'}


def record_schema(sample):
    schema = closed_schema(sample)
    def prop(path):
        cur = schema
        for token in path.split('.'):
            cur = cur['items'] if token == '*' else cur['properties'][token]
        return cur
    def typ(path, values, **extra):
        value = prop(path)
        value.clear()
        value.update(type=values, **extra)
    for path in ['identity.generation']:
        typ(path, ['integer', 'null'], minimum=0)
    for path in ['mobility.speed_limit_mps', 'mobility.min_turning_radius_m', 'manipulation.payload_kg',
                 'manipulation.max_pickplace_z_m', 'manipulation.min_pickplace_z_m',
                 'manipulation.radial_workspace_m', 'runtime_state.battery_fraction']:
        typ(path, ['number', 'null'])
    for path in ['identity.host_platform', 'configuration.motion_controller', 'configuration.manipulator_controller',
                 'configuration.external_manipulator', 'configuration.primary_manipulator', 'mobility.navigation_dimension',
                 'manipulation.model', 'manipulation.end_effector', 'manipulation.height_frame',
                 'manipulation.limits_source', 'skills.*.backend', 'sensing.camera_model']:
        typ(path, ['string', 'null'])
    typ('deployment.attachment_validation', ['boolean', 'null'])
    typ('configuration.mount_pose', ['object', 'null'], additionalProperties=False,
        required=['translation_m', 'rotation_wxyz', 'reference_frame'],
        properties={'translation_m': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                    'rotation_wxyz': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4},
                    'reference_frame': {'type': 'string'}})
    enums = {
        'record_type': ['configuration_profile', 'configuration_variant'],
        'deployment.status': list(STATUS_ZH),
        'deployment.controller_maturity': ['standard', 'stable', 'experimental_unstable',
                                           'deployment_baseline_limited'],
        'deployment.fall_recovery': ['unknown', 'unsupported'],
        'mobility.stairs.ascent': list(SUPPORT_ZH), 'mobility.stairs.descent': list(SUPPORT_ZH),
        'skills.*.support': list(SUPPORT_ZH), 'skills.*.availability': list(AVAIL_ZH),
        'skills.*.support_basis': ['source_analysis', 'user_confirmed_project_capability'],
        'sensing.camera_presence_basis': ['source_integrated', 'user_confirmed_default_with_builder_mount',
                                          'required_pending_adaptation'],
        'sensing.camera_integration_status': ['integrated', 'builder_registered_default', 'pending_adaptation'],
        'skills.*.skill_id': ['eai.navigation.node_route', 'eai.manipulation.pick_place'],
        'manipulation.origin': ['none', 'attached', 'integrated', 'primary_robot'],
    }
    for path, enum in enums.items():
        prop(path)['enum'] = enum
    prop('skills').update(minItems=2, maxItems=2)
    prop('configuration.tools')['maxItems'] = 0
    prop('sensing.camera_required')['const'] = True
    prop('sensing.camera_is_default_robot_property')['const'] = True
    return schema


def render_zh(data):
    lines = ['# HeteroCoord 完整机器人池简历（中文审核稿）', '',
             '本稿是配置池设计：任务可以任取子集，也可以创建同配置的多个实例；不是要求每个场景同时放入所有机器人。', '',
             f'代码基线：`{data["baseline_commit"]}`。14 类平台，{len(data["configuration_profiles"])} 份核心配置简历，'
             f'{data["coverage"]["catalog_and_direct_variant_count"]} 份平台/控制器/携臂合法配置，另加 {data["coverage"]["specialized_candidate_count"]} 份独立机械臂配置，共 {len(data["resumes"])} 条。', '',
             '核心审核条目还包含已拒绝的 Lite3 + Z1；该条只记录排除原因，不进入配置池。', '',
             'JSON 中每条记录都具备同一套固定字段，未知值写 `null`。`configuration_profiles` 用于审核能力差异，'
             '`resumes` 按机器人本体与外挂臂区分，另列独立机械臂；G1 的移动与抓放能力归于同一机器人身份。', '',
             '这些是供审核的配置简历，尚未转为运行时输入。`runtime_qualified=false`，实例身份、状态和可用技能均未伪造；'
             '技能的 support 描述配置能力，support_basis 区分源码分析与用户确认；availability 记录执行接入状态，实际可执行还要满足该条前提。', '',
             '**确认约束：Lite3 不能上楼、不能下楼；UR5 可作为独立机器人。UR5 载荷 5 kg、相对安装基座限高 0.824073 m；'
             'Z1 载荷 3 kg、相对安装基座限高 0.798026 m。限高测量点与载荷是否含工具质量待你确认。**', '',
             'UR5 仅作为独立机器人，固定配备 Robotiq 2F-85；不提供裸臂或任何机器人搭载 UR5 的组合。'
             'Pepper 和 G1 默认具备抓取能力；Pepper 默认选用 PEPPER_PICK_PLACE_CFG。G1 只保留一个机器人身份，'
             '其行走和标定抓放是同一机器人在不同运行模式下的能力，模式接线仍待统一。'
             'Z1 独立配置作为有资产但待接入的候选列出。'
             'Kinova Gen3 按已有项目决定不进入正式池。未受目录支持的任意跨型号拼接、多外挂臂或双重登记均不纳入。', '',
             'HeteroCoord 实验不使用键盘控制和 Navigation I/O 工具。相机是机器人默认属性，不作为可选开关。'
             'SimpleCar 与三类无人机使用内建相机；Carter、Pepper、Go2、B2、M20、Scout、MuSHR v2、Coco、Lite3 '
             '默认装配 RealSense D455。G1 相机适配待后续完成；独立 UR5/Z1 的相机接线也尚未建立。'
             '除 Go2 外，每个平台只使用一个实验控制器：B2 为 `B2_MIXED_STAIRS_CFG`，'
             'Pepper 为 `PEPPER_PICK_PLACE_CFG`，G1 已由 `G1_DWAQ_CFG` 完全替换旧 `G1_SKRL_CFG`。'
             'Go2 保留两份简历：`GO2_MIXED_STAIRS_CFG` 是正常稳定版本，'
             '`GO2_EXTREME_PARKOUR_TEACHER_CFG` 是运动不稳定的实验性越野版本。'
             '楼梯控制器的选择不自动确认携臂或持物状态的楼梯资格。', '',
             '## 平台与组合覆盖', '',
             '|平台|本体/携臂配置数|正式控制器数|目录外控制器数|简历数|', '|---|---:|---:|---:|---:|']
    for row in data['coverage']['by_platform']:
        h = row['platform_type']
        lines.append(f'|{LABELS[h]}|{row["attachment_sets"]}|{row["catalog_controller_count"]}|{row["direct_controller_count"]}|{row["expanded_resume_count"]}|')
    lines += ['', '本池仅区分无外挂臂与单外挂 Z1 配置，按 `validate_attachment_types` 筛查。'
              '通过配置筛查不代表物理能力已全部认证。历史和备选控制器均不进入本实验简历池。', '',
              '外挂臂仅保留 Z1 配对，包括当前拒绝配对；拒绝配对不进入合法变体池。', '',
              '## 全部外挂臂配对', '', '|底盘|Z1 + 夹具|', '|---|---|']
    for host in C.robot_keys():
        if C.attachment_catalog()['z1'].supports(host):
            cells = [STATUS_ZH[C.manipulator_deployment_status(host, a)[0]] for a in POOL_MANIPULATORS]
            lines.append('|' + '|'.join([LABELS[host], *cells]) + '|')
    lines += ['', '## 逐项核心简历', '',
              '每项共享上述限值与实例身份规则；以下逐条列明能力、控制器、技能前提和特殊限制。'
              '无外挂臂的 Pepper/G1 默认具备原生抓取能力；普通移动底盘无臂配置不具备抓放。', '']
    for n, rec in enumerate(data['configuration_profiles'], 1):
        dep, conf, mob, man = [rec[k] for k in ['deployment', 'configuration', 'mobility', 'manipulation']]
        lines += [f'### {n:02d}. {rec["name_zh"]}', '', f'配置 ID：`{rec["profile_id"]}`。', '',
                  f'部署：**{STATUS_ZH[dep["status"]]}**。{dep["reason_zh"]}', '',
                  f'运动：{FAMILY_ZH[mob["family"]]} / {MODE_ZH[mob["mode"]]}；运动控制器：`{conf["motion_controller"] or "无（固定/专用配置）"}`；'
                  f'机械臂控制：`{conf["manipulator_controller"] or "无独立外挂臂控制器"}`。', '',
                  f'手臂：{man["arm_count"]}；来源：{ORIGIN_ZH[man["origin"]]}；末端：`{man["end_effector"] or "无"}`。']
        if conf['controller_variant']:
            variant_zh = {'normal_stable': '正常稳定版本', 'experimental_offroad': '实验性越野版本',
                          'dwaq_locomotion': 'DWAQ 行走版本'}[conf['controller_variant']]
            lines += ['', f'控制器版本：**{variant_zh}**；成熟度：`{dep["controller_maturity"]}`；'
                      f'跌倒恢复：{SUPPORT_ZH[dep["fall_recovery"]]}。']
        if dep['known_instability_zh']:
            lines += ['', '已知稳定性限制：**' + dep['known_instability_zh'] + '**']
        if dep['runtime_blockers_zh']:
            lines += ['', '运行时阻塞：' + '；'.join(dep['runtime_blockers_zh'])]
        camera_text = (f'默认配备 `{rec["sensing"]["camera_model"]}`；安装位姿与图像流待实例绑定。'
                       if rec['sensing']['camera_present'] else '实验要求默认相机，但当前工程尚未完成该配置的相机适配。')
        lines += ['', '相机：' + camera_text]
        if man['payload_kg'] is not None:
            lines += ['', f'机械臂限制：{man["payload_kg"]:g} kg；抓放限高 {man["max_pickplace_z_m"]:.6f} m，'
                      '相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。'
                      '此数值不是整机携臂移动载荷或整机满载验证结论。']
        lines += ['', f'楼梯：上楼={SUPPORT_ZH[mob["stairs"]["ascent"]]}，下楼={SUPPORT_ZH[mob["stairs"]["descent"]]}；'
                  f'依据：{STAIR_BASIS_ZH[mob["stairs"]["basis"]]}。', '',
                  '|技能|配置支持|能力依据|操作|实例可用性|后端|', '|---|---|---|---|---|---|']
        for s in rec['skills'][:2]:
            basis = '用户确认的项目能力' if s['support_basis'] == 'user_confirmed_project_capability' else '源码分析'
            backend_label = s['backend'] or ('待接入' if s['support'] == 'supported' else '无')
            lines.append(f'|`{s["skill_id"]}`|{SUPPORT_ZH[s["support"]]}|{basis}|{", ".join(s["operations"]) or "无"}|{AVAIL_ZH[s["availability"]]}|{backend_label}|')
        for s in rec['skills'][:2]:
            if s['support'] != 'unsupported':
                lines += ['', f'`{s["skill_id"]}` 的前提：' + '；'.join(s['requirements_zh']) + '。']
            if s['limitations_zh']:
                lines += ['', f'`{s["skill_id"]}` 的限制：' + '；'.join(s['limitations_zh'])]
        if rec['special_notes_zh']:
            lines += ['', '特殊说明：' + '；'.join(rec['special_notes_zh'])]
        lines += ['', '证据范围：' + dep['evidence_scope_zh'], '']
    lines += ['## 实验相机与传感器规则', '',
              '相机是机器人默认属性，不单独产生简历。无键盘控制或 Navigation I/O 工具。'
              '传感器实际装配变化仍须更新实例配置身份与 generation，但不预先穷举成不同机器人类型。每个宿主最多一个外挂 Z1。', '',
              'Orsus、独立 LiDAR、C01 三者互斥。Carter + Z1 不得再加 Orsus/LiDAR/C01；Go2 + Z1 不得再加 Orsus。'
              '其他已拒绝臂配对也不因附加传感器重新成为合法组合。', '',
              '工程现有相机接线仍须核对实际图像来源；C01 单独不能证明具有可发布图像的相机来源。'
              'SimpleCar 和空中平台已有内建相机配置，其余实验相机的型号与安装方式待实例明确。', '',
              '默认相机声明不替代具体相机硬件与图像接口的接线。相机发布也不等于已经提供识别、分割、目标定位等 skill。', '',
              '## 实验配置中文索引', '',
              '下表每行对应 JSON `resumes` 中一份完整简历；核心能力读取同 `profile_id` 的上文，附件名称全部在此展开。'
              '实例数量不固定，任务可对同一行创建多个不同 `robot_id`。', '',
              '|完整简历 ID|核心配置 ID|外挂臂（所有配置均带相机）|部署状态|', '|---|---|---|---|']
    for rec in data['resumes']:
        desc = '；'.join(ATTACH_LABELS[a] for a in rec['configuration']['attachments']) or '无外挂臂'
        lines.append(f'|`{rec["resume_id"]}`|`{rec["profile_id"]}`|{desc}|{STATUS_ZH[rec["deployment"]["status"]]}|')
    lines += ['', '## 需要审核的项目', '',
              '1. UR5/Z1 限高对应的点：物体原点、抓取点，还是末端执行器参考点；载荷是否包含夹具/工具质量。',
              '2. Go2 稳定版已按用户确认标记可上楼、下楼仍待确认；Go2 越野版、B2、M20、G1 的路线专项资格继续审核。Lite3 已明确不支持上下楼。',
              '3. 目前静止通过的携臂组合，是否需要继续验证携臂移动、持物导航和楼梯；现稿不继承裸机资格。',
              '4. 独立 UR5 的实际安装坐标与对象限制（夹具固定为 Robotiq 2F-85）；独立 Z1 候选是否保留；Pepper/G1 的自有载荷、工作空间另填。',
              '5. 审核通过后再落地运行时 schema、注册与资格检查；本轮仅形成完整固定 JSON 和对应中文简历。', '']
    return '\n'.join(lines)


def main():
    profiles, resumes, coverage = [], [], []
    for host in C.robot_keys():
        arms = [None] + [a for a in POOL_MANIPULATORS if C.attachment_catalog()[a].supports(host)]
        controllers = controller_choices(host)
        for arm, controller in itertools.product(arms, controllers):
            profiles.append(make_record(host, arm, controller))
        sets = valid_sets(host)
        for controller in controllers:
            for seq, values in enumerate(sets, 1):
                arm = next((a for a in values if a in C.MANIPULATOR_TYPES), None)
                record = make_record(host, arm, controller, attachments=values)
                record['record_type'] = 'configuration_variant'
                record['resume_id'] = f'{host}.{controller}.v{seq:04d}'
                resumes.append(record)
        coverage.append(dict(platform_type=host, attachment_sets=len(sets),
                             catalog_controller_count=len([ctrl for ctrl in controllers if ctrl in C.controller_cfgs_for_robot(host)]),
                             direct_controller_count=0,
                             expanded_resume_count=len(sets) * len(controllers)))
    specialized = [make_record(host, arm, None, role='standalone_arm')
                   for host, arm in [('ur5', 'ur5'), ('z1', 'z1')]]
    profiles.extend(specialized)
    for candidate in specialized:
        record = copy.deepcopy(candidate)
        record['record_type'] = 'configuration_variant'
        record['resume_id'] += '.v0001'
        resumes.append(record)
    sources = {
        'catalog': ROOT / 'source/EAI/EAI/hmrs_env/env_diy/catalog.py',
        'builder': BUILDER,
        'skill_registry': ROOT / 'algorithm/skills/registry.py',
        'arm_backend': ROOT / 'task/executors/pick_place_backends/arm.py',
        'pepper_backend': ROOT / 'task/executors/pick_place_backends/pepper.py',
        'drone_backend': ROOT / 'task/executors/pick_place_backends/drone.py',
        'navigation_backend': ROOT / 'task/executors/navigation_backends.py',
        'morphology': ROOT / 'tools/scenario_generation/robot_dossier.py',
        'user_manipulator_limits': LIMIT_FILE,
        'g1_fixed_backend': ROOT / 'task/executors/g1_live.py',
        'g1_pick_place_backend': ROOT / 'task/executors/pick_place_backends/g1.py',
        'global_sim_timing': ROOT / 'source/EAI/EAI/hmrs_env/multi_robot_direct_env_cfg.py',
        'g1_dwaq_integration': ROOT / 'source/EAI_assets/EAI_assets/controller/g1_dwaq/integration.py',
        'g1_dwaq_contract': ROOT / 'source/EAI_assets/EAI_assets/controller/g1_dwaq/deployment_contract.json',
        'g1_dwaq_policy': ROOT / 'source/EAI_assets/EAI_assets/controller/g1_dwaq/policy.onnx',
        'go2_extreme_integration': ROOT / 'source/EAI_assets/EAI_assets/controller/go2_extreme_parkour/integration.py',
        'go2_extreme_deployment': ROOT / 'source/EAI_assets/EAI_assets/controller/go2_extreme_parkour/deployment.py',
        'go2_extreme_provenance': ROOT / 'source/EAI_assets/EAI_assets/controller/go2_extreme_parkour/PROVENANCE.json',
        'go2_extreme_dynamic_policy': ROOT / 'source/EAI_assets/EAI_assets/controller/go2_extreme_parkour/exported/policy_dynamic.onnx',
        'standalone_ur5_asset': ROOT / 'source/EAI_assets/EAI_assets/robots/ur5_mount.py',
        'standalone_z1_asset': ROOT / 'source/EAI_assets/EAI_assets/robots/z1_mount.py',
    }
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    data = {
        '$schema': './robot_pool_resume.schema.json',
        'schema_id': 'heterocoord.robot_pool_resume_review', 'schema_version': '1.3.0',
        'status': 'review_draft_not_runtime_input', 'baseline_commit': commit,
        'scope_zh': 'HeteroCoord实验配置池：14平台与单外挂Z1配对；除Go2的稳定/实验越野双版本外，每个平台一个实验控制器。相机是机器人默认属性，无键盘与Navigation I/O，不穷举传感器排列。UR5仅独立且固定配备Robotiq 2F-85；G1移动与抓放共用一个机器人身份。',
        'experiment_policy': {'camera_required': True, 'camera_optional': False,
                              'camera_is_default_robot_property': True,
                              'keyboard_control_enabled': False, 'navigation_io_enabled': False,
                              'enumerate_sensor_combinations': False,
                              'controller_profile_count_by_platform': {
                                  host: len(controller_choices(host)) for host in C.robot_keys()},
                              'resume_identity_axes': ['platform_type', 'external_manipulator', 'configuration_role']},
        'identity_policy': {
            'arbitrary_pool': True, 'multiple_instances_per_configuration': True,
            'mounted_arm_is_separate_member': False, 'standalone_ur5_is_separate_member': True,
            'missing_values_are_unknown': True,
            'runtime_publication_requires': ['实例唯一 robot_id', '实际配置摘要 configuration_id 与 generation',
                                             '成员/健康状态和所需后端绑定', '资格与约束检查通过后才写 available_skill_ids'],
            'configuration_change_zh': '装卸臂、夹具、传感器或切换控制器后生成新配置身份，更新简历版本/generation并重验受影响资格。',
        },
        'controller_selection': {
            'g1': controller_choices('g1'),
            'pepper': controller_choices('pepper'),
            'go2': controller_choices('go2'),
            'b2': controller_choices('b2'),
            'policy_zh': 'G1只用新DWAQ控制器；Go2保留正常稳定版与运动不稳定的实验越野版；其余平台不展开历史与备选控制器。',
        },
        'sources': [{ 'source_id': key, 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest() }
                    for key, path in sources.items()] + [
                        {'source_id': 'user_lite3_stairs', 'path': 'user_confirmation', 'sha256': None},
                        {'source_id': 'user_pool_policy', 'path': 'user_confirmation: UR5 standalone with fixed Robotiq 2F-85; no mounted UR5; Pepper and G1 grasp by default', 'sha256': None},
                        {'source_id': 'user_experiment_policy', 'path': 'user_confirmation: HeteroCoord has no keyboard or Navigation I/O tools; camera equipment is a default robot property; G1 movement and grasping share one robot identity', 'sha256': None}],
        'exclusions': [{'model': 'kinova_gen3', 'reason_zh': '按已有用户决定不进入 Env DIY；保留资产不代表进入正式机器人池。'},
                       {'model': 'ur5_bare', 'reason_zh': '用户确认 UR5 固定配备 Robotiq 2F-85，不单独使用裸臂。'},
                       {'model': 'mounted_ur5', 'reason_zh': '用户确认所有机器人均不搭载 UR5；UR5 仅作为独立机器人。'},
                       {'model': 'arbitrary_cross_product', 'reason_zh': '不虚构目录不支持的跨型号挂载、多外挂臂或无人机外挂UR5等组合。'}],
        'coverage': {'platform_count': len(C.robot_keys()), 'profile_count': len(profiles),
                     'legal_attachment_set_count': sum(x['attachment_sets'] for x in coverage),
                     'catalog_controller_variant_count': sum(x['attachment_sets'] * x['catalog_controller_count'] for x in coverage),
                     'catalog_and_direct_variant_count': sum(x['expanded_resume_count'] for x in coverage),
                     'controller_import_registered_variant_count': sum(x['deployment']['controller_binding'] == 'builder_import_registered' for x in resumes),
                     'legacy_unwired_variant_count': sum(x['deployment']['status'] == 'declared_unwired' for x in resumes),
                     'specialized_candidate_count': len(specialized),
                     'expanded_resume_count': len(resumes),
                     'profile_status_counts': dict(Counter(x['deployment']['status'] for x in profiles)),
                     'expanded_status_counts': dict(Counter(x['deployment']['status'] for x in resumes)),
                     'by_platform': coverage},
        'configuration_profiles': profiles,
        'resumes': resumes,
    }
    schema = closed_schema(data)
    schema.update({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                   '$id': 'https://heterocoord.local/schema/robot_pool_resume_review.v1.json',
                   '$defs': {'resume': record_schema(profiles[0])}})
    schema['properties']['configuration_profiles']['items'] = {'$ref': '#/$defs/resume'}
    schema['properties']['resumes']['items'] = {'$ref': '#/$defs/resume'}
    # Dictionary keys in summaries are from a fixed vocabulary, even if count is zero.
    for key in ['profile_status_counts', 'expanded_status_counts']:
        data['coverage'][key] = {status: data['coverage'][key].get(status, 0) for status in STATUS_ZH}
        schema['properties']['coverage']['properties'][key] = closed_schema(data['coverage'][key])
    schema['properties']['sources']['items']['properties']['sha256']['type'] = ['string', 'null']
    for filename, value in [('robot_pool_resume.v1.json', data), ('robot_pool_resume.schema.json', schema)]:
        (OUT / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    (OUT / 'robot_pool_resume.zh-CN.md').write_text(render_zh(data))
    print(json.dumps(data['coverage'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
