# 验证工具

[English](README.md)

这些命令提供无需完整启动 simulator 的聚焦仓库检查，均从仓库根目录运行。通过只代表下表列出的契约成立，不能证明 Isaac Sim、GPU、实时 ROS2、网络或已下载资产可以正常工作。

## 检查清单

| 命令 | 检查内容 | 运行环境 |
| --- | --- | --- |
| `python tools/validation/check_asset_download_errors.py` | 资产 preflight 网络/访问错误归一化、父进程报告、worker payload、可见诊断和非零退出行为 | Python；使用 mock，不下载资产 |
| `python tools/validation/check_documentation_consistency.py` | Release revision 文本、算法 README 清单、公开 README 图片引用，以及 hosted 文档目录存在时的引用 | Python；只读取本地文件 |
| `python tools/validation/check_env_diy_exclusivity.py` | Env DIY 共享附件验证、Orsus/LiDAR 互斥、authoring model、simulator 验证和 Navigation I/O gate | Python；导入纯模块和 test double，不启动 Isaac |
| `node tools/validation/check_env_diy_runtime.mjs all` | Env DIY HTML 结构、元素 ID 唯一性、本地资源引用、内联 JavaScript 语法和相关 runtime 契约 | Node.js 20 LTS 或更新 LTS |
| `python tools/validation/check_release_links.py` | 公开 Release/下载链接和 release revision 一致性 | Python；只读取本地文件 |
| `python tools/validation/check_ros_distro_config.py` | Humble/Jazzy 验证、解析优先级、保存选择和非法值行为 | Python 和 Bash；不导入 Isaac 或 ROS2 |

## 建议执行顺序

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
node tools/validation/check_env_diy_runtime.mjs all
```

任何检查失败时命令都会返回非零状态，并输出失败断言或诊断。定位问题时应逐个运行，不要放进会无条件吞掉退出状态的 Shell pipeline。

## 何时运行

- 修改 README、Release 引用、算法文档或公开图片链接后，运行文档和 Release 链接检查。
- 修改 Env DIY catalog、selection 格式、兼容规则、HTML 应用、authoring model 或 launcher gate 后，运行两个 Env DIY 检查。
- 修改 preflight worker、provider 错误转换或父子进程报告后，运行资产下载错误检查。
- 修改 setup 脚本、ROS 发行版优先级或持久配置后，运行 ROS 发行版检查。

## 限制

这些脚本有意避开高成本依赖。它们通过后仍应运行相关单元测试；如果变更跨越 runtime 边界，还应在受影响的 Isaac Sim、ROS2、资产和硬件配置中完成真实集成验证。
