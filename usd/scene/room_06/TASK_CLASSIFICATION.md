# room_06：场景任务分类
- **场景族（scene family）**：residential_indoor
- **建议主分类（paper task domain）**：**Living and public space**

## 分类理由

适合家庭协作访问、风险规避和空间恢复任务。

## 可承载的其他任务域

Service and operations；物资配送可用 Production and logistics。

## 任务注册约束

该文件给出资产层面的建议主分类。正式评测时，应根据具体任务的终端成功谓词（terminal-value predicate）在 task contract 中注册唯一 domain；同一场景可以拥有多个域的任务，但一个具体 task instance 只能属于一个正式域。
