from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import deps


@pytest.fixture
def redis_for_deps(fake_redis, monkeypatch):
    monkeypatch.setattr(deps, "_get_rl_redis", lambda: fake_redis)
    return fake_redis


def test_clear_user_task_removes_capacity_bookkeeping(redis_for_deps):
    redis_for_deps.sadd("user_tasks:user-1", "task-1")
    redis_for_deps.set("task:task-1:owner", "user-1")

    deps.clear_user_task("user-1", "task-1")

    assert not redis_for_deps.sismember("user_tasks:user-1", "task-1")
    assert not redis_for_deps.exists("task:task-1:owner")


def test_prune_user_tasks_removes_terminal_and_orphaned_tasks(redis_for_deps, monkeypatch):
    redis_for_deps.sadd("user_tasks:user-1", "done-task", "live-task", "orphan-task")
    redis_for_deps.set("task:done-task:owner", "user-1")
    redis_for_deps.set("task:live-task:owner", "user-1")
    monkeypatch.setattr(deps, "_task_is_terminal", lambda task_id: task_id == "done-task")

    active = deps.prune_user_tasks(redis_for_deps, "user-1")

    assert active == 1
    assert redis_for_deps.smembers("user_tasks:user-1") == {"live-task"}


def test_capacity_recovers_after_cancel_cleanup(redis_for_deps, monkeypatch):
    monkeypatch.setattr(deps, "MAX_CONCURRENT_TASKS", 1)
    monkeypatch.setattr(deps, "_task_is_terminal", lambda _task_id: False)
    deps.register_user_task("user-1", "task-1")
    deps.store_task_owner("task-1", "user-1")

    with pytest.raises(HTTPException) as exc:
        deps.check_user_task_capacity("user-1")
    assert exc.value.status_code == 429

    deps.clear_user_task("user-1", "task-1")

    deps.check_user_task_capacity("user-1")
