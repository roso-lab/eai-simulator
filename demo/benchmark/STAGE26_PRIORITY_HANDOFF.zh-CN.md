# 阶段 26：通信曲线分析与双机兼容性

状态：**进行中**  
基线：`21063dd`

## 本阶段产出

1. `communication_analysis.py` 增加容量曲线、参考有效性、C90/C50 包围区间、绝对
   bit/s、`C/C0` 和实例级 bootstrap CI；不外推测试范围外的阈值。
2. 增加固定延迟/抖动、IID/突发丢包的同均值配对差异汇总；缺少配对时只记
   `missing_pair`。
3. 保持通信 runtime、Goal v2/v3、题包、见证计划和所有发布标志不变。
4. 独立定位 ljy 全量回归中的 USD metrics layer relocation 顺序问题；不修改 fixture
   哈希或资产文件。

## 验收

- [x] 通信分析离线单测覆盖 C90/C50 包围、参考不足、重复记录、JP/BP 配对和缺失配对。
- [ ] 本机完整 benchmark 回归通过，外部 Factory USD fixture 单列资产版本缺口。
- [ ] ljy 同提交完整回归通过；若仍受 OpenUSD 全量顺序影响，保留可复现证据并单列环境门禁。
- [ ] Goal 正式 companion/p2p service 仍按已拍板接口先行，不在本阶段静默升级旧接口。

