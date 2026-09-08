# GREEN：多仓产物溯源

## 结果

**PASS**。加载技能后的代理分别输出 build/repo/artifact/deployment 四张矩阵，把四个独立 Git 根全部保持为 `unresolved`，没有用父仓 clean 代替子仓身份，也没有用总包版本或复制成功代替包内成员、设备文件和运行加载证据。

代理将潜在问题按 `repo_mismatch`、`wrong_build_root`、`stale_artifact`、`package_member_mismatch`、`device_file_mismatch`、`wrong_slot`、`runtime_not_reloaded` 分类；由于缺少具体 hash/Build-ID，正确保持 aggregate verdict=`unresolved`，没有把“疑似旧件”伪装为已证 mismatch。
