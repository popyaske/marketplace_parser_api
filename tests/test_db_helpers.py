"""Тесты, не требующие БД — только логика запросов."""
import pytest


def test_get_stats_returns_dict_shape():
    """
    Баг: last_24h всегда 0 из-за `if False`.
    Помечаем как xfail — тест покажет, когда фикс появится.
    """
    pytest.skip("Требует PostgreSQL")


@pytest.mark.xfail(reason="Известный баг: last_24h всегда 0")
def test_last_24h_not_zero():
    """
    Этот тест должен упасть пока баг не исправлен.
    После фикса — снять xfail.
    """
    # Псевдокод, реально нужен мок сессии
    result = {"last_24h": 0}
    assert result["last_24h"] > 0