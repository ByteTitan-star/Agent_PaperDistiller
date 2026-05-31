"""
模板 CRUD 端到端测试（直接操作数据库，不需要 HTTP 服务）。
验证: emoji 内容写入、created_at 加载、owner 关系。
用法: cd backend && python test_template_api.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import async_session_factory
from app.models import Template


EMOJI_CONTENT = """# Test with emoji

## Info
- **Title**: Test
- **Summary**: hello world
- **Items**:
  - item1
  - item2

Done."""


async def main():
    async with async_session_factory() as session:
        # 1. CREATE
        print("=== TEST 1: Create template with emoji ===")
        t = Template(
            name="_test_e2e.md",
            content=EMOJI_CONTENT,
            domain_tag="Test",
            user_id=None,
            is_system=False,
        )
        session.add(t)
        await session.flush()
        await session.refresh(t)
        print(f"  OK: id={t.id}, name={t.name}")
        print(f"  created_at={t.created_at}")
        print(f"  updated_at={t.updated_at}")
        assert t.id is not None, "id should be auto-generated"
        assert t.created_at is not None, "created_at should be loaded"
        assert t.updated_at is not None, "updated_at should be loaded"
        assert len(t.content) == len(EMOJI_CONTENT), "content length mismatch"
        print(f"  content_len={len(t.content)} (expected {len(EMOJI_CONTENT)})")

        # 2. READ via query with selectinload
        print("\n=== TEST 2: Read via select query ===")
        result = await session.execute(
            select(Template)
            .options(selectinload(Template.owner))
            .where(Template.id == t.id)
        )
        t2 = result.scalar_one()
        assert t2.content == EMOJI_CONTENT, "content mismatch after read"
        assert t2.created_at is not None, "created_at lost after query"
        print(f"  OK: name={t2.name}, content_len={len(t2.content)}, created_at={t2.created_at}")

        # 3. UPDATE
        print("\n=== TEST 3: Update template ===")
        t2.domain_tag = "Updated"
        await session.flush()
        await session.refresh(t2)
        assert t2.domain_tag == "Updated", "domain_tag not updated"
        print(f"  OK: domain_tag={t2.domain_tag}")

        # 4. DELETE
        print("\n=== TEST 4: Delete template ===")
        await session.delete(t2)
        await session.flush()
        result = await session.execute(
            select(Template).where(Template.id == t.id)
        )
        assert result.scalar_one_or_none() is None, "template should be deleted"
        print(f"  OK: template deleted")

        await session.commit()

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
