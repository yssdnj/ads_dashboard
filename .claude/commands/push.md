检查当前 git 状态，列出所有变更文件，询问用户要提交哪些文件（排除临时文件、.db 文件、日志文件），然后根据变更内容生成合适的 commit message，创建 commit 并推送到当前分支的远端（git push origin HEAD）。

注意：
- 不要提交 *.db、*.log、nohup.out、.env、__pycache__ 等文件
- commit message 用中文描述本次变更内容
- 推送前展示将要执行的操作，等用户确认
