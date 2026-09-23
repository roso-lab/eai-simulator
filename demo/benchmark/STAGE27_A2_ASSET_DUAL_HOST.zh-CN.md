# 阶段 27：餐厅 A2 资产准备双机复验

本批使用冻结的 `products-candidate-v7`，不修改 overlay、四个哈希绑定产品或 resolver 语义。

本机命令以 `/home/pupil/a/b/c/d/products-candidate-v7` 为 candidate root，并使用当前 checkout 的 `Restaurant_Only.usda`；结果为：

```text
status=ready
hash_resolver=passed
source_closure_exists=true
source_sha256=618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b
copied=false
```

ljy 隔离工作树 `/home/ljy/eai-simulator-worktrees/stage27-scoring` 使用 `/home/ljy/a/b/c/d/products-candidate-v7`，并只读引用 ljy 原工作区的 source USD；结果相同，`copied=false`。ljy 原工作区没有被切换或写入，已有 `stage26-usd-compat` 未提交修改保持不动。

初次使用隔离工作树内的 source USD 失败，原因是 USD 源文件属于 `.gitignore` 的运行资产、不会进入 git bundle；改用原工作区只读源路径后通过。该次失败归类为资产同步方式问题，不是哈希或 overlay 失败。

这只证明 episode 启动前资产准入步骤可复现；餐厅正式 Isaac episode 仍须单独通过显示/GLFW、物理、导航和业务证据门禁，所有发布标志保持关闭。
