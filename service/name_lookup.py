"""给 Java 业务系统返回的结构补上用户/部门名称（业务系统只存 id）。"""
import re
from typing import Dict, List

from sqlalchemy import text

_IDENTIFIER = re.compile(r"^(`[A-Za-z_][A-Za-z0-9_]*`|[A-Za-z_][A-Za-z0-9_]*)$")


async def names(db, table: str, ids: List[int], column: str = "name") -> Dict[int, str]:
    """table/column 只能是代码里写死的标识符，不接受外部输入（这里再校验一次格式，哪怕以后有人传了变量也拼不进别的 SQL）。"""
    if not _IDENTIFIER.match(table or "") or not _IDENTIFIER.match(column or ""):
        raise ValueError("表名和列名只能是标识符")
    unique = sorted({int(i) for i in ids if i is not None})
    if not unique:
        return {}
    marks = ",".join(f":i{n}" for n in range(len(unique)))
    rows = (await db.execute(text(f"SELECT id, {column} FROM {table} WHERE id IN ({marks})"),
                             {f"i{n}": v for n, v in enumerate(unique)})).all()
    return {int(row[0]): row[1] for row in rows}


async def user_names(db, ids: List[int]) -> Dict[int, str]:
    return await names(db, "`user`", ids)


async def team_names(db, ids: List[int]) -> Dict[int, str]:
    return await names(db, "teams", ids)
