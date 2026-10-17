离线内容审阅包的工程检查记录

包目录：../2026-10-17-v0.11.17/
全部28项均待审核；本目录仅记录导出、筛选、显示和文件一致性检查。
inspect.mjs为实际使用的浏览器驱动，原执行位置为项目根目录下.runtime/review-packet-20261017/inspect.mjs；依赖项目现有Playwright和/usr/bin/google-chrome。
首次Chrome启动因执行沙箱禁止setsockopt而退出；在获准的执行环境中使用临时浏览器配置复验通过，驱动阻断HTTP请求。自动化含--no-sandbox，不是浏览器进程沙箱有效性验证。
无模型API请求；没有临床审核或研究参与者。
