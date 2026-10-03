"""给 Java 业务系统返回的结构补上用户/部门名称（业务系统只存 id）。"""
from typing import Dict, List

from sqlalchemy import text


async def names(db, table: str, ids: List[int], column: str = "name") -> Dict[int, str]:
    """table/column 只能是代码里写死的标识符，不接受外部输入。"""
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
