"""
诊断和修复 MySQL 表字符集为 utf8mb4。
按外键依赖顺序转换，避免 FK 不兼容错误。
用法: cd backend && python fix_charset.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text
from app.database import engine

# 按 FK 依赖顺序排列：被引用的表在前
TABLE_ORDER = [
    "users",
    "papers",
    "templates",
    "user_api_configs",
    "chat_sessions",
    "chat_messages",
    "task_records",
    "system_settings",
    "audit_logs",
    "email_verifications",
    "token_usage_logs",
]


async def main():
    print("=== 1. 检查当前字符集 ===")
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
            "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = DATABASE()"
        ))
        row = result.fetchone()
        print(f"  数据库: {row[0]} / {row[1]}")

        result = await conn.execute(text(
            "SELECT TABLE_NAME, TABLE_COLLATION "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
        ))
        needs_fix = []
        for tbl in result.fetchall():
            collation = tbl[1] or ""
            charset = collation.split("_")[0]
            if charset != "utf8mb4":
                needs_fix.append(tbl[0])
            print(f"  {'FIX' if charset != 'utf8mb4' else 'OK '} {tbl[0]:30s} {collation}")

    if not needs_fix:
        print("\n所有表已是 utf8mb4，无需修复。")
        return

    print(f"\n=== 2. 修复 {len(needs_fix)} 张表 (按 FK 依赖顺序) ===")

    # 先修复数据库默认字符集
    async with engine.begin() as conn:
        print("  修复数据库默认字符集...")
        await conn.execute(text(
            "ALTER DATABASE CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
        print("  OK: database default")

    # 按依赖顺序转换表
    async with engine.begin() as conn:
        for table_name in TABLE_ORDER:
            if table_name not in needs_fix:
                continue
            try:
                sql = (
                    f"ALTER TABLE `{table_name}` "
                    f"CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                print(f"  执行: {sql}")
                await conn.execute(text(sql))
                print(f"  OK: {table_name}")
            except Exception as e:
                print(f"  FAIL: {table_name} -> {e}")
                print(f"  尝试逐列修改...")
                # 回退: 逐个修改文本列
                try:
                    await conn.execute(text(
                        f"ALTER TABLE `{table_name}` "
                        f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    ))
                    col_result = await conn.execute(text(
                        "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:tbl "
                        "AND CHARACTER_SET_NAME IS NOT NULL",
                    ), {"tbl": table_name})
                    for col in col_result.fetchall():
                        alter_col = (
                            f"ALTER TABLE `{table_name}` "
                            f"MODIFY `{col[0]}` {col[1]} "
                            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                        )
                        try:
                            await conn.execute(text(alter_col))
                            print(f"    列 {col[0]}: OK")
                        except Exception as e2:
                            print(f"    列 {col[0]}: SKIP ({e2})")
                    print(f"  OK (逐列): {table_name}")
                except Exception as e2:
                    print(f"  FAIL (逐列也失败): {table_name} -> {e2}")

    print("\n=== 3. 验证 ===")
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT TABLE_NAME, TABLE_COLLATION "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
        ))
        for tbl in result.fetchall():
            collation = tbl[1] or ""
            charset = collation.split("_")[0]
            print(f"  {'OK ' if charset == 'utf8mb4' else 'FAIL'} {tbl[0]:30s} {collation}")


if __name__ == "__main__":
    asyncio.run(main())
