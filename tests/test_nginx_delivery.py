"""三份 nginx 配置（非容器部署 / docker-compose / HTTPS 生产）在发版相关的规则上保持一致。

回归：重新部署后，开着的旧页面去要已经不存在的旧版页面文件，nginx 的 SPA 兜底把它退回成 index.html（200 text/html），
浏览器当脚本执行失败，用户看到“点了没反应 / 白屏”；入口页又没有缓存规则，刷新也可能拿到旧的。
这些规则用真实 nginx 验证过（入口页 no-cache、构建产物长缓存、旧文件 404、安全头都在），这里防止被悄悄改回去。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFS = ("deploy/nginx.conf", "frontend/nginx.conf", "agent-platform-https.conf")


def _locations(text: str) -> dict:
    """location 匹配串 → 块内容（只看一层大括号，这几份配置里 location 不嵌套）。"""
    text = re.sub(r"#[^\n]*", "", text)   # 注释里也会出现 “location” 这个词
    found = {}
    for match in re.finditer(r"^\s*location\s+([^{\n]+?)\s*\{", text, re.M):
        start = match.end()
        end = text.index("}", start)
        found[match.group(1).strip()] = text[start:end]
    return found


class NginxDeliveryTest(unittest.TestCase):
    def test_entry_page_is_revalidated_and_assets_never_fall_back_to_html(self):
        for conf in CONFS:
            with self.subTest(conf=conf):
                locations = _locations((ROOT / conf).read_text(encoding="utf-8"))
                self.assertIn("expires -1;", locations.get("= /index.html", ""), "入口页要每次向服务器确认新版本")
                assets = locations.get("/assets/", "")
                self.assertIn("try_files $uri =404;", assets, "构建产物找不到要 404，不能退回首页")
                self.assertRegex(assets, r"expires\s+\d+[dy];", "带哈希的构建产物可以长期缓存")
                self.assertIn("/index.html", locations.get("/", ""), "前端路由仍然由 index.html 处理")
                self.assertIn("expires -1;", locations.get("/", ""), "前端路由返回的也是入口页，同样不能长期缓存")

    def test_no_add_header_inside_locations(self):
        """location 里一旦写 add_header，server 级的安全头（CSP、X-Frame-Options 等）在这个 location 里就全部失效。"""
        for conf in CONFS:
            with self.subTest(conf=conf):
                for name, body in _locations((ROOT / conf).read_text(encoding="utf-8")).items():
                    self.assertNotIn("add_header", body, f"{conf} 的 location {name} 里写了 add_header")


if __name__ == "__main__":
    unittest.main()
