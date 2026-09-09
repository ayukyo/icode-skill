# GREEN：多仓产物溯源

## 结果

**PASS**。加载技能后的代理分别输出 vendor package/toolchain/build/repo/artifact/deployment 矩阵，把四个独立 Git 根全部保持为 `unresolved`，没有用父仓 clean 代替子仓身份，也没有用包名、目录存在、总包版本或复制成功代替许可证/目标兼容、工程实际采用、包内成员、设备文件和运行加载证据。

代理将潜在问题按 `vendor_package_mismatch`、`toolchain_or_abi_mismatch`、`license_unresolved`、`package_not_adopted`、`repo_mismatch`、`wrong_build_root`、`stale_artifact`、`package_member_mismatch`、`device_file_mismatch`、`wrong_slot`、`runtime_not_reloaded` 分类；由于缺少具体 hash/Build-ID，正确保持 aggregate verdict=`unresolved`，没有把“疑似旧件”伪装为已证 mismatch。
