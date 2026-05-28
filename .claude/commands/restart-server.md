彻底重启 ads_funnel 服务（端口 5001）。

步骤：
1. 用 PowerShell 列出所有 python 进程（Get-Process python*），全部 Stop-Process -Force
2. 等待 2 秒后用 netstat -ano | Select-String ":5001" 确认端口已释放
3. 在 ads_funnel/ 目录下后台启动：nohup uvicorn api.main:app --reload --port 5001
4. 等待 4 秒后 curl http://127.0.0.1:5001/docs 验证返回 200
5. 再次检查 netstat，确认只有**一个** PID 在监听 5001
6. 告知用户：启动成功 / PID / 如有多个 PID 则报警

注意：
- 必须杀掉所有 python 进程，不能只杀单个 PID
- 启动前必须确认端口已空闲
- 启动后必须验证只有一个 PID，防止旧进程残留
