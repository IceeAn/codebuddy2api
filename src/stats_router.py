"""管理台持久化请求统计 API。"""

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from .auth_router import require_session_user
from .auth_types import AuthenticatedUser
from .private_response import PrivateNoStoreRoute
from .usage_stats_middleware import dropped_usage_events
from .usage_stats_store import (
    MAX_STATS_TIMESTAMP,
    SQLITE_MAX_INTEGER,
    StatsFilters,
    usage_stats_store,
)

router = APIRouter(route_class=PrivateNoStoreRoute)

Traffic = Literal["all", "external", "admin"]
Granularity = Literal["auto", "hour", "day", "week"]
Dimension = Literal["models", "api_keys", "credentials"]


def _stats_filters(
    *,
    start_at: Optional[int],
    end_at: Optional[int],
    timezone: str,
    traffic: Traffic,
    model: Optional[str],
    api_key_id: Optional[str],
    credential_id: Optional[str],
    outcome: Optional[str],
    granularity: Granularity = "auto",
) -> StatsFilters:
    return StatsFilters(
        start_time=start_at,
        end_time=end_at,
        timezone=timezone,
        traffic=traffic,
        model=model,
        api_key_id=api_key_id,
        credential_id=credential_id,
        outcome=outcome,
        granularity=granularity,
    )


def _invalid_query(error: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.get("/overview")
def get_stats_overview(
    start_at: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    end_at: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    timezone: str = "UTC",
    traffic: Traffic = "all",
    model: Optional[str] = None,
    api_key_id: Optional[str] = None,
    credential_id: Optional[str] = None,
    outcome: Optional[str] = None,
    granularity: Granularity = "auto",
    user: AuthenticatedUser = Depends(require_session_user),
):
    """返回当前用户在指定时间范围内的统计总览。"""
    try:
        filters = _stats_filters(
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            traffic=traffic,
            model=model,
            api_key_id=api_key_id,
            credential_id=credential_id,
            outcome=outcome,
            granularity=granularity,
        )
        return usage_stats_store.get_overview(
            user.username,
            filters,
            dropped_events=(
                usage_stats_store.get_dropped_events(user.username)
                + dropped_usage_events.get(user.username)
            ),
        )
    except ValueError as error:
        raise _invalid_query(error) from error


@router.get("/requests")
def list_stats_requests(
    start_at: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    end_at: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    timezone: str = "UTC",
    traffic: Traffic = "all",
    model: Optional[str] = None,
    api_key_id: Optional[str] = None,
    credential_id: Optional[str] = None,
    outcome: Optional[str] = None,
    page: Annotated[int, Query(ge=1, le=SQLITE_MAX_INTEGER)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    snapshot_id: Annotated[Optional[int], Query(ge=0, le=SQLITE_MAX_INTEGER)] = None,
    snapshot_time: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    user: AuthenticatedUser = Depends(require_session_user),
):
    """返回当前用户最近 90 天内的脱敏请求明细。"""
    try:
        filters = _stats_filters(
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            traffic=traffic,
            model=model,
            api_key_id=api_key_id,
            credential_id=credential_id,
            outcome=outcome,
        )
        return usage_stats_store.list_events(
            user.username,
            filters,
            page=page,
            page_size=page_size,
            snapshot_id=snapshot_id,
            snapshot_time=snapshot_time,
        )
    except ValueError as error:
        raise _invalid_query(error) from error


@router.get("/dimensions/{dimension}")
def list_stats_dimensions(
    dimension: Dimension,
    start_at: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    end_at: Annotated[Optional[int], Query(ge=0, le=MAX_STATS_TIMESTAMP)] = None,
    timezone: str = "UTC",
    traffic: Traffic = "all",
    model: Optional[str] = None,
    api_key_id: Optional[str] = None,
    credential_id: Optional[str] = None,
    outcome: Optional[str] = None,
    search: Annotated[str, Query(max_length=100)] = "",
    cursor: Annotated[Optional[str], Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    user: AuthenticatedUser = Depends(require_session_user),
):
    """分页返回指定统计维度的完整历史排行。"""
    try:
        filters = _stats_filters(
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            traffic=traffic,
            model=model,
            api_key_id=api_key_id,
            credential_id=credential_id,
            outcome=outcome,
        )
        return usage_stats_store.list_dimension_values(
            user.username,
            dimension,
            filters,
            search=search,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise _invalid_query(error) from error


@router.get("/requests/{id}")
def get_stats_request(
    event_id: Annotated[int, Path(alias="id", gt=0, le=SQLITE_MAX_INTEGER)],
    user: AuthenticatedUser = Depends(require_session_user),
):
    """返回当前用户的一条脱敏请求明细。"""
    try:
        event = usage_stats_store.get_event(user.username, event_id)
    except ValueError as error:
        raise _invalid_query(error) from error
    if event is None:
        raise HTTPException(status_code=404, detail="Usage event not found")
    return event
