# Allegro Hand 源审计

本地右手 URDF：`/home/pupil/下载/eai_manipulation_assets_20260915/sources/allegro_hand_ros_v4/allegro_hand_ros_v4-master/src/allegro_hand_description/allegro_hand_description_right.urdf`。

审计结果：21 个链接、20 个关节（16 个可动）、质量总和 `0.9549 kg`，质量和六项惯量字段齐全；但 `link_1.0`、`link_2.0`、`link_3.0`、`link_5.0`、`link_6.0`、`link_7.0`、`link_9.0`、`link_10.0`、`link_11.0`、`link_12.0`、`link_13.0`、`link_14.0`、`link_15.0` 的惯量违反主惯量三角约束。当前状态是 `invalid_source_physics`，不是可挂载候选。

审计器拒绝把“字段存在”当成“物理有效”，也不会用形状或密度猜测替换数据。该源没有不可变上游提交记录，也没有对应 Isaac USD；不能从它建立实机等价或灵巧手挂载验证。修复必须取得可信的部件惯量来源或重新测量后，重新生成 USD 并做完整张量、碰撞和控制审计。

结果文件：`tmp/allegro_hand_audit_v1.json`。复现：

```bash
python tools/validation/hand_candidate_audit.py \
  --urdf '/home/pupil/下载/eai_manipulation_assets_20260915/sources/allegro_hand_ros_v4/allegro_hand_ros_v4-master/src/allegro_hand_description/allegro_hand_description_right.urdf' \
  --report tmp/allegro_hand_audit_v1.json
```
