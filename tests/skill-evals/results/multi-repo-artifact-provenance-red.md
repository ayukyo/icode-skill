# RED：多仓产物溯源

## 实际结果

未加载新技能的代理正确拒绝“顶层 clean + 构建成功 + 复制成功 = 部署正确”，并提出从 manifest commit 到运行加载文件的逐模块大矩阵。

## 可复现缺口

输出把 build、repo、artifact、deployment 混在一张自由表中，没有稳定拆出 `build_identity`、`repo_matrix`、`artifact_matrix`、`deployment_matrix`、`mismatches`、`verdict`；未统一表达 target、build root、package member、install slot、device identity 和 exact window，后续工具难以比较“期望、实际构建、包内、设备落盘、进程加载”五类身份。

## RED 判定

**FAIL（产物身份合同不稳定）**。新技能保留其正确的逐模块闭环思路，并把每段身份拆成可审计矩阵。
