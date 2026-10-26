# 被测系统接入规范：观测调用（M1）

观测调用通过 `eai.observation.camera` 提供按需单帧 RGB。被测系统在初始化时声明 `initialize_camera_tools(profile)`，宿主只把当前会话公开的机器人和相机能力放入 profile；没有该钩子时，宿主不泵送观测帧，也不产生相机通信字节。

请求使用冻结的 `SkillRequest` 形状：

```text
skill_id = eai.observation.camera
parameters = {
  camera_ref, mode="capture", deadline,
  resolution={width, height}, quality={encoding, max_bytes}
}
```

`camera_ref` 是当前公开 profile 中的传感器 ID，不能从私有 registry 枚举。`encoding` 只能使用该相机登记的白名单；`max_bytes`、分辨率和 deadline 必须同时满足 profile 与本回合仿真预算。首版拒绝 `requested_sim_time_s`、pose override、连续订阅和发现类调用。

每个请求恰有一个终态：`delivered`、`timeout`、`cancelled`、`failed` 或 `episode_ended`。accepted 只表示宿主接受请求，不表示图像已经交付。图像由已配置的 builder 默认相机创建私有 render product/annotator 采集；假后端只用于 CPU 静态测试，Orsus 只是后端协议的可选实现样例。

图像字节按编码后的传输大小进入通信账本，已 offered/link 的字节不会因超时或迟到帧抑制而回滚。终态后的 transport 帧不再交给被测系统。收据只携带机器人、相机、拍号、摘要、字节数、传输消息和内容哈希；payload 本体留在评测侧内容寻址 artifact store，不暴露本地路径、`payload_ref` 或 `file://`。

宿主以 `update_camera_tools(delta)`（若实现）或公开 observation profile-delta 事件通知经 `member_join` 认证的新成员。激活按已提交会话事件序号幂等；直接 `register_public_robot` 永久禁用。M2 订阅流与发现锚点属于后续阶段，正式资格门保持 false。
