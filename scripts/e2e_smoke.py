"""浏览器冒烟测试：真实前端 + 真实后端 + 真实 MySQL + 内嵌 ChromaDB，串起 7 个核心流程：

  1. 用户名密码登录
  2. 创建 Agent
  3. 绑定知识库空间
  4. 发起 SSE 聊天并等回答完整生成
  5. 查看 RAG 引用来源
  6. 普通用户绑定公开 Skill
  7. 管理员编辑 Skill 并回滚到编辑前的版本

大模型和向量化两处换成进程内假实现（tests_e2e/fakes.py），不花钱、不需要联网、结果确定；
其余全部走真实链路——真实 FastAPI 路由、真实 MySQL 读写、真实 ChromaDB 检索、真实 Vue 页面。

用法：
    .venv\\Scripts\\python.exe scripts\\e2e_smoke.py
    .venv\\Scripts\\python.exe scripts\\e2e_smoke.py --headed          # 弹出浏览器窗口，方便看
    .venv\\Scripts\\python.exe scripts\\e2e_smoke.py --keep-services   # 结束后不关后端/前端，方便手动排查

依赖：本地可连 MySQL（读 tests/_route_client.py 同一套 DB_* 环境变量）、`npm --prefix frontend install`
已经跑过、Playwright 浏览器已装（`python -m playwright install chromium`）。

退出码：0 = 7 个流程全过；1 = 有流程失败；2 = 环境没准备好（连不上数据库 / 起不来服务）。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"
_results = []
_e2e_data_dir = ""


def report(step: str, status: str, detail: str = "") -> None:
    _results.append((step, status))
    line = f"[{status}] {step}"
    if detail:
        line += f" - {detail}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)


def _env_check() -> Optional[str]:
    if not os.getenv("JWT_SECRET_KEY"):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:  # noqa: BLE001
            pass
    try:
        from models.init_db import SessionLocal
        import sqlalchemy
        db = SessionLocal()
        db.execute(sqlalchemy.text("SELECT 1"))
        db.close()
    except Exception as e:  # noqa: BLE001
        return f"数据库不可用: {e}"
    return None


class ServiceProcess:
    """子进程包一层：日志写文件而不是 PIPE（PIPE 不主动读会在子进程输出多了之后死锁）。"""

    def __init__(self, name: str, cmd: list, cwd: str, env: dict, log_path: str):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self.proc: Optional[subprocess.Popen] = None
        self._log_file = None

    def start(self) -> None:
        self._log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            self.cmd, cwd=self.cwd, env=self.env,
            stdout=self._log_file, stderr=subprocess.STDOUT,
        )

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def tail(self, n_chars: int = 2000) -> str:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return data[-n_chars:]
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc:
            # npm(.cmd) 在 Windows 下会另起一个 node.exe 子进程干活——只杀 npm 自己那个
            # 外壳进程，真正监听端口的 node 进程会变成孤儿继续跑，下次起服务端口占用。
            # taskkill /T 连子进程树一起杀。
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                )
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if self._log_file:
            self._log_file.close()


def _npm_executable() -> str:
    if os.name == "nt":
        found = shutil.which("npm.cmd") or shutil.which("npm")
        if found:
            return found
        return "npm.cmd"
    return shutil.which("npm") or "npm"


def run(args) -> int:
    env_problem = _env_check()
    if env_problem:
        report("环境检查", FAIL, env_problem)
        return 2

    backend_port = args.backend_port
    frontend_port = args.frontend_port
    base_url = f"http://127.0.0.1:{frontend_port}"
    backend_url = f"http://127.0.0.1:{backend_port}"

    global _e2e_data_dir
    e2e_data_dir = os.path.join(ROOT, "tests_e2e", ".e2e_data")
    os.makedirs(e2e_data_dir, exist_ok=True)
    _e2e_data_dir = e2e_data_dir

    suffix = str(int(time.time()))[-6:]
    normal_name = f"e2e_u_{suffix}"     # 用户名上限 20 个字符（LoginUser.name），前缀要短
    admin_name = f"e2e_a_{suffix}"

    backend_env = dict(os.environ)
    backend_env["E2E_BACKEND_PORT"] = str(backend_port)
    backend_env["E2E_DATA_DIR"] = e2e_data_dir
    # 必须在起后端子进程之前就定好管理员名单——后端读的是它自己进程里的环境变量，
    # orchestrator 事后改自己的 os.environ 对已经起来的子进程没有任何作用。
    backend_env["ADMIN_USER_NAMES"] = admin_name
    backend = ServiceProcess(
        "backend", [sys.executable, "-m", "tests_e2e.e2e_server"], cwd=ROOT, env=backend_env,
        log_path=os.path.join(e2e_data_dir, "backend.log"),
    )

    frontend_env = dict(os.environ)
    frontend = ServiceProcess(
        "frontend",
        [_npm_executable(), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
        cwd=os.path.join(ROOT, "frontend"), env=frontend_env,
        log_path=os.path.join(e2e_data_dir, "frontend.log"),
    )

    ok = False
    try:
        from tests_e2e.fixtures import wait_for_health, wait_for_http

        backend.start()
        report("启动后端", INFO, f"{backend_url}（假 LLM / Embedding，真实数据库 + ChromaDB）")
        time.sleep(1.5)
        if not backend.alive():
            report("启动后端", FAIL, f"进程立刻退出，多半是端口被占用或启动异常\n--- backend.log ---\n{backend.tail()}")
            return 2
        try:
            wait_for_health(backend_url)
        except TimeoutError as e:
            report("启动后端", FAIL, f"{e}\n--- backend.log 末尾 ---\n{backend.tail()}")
            return 2

        frontend.start()
        report("启动前端", INFO, base_url)
        time.sleep(1.5)
        if not frontend.alive():
            report("启动前端", FAIL, f"进程立刻退出，多半是端口被占用或启动异常\n--- frontend.log ---\n{frontend.tail()}")
            return 2
        try:
            wait_for_http(base_url)
        except TimeoutError as e:
            report("启动前端", FAIL, f"{e}\n--- frontend.log 末尾 ---\n{frontend.tail()}")
            return 2

        ok = _run_flows(base_url, backend_url, args, normal_name, admin_name)
    finally:
        if not args.keep_services:
            frontend.stop()
            backend.stop()
        else:
            report("保留服务", INFO, f"后端 pid={backend.proc.pid if backend.proc else '-'}，"
                                    f"前端 pid={frontend.proc.pid if frontend.proc else '-'}，用完手动关掉；"
                                    f"日志在 {e2e_data_dir}")
        # 每次跑都建一批 e2e_* 用户/Agent/Skill/知识库空间，不清理本地库会越跑越多
        # （历史上 tests/_route_client.py 就吃过这个亏）。放最后，不管流程成败都要执行。
        try:
            from tests_e2e.fixtures import purge_e2e_data
            deleted = purge_e2e_data()
            if deleted:
                report("清理测试数据", INFO, f"删除了 {deleted} 个 e2e_* 用户及其级联数据")
        except Exception as e:  # noqa: BLE001 - 清理失败不能掩盖上面流程本身的结果
            report("清理测试数据", INFO, f"清理时出了点问题（不影响上面的验收结果）: {e}")

    print()
    passed = sum(1 for _, s in _results if s == PASS)
    failed = sum(1 for _, s in _results if s == FAIL)
    print(f"共 {passed + failed} 项：{passed} 过，{failed} 没过")
    return 0 if ok and failed == 0 else 1


def _run_flows(base_url: str, backend_url: str, args, normal_name: str, admin_name: str) -> bool:
    from tests_e2e import fixtures as fx

    user = fx.create_db_user(normal_name)
    admin = fx.create_db_user(admin_name, admin=True)

    admin_api = fx.ApiClient(backend_url)
    admin_api.login(admin_name, admin["password"])

    user_api = fx.ApiClient(backend_url)
    user_api.login(normal_name, user["password"])
    fx.ensure_llm_configs(user_api)

    suffix = normal_name.rsplit("_", 1)[-1]
    space_name = f"E2E冒烟测试空间{suffix}"
    skill_name = f"E2E冒烟测试技能{suffix}"

    all_ok = True
    space_id = skill_id = None
    try:
        doc_text = (
            "# 专业版价格说明\n\n专业版一年授权的价格是 39 元/月，包含数据分析和自动化两项能力。\n"
            "如需开票请联系客服。\n"
        )
        created = fx.create_space_with_document(user_api, name=space_name, doc_text=doc_text)
        space_id = created["space_id"]
        skill = admin_api.post("/skill/", json={
            "name": skill_name, "description": "E2E 冒烟测试用的技能",
            "system_prompt": "回答前先说“已收到需求”。", "tool_names": ["word_count"], "is_public": 1,
        })
        skill_id = skill["data"]["id"]
        # 公开 Skill 要“已发布”才会出现在普通用户的列表里（draft 即使 is_public=1 也看不到），所以建完先发布
        current = admin_api.get(f"/skill/{skill_id}")["data"]
        admin_api.put(f"/skill/{skill_id}", json={"lifecycle_status": "published", "expected_row_version": current["row_version"]})
        report("准备知识库空间与公开 Skill", PASS, f"space_id={space_id}, skill_id={skill_id}")
    except Exception as e:  # noqa: BLE001
        report("准备知识库空间与公开 Skill", FAIL, str(e))
        all_ok = False

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        page.on("dialog", lambda d: d.accept())  # 恢复历史版本会弹原生 confirm，一律确认

        try:
            all_ok &= _flow_login(page, base_url, normal_name, user["password"])
            agent_id = None
            if space_id is not None and skill_id is not None:
                agent_id = _flow_create_agent_bind_space_and_skill(
                    page, base_url, user_api, space_name, skill_name,
                )
                all_ok &= agent_id is not None
            if agent_id is not None:
                all_ok &= _flow_chat_and_citations(page, base_url, agent_id)
            if skill_id is not None:
                all_ok &= _flow_admin_edit_and_rollback_skill(
                    browser, base_url, admin_name, admin["password"], admin_api, skill_id,
                )
        except Exception as e:  # noqa: BLE001
            report("流程执行", FAIL, f"未预期的异常: {type(e).__name__}: {e}")
            all_ok = False
        finally:
            if not args.keep_services:
                browser.close()

    return all_ok


def _flow_login(page, base_url, name, password) -> bool:
    try:
        page.goto(base_url)
        page.get_by_placeholder("3–20 个字符").fill(name)
        page.get_by_placeholder("至少 6 位").first.fill(password)
        page.get_by_role("button", name="登录").click()
        page.wait_for_url("**/agents**", timeout=15000)
        report("1. 用户名密码登录", PASS)
        return True
    except Exception as e:  # noqa: BLE001
        report("1. 用户名密码登录", FAIL, str(e))
        return False


def _debug_dump(page, tag: str) -> None:
    if not _e2e_data_dir:
        return
    try:
        page.screenshot(path=os.path.join(_e2e_data_dir, f"fail_{tag}.png"))
        with open(os.path.join(_e2e_data_dir, f"fail_{tag}.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception:  # noqa: BLE001
        pass


def _flow_create_agent_bind_space_and_skill(page, base_url, user_api, space_name, skill_name):
    """创建助手 + 勾选知识库空间 + 勾选公开 Skill——同一个对话框提交，一次成功等于三个流程都过。"""
    agent_name = f"E2E冒烟助手{int(time.time()) % 1000000}"
    try:
        page.goto(f"{base_url}/agents")
        page.get_by_role("button", name="新建助手").first.click()
        page.get_by_role("button", name="空白自定义").click()
        page.get_by_placeholder("例如：论文写作助手").fill(agent_name)

        # 知识库空间那一块是 v-if="ragEnabledBool"，不勾"使用资料库"整块都不渲染
        page.locator("label", has_text="使用资料库").locator("input[type=checkbox]").check()

        space_row = page.locator("label", has_text=space_name)
        space_row.wait_for(timeout=10000)
        space_row.locator("input[type=checkbox]").check()

        skill_row = page.locator("label", has_text=skill_name)
        skill_row.wait_for(timeout=10000)
        skill_row.locator("input[type=checkbox]").check()

        page.get_by_role("button", name="保存", exact=True).click()
        page.wait_for_selector("text=" + agent_name, timeout=15000)
        report("2. 创建 Agent", PASS, agent_name)
    except Exception as e:  # noqa: BLE001
        _debug_dump(page, "create_agent")
        report("2. 创建 Agent", FAIL, str(e))
        report("3. 绑定知识库空间", FAIL, "创建助手失败，跳过")
        report("6. 普通用户绑定公开 Skill", FAIL, "创建助手失败，跳过")
        return None

    try:
        agents = user_api.get("/agent/list")
        items = agents.get("items", agents) if isinstance(agents, dict) else agents
        match = next((a for a in items if a.get("name") == agent_name), None)
        if not match:
            raise RuntimeError(f"在助手列表里没找到刚创建的 {agent_name}")
        agent_id = match["id"]
        detail = user_api.get(f"/agent/{agent_id}")
        space_ids = detail.get("space_ids") or []
        skills = detail.get("skills") or []  # AgentResponse 字段是 skills（对象列表），不是 skill_ids
        if space_ids:
            report("3. 绑定知识库空间", PASS, f"agent.space_ids={space_ids}")
        else:
            report("3. 绑定知识库空间", FAIL, "创建成功但 space_ids 是空的")
        if skills:
            report("6. 普通用户绑定公开 Skill", PASS, f"agent.skills={skills}")
        else:
            report("6. 普通用户绑定公开 Skill", FAIL, "创建成功但 skills 是空的")
        return agent_id
    except Exception as e:  # noqa: BLE001
        report("3. 绑定知识库空间", FAIL, str(e))
        report("6. 普通用户绑定公开 Skill", FAIL, str(e))
        return None


def _flow_chat_and_citations(page, base_url, agent_id) -> bool:
    ok = True
    try:
        page.goto(f"{base_url}/agents/{agent_id}/chat")
        box = page.get_by_placeholder("输入消息…")
        box.wait_for(timeout=15000)
        box.fill("专业版多少钱一个月？")
        page.get_by_role("button", name="发送").click()
        page.wait_for_selector("text=39 元/月", timeout=30000)
        report("4. 发起 SSE 聊天并完成回答", PASS)
    except Exception as e:  # noqa: BLE001
        _debug_dump(page, "chat")
        report("4. 发起 SSE 聊天并完成回答", FAIL, str(e))
        ok = False

    try:
        page.wait_for_selector("text=参考来源", timeout=10000)
        report("5. 查看 RAG 引用", PASS)
    except Exception as e:  # noqa: BLE001
        _debug_dump(page, "citations")
        report("5. 查看 RAG 引用", FAIL, str(e))
        ok = False
    return ok


def _flow_admin_edit_and_rollback_skill(browser, base_url, admin_name, admin_password, admin_api, skill_id) -> bool:
    edited_prompt = f"改过的提示词-{int(time.time())}"
    skill_name = admin_api.get(f"/skill/{skill_id}")["data"]["name"]
    # 独立浏览器上下文：普通用户的登录态（HttpOnly 会话 Cookie）留在原来那个 page 里，
    # 不新开一个上下文的话，导航到 /login 会被 Login.vue 的 onMounted 直接重定向回工作台。
    context = browser.new_context(viewport={"width": 1360, "height": 900})
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())  # 恢复历史版本会弹原生 confirm——这个 page 是新开的上下文，
    # 全局那个 dialog 处理器挂在另一个 page 对象上，这里必须重新挂一次
    try:
        try:
            page.goto(f"{base_url}/login")
            page.get_by_placeholder("3–20 个字符").fill(admin_name)
            page.get_by_placeholder("至少 6 位").first.fill(admin_password)
            page.get_by_role("button", name="登录").click()
            page.wait_for_url("**/admin**", timeout=15000)

            page.goto(f"{base_url}/admin/skills")
            card = page.locator("article").filter(has_text=skill_name)
            card.wait_for(timeout=10000)
            card.get_by_title("编辑").click()

            # 打开编辑框会异步重新拉一次详情（openEdit 里的 skillApi.getSkill），textarea 元素
            # 立刻就在 DOM 里，但内容是等这次请求回来才填进去的——在它回来之前就 fill，
            # 填的字会被这次异步回填直接覆盖掉。等到内容变成已知的原文，才说明这次回填已经完成。
            prompt_box = page.get_by_placeholder("写下这个能力的工作流程、约束、输出格式。")
            from playwright.sync_api import expect
            expect(prompt_box).to_have_value("回答前先说“已收到需求”。", timeout=10000)
            prompt_box.fill(edited_prompt)
            page.get_by_role("button", name="保存", exact=True).click()
            page.wait_for_timeout(800)

            after_edit = admin_api.get(f"/skill/{skill_id}")
            if after_edit["data"]["config"]["system_prompt"] != edited_prompt:
                raise RuntimeError("编辑后系统提示词没有变成预期内容")
            report("7a. 管理员编辑 Skill", PASS)
        except Exception as e:  # noqa: BLE001
            _debug_dump(page, "skill_edit")
            report("7a. 管理员编辑 Skill", FAIL, str(e))
            report("7b. 管理员回滚 Skill", FAIL, "编辑失败，跳过")
            return False

        try:
            card = page.locator("article").filter(has_text=skill_name)
            card.get_by_title("历史版本（改错了可以恢复）").click()
            page.get_by_role("button", name="恢复到此版本").first.click()
            page.wait_for_timeout(800)

            after_restore = admin_api.get(f"/skill/{skill_id}")
            if after_restore["data"]["config"]["system_prompt"] == edited_prompt:
                raise RuntimeError("回滚后系统提示词还是编辑后的内容，没有真的恢复")
            report("7b. 管理员回滚 Skill", PASS)
            return True
        except Exception as e:  # noqa: BLE001
            _debug_dump(page, "skill_rollback")
            report("7b. 管理员回滚 Skill", FAIL, str(e))
            return False
    finally:
        context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--headed", action="store_true", help="弹出浏览器窗口（默认无头）")
    parser.add_argument("--keep-services", action="store_true", help="结束后不关前后端进程和浏览器，方便手动排查")
    parser.add_argument("--backend-port", type=int, default=8011)
    parser.add_argument("--frontend-port", type=int, default=5173)
    sys.exit(run(parser.parse_args()))
