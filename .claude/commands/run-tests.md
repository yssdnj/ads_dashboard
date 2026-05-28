运行 ads_funnel/api/ 目录下的测试文件。

步骤：
1. 用 glob 找出所有 ads_funnel/api/_test_*.py 文件
2. 若没有测试文件，告知用户"未找到测试文件（_test_*.py）"
3. 逐个运行：python ads_funnel/api/_test_<name>.py
   - 工作目录：项目根目录（ads_dashboard/）
4. 每个文件运行完后展示输出，标注 ✅ 通过 / ❌ 失败
5. 最后汇总：N 个测试文件，M 个通过，K 个失败

注意：
- 失败时展示完整错误信息，不截断
- 不要用 pytest，直接 python 运行（测试文件是独立脚本）
- 不要修改测试文件
