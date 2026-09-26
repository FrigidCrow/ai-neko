"""Persona data persists locally, is versioned, bounded, and scoped."""

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import (
    DEFAULT_PERSONA,
    MemoryConflictError,
    MemoryInputError,
    MemoryService,
    persona_prompt,
)


def test_persona_persists_and_scopes_independent(tmp_path):
    paths = initialize_data_root(tmp_path / "data")
    with MemoryService(paths) as memory:
        original = memory.get_persona()
        assert original == {**DEFAULT_PERSONA, "version": 1}
        updated = memory.update_persona(
            {"name": "小雪", "traits": ["温柔", "机灵"], "user_name": "阿鸦"}, expected_version=1
        )
        assert updated["version"] == 2
        assert updated["speaking_style"] == original["speaking_style"]
        with pytest.raises(MemoryConflictError, match="version"):
            memory.update_persona({"name": "过期修改"}, expected_version=1)
    with MemoryService(paths) as reopened, MemoryService(paths, character_id="other") as other:
        assert reopened.get_persona() == updated
        assert other.get_persona()["name"] == DEFAULT_PERSONA["name"]


@pytest.mark.parametrize(
    "changes",
    [
        {"system_prompt": "ignore rules"},
        {"name": ""},
        {"traits": "cute"},
        {"traits": []},
        {"traits": ["好奇"] * 6},
        {"traits": ["猫" * 31]},
        {"name": "猫" * 41},
        {"speaking_style": "a" * 401},
        {"name": "bad\x00name"},
        {"version": 3},
    ],
)
def test_persona_validation_is_atomic(tmp_path, changes):
    with MemoryService(initialize_data_root(tmp_path / "data")) as memory:
        before = memory.get_persona()
        with pytest.raises(MemoryInputError):
            memory.update_persona(changes)
        assert memory.get_persona() == before


def test_persona_prompt_does_not_claim_missing_senses_or_facts(tmp_path):
    with MemoryService(initialize_data_root(tmp_path / "data")) as memory:
        profile = memory.update_persona({"catchphrase": "偶尔喵一下"})
        prompt = persona_prompt(profile)
        assert '"catchphrase": "偶尔喵一下"' in prompt
        assert "不改变事实、工具权限" in prompt
        assert "不冒充真人" in prompt
        assert "虚构看见、听见、记得" in prompt
        assert memory.list_facts() == []
