"""极简模板测试 - 用 asyncmy 直接连 MySQL，不加载任何 app 模块。"""
import asyncio
import asyncmy


EMOJI_CONTENT = "# Test with emoji\n\n## Info\n- **Title**: Test\n- **Items**: item1\n\nDone."


async def main():
    conn = await asyncmy.connect(
        host="localhost", port=3306,
        user="root", password="root223",
        database="AgentPaperDistriller",
        charset="utf8mb4",
    )

    async with conn.cursor() as cur:
        # 0. Check charset
        await cur.execute(
            "SELECT CHARACTER_SET_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='templates' AND COLUMN_NAME='content'"
        )
        charset = (await cur.fetchone())[0]
        print(f"  templates.content charset: {charset}")

        # 1. INSERT with emoji
        print("\n=== TEST 1: INSERT ===")
        await cur.execute(
            "INSERT INTO templates (name, content, domain_tag, is_default, user_id, is_system) "
            "VALUES (%s, %s, %s, 0, NULL, 0)",
            ("_lite_test.md", EMOJI_CONTENT, "Test"),
        )
        await conn.commit()
        insert_id = cur.lastrowid
        print(f"  INSERT OK: id={insert_id}")

        # 2. SELECT and verify
        print("=== TEST 2: SELECT ===")
        await cur.execute(
            "SELECT id, name, content, created_at, updated_at FROM templates WHERE id=%s",
            (insert_id,),
        )
        row = await cur.fetchone()
        print(f"  id={row[0]} name={row[1]} content_len={len(row[2])} created_at={row[3]}")
        assert row[2] == EMOJI_CONTENT, f"content mismatch: expected {len(EMOJI_CONTENT)}, got {len(row[2])}"
        assert row[3] is not None, "created_at is None!"
        print(f"  content matches, created_at present")

        # 3. UPDATE
        print("=== TEST 3: UPDATE ===")
        await cur.execute("UPDATE templates SET domain_tag=%s WHERE id=%s", ("Updated", insert_id))
        await conn.commit()
        await cur.execute("SELECT domain_tag FROM templates WHERE id=%s", (insert_id,))
        assert (await cur.fetchone())[0] == "Updated"
        print(f"  UPDATE OK")

        # 4. DELETE
        print("=== TEST 4: DELETE ===")
        await cur.execute("DELETE FROM templates WHERE id=%s", (insert_id,))
        await conn.commit()
        await cur.execute("SELECT COUNT(*) FROM templates WHERE id=%s", (insert_id,))
        assert (await cur.fetchone())[0] == 0
        print(f"  DELETE OK")

    conn.close()
    print("\n=== ALL TESTS PASSED ===")


asyncio.run(main())
