"""
🧠 Brain Memory API — 닥터브릉이의 중앙 기억 관리
모든 지식을 한 곳에서 정돈하고, 검색하고, 관리

Endpoints:
  POST /api/v1/memory/submit          — 새 기억/지식 제출
  GET  /api/v1/memory/list            — 기억 목록 (태그/차량별 필터)
  GET  /api/v1/memory/{id}            — 기억 상세
  POST /api/v1/memory/{id}/verify     — 기억 검증 (맞음/틀림 투표)
  POST /api/v1/memory/{id}/helpful    — '도움됨' 투표
  GET  /api/v1/memory/search          — 전문 검색
  GET  /api/v1/memory/stats           — 전체 메모리 통계
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

from app.db.database import get_db, SoundKnowledge
from app.db.community_schema import (
    BrainMemory, MemoryVerification, MemoryTag, MemoryStatus,
    SoundReport, SoundReportType,
    Notification
)
from app.db.points_schema import PointWallet, PointTransaction, PointTxType
from app.api.auth import get_current_user
from app.db.database import User

router = APIRouter(prefix="/api/v1/memory", tags=["🧠 Brain Memory"])


# ─── Pydantic Models ──────────────────────────────────────────

class MemorySubmitRequest(BaseModel):
    """새 기억 제출"""
    memory_type: MemoryTag
    title: str = Field(..., max_length=200)
    content: str = Field(..., description="자연어로 설명 (예: 엔진 시동 시 떨림)")
    vehicle_type: Optional[str] = None
    vehicle_brand: Optional[str] = None
    vehicle_model: Optional[str] = None
    component: Optional[str] = None
    dominant_freq: Optional[float] = None
    freq_signature: Optional[Dict] = None
    audio_url: Optional[str] = None
    structured_data: Optional[Dict] = None
    tags: List[str] = []
    session_id: Optional[str] = None   # 진단 세션에서 자동 생성된 경우


class MemoryVerifyRequest(BaseModel):
    """기억 검증 요청"""
    is_correct: bool
    correction_note: Optional[str] = None


class MemoryResponse(BaseModel):
    """기억 응답"""
    id: str
    memory_type: str
    title: str
    content: str
    vehicle_type: Optional[str]
    vehicle_brand: Optional[str]
    component: Optional[str]
    confidence: float
    verification_count: int
    helpful_count: int
    status: str
    tags: List[str]
    source_type: str
    points_awarded: int
    created_at: str

    class Config:
        from_attributes = True


# ─── Helpers ─────────────────────────────────────────────────

MEMORY_POINTS = {
    MemoryTag.NORMAL_SOUND:  30,   # 정상 소리 제보
    MemoryTag.FAULT_SOUND:   80,   # 고장 소리 제보 (더 가치 있음)
    MemoryTag.WARNING_SOUND: 50,   # 경고 소리
    MemoryTag.VEHICLE_INFO:  20,   # 차량 정보
    MemoryTag.REPAIR_TIP:    60,   # 수리 팁
    MemoryTag.EXPERT_NOTE:   100,  # 전문가 노트
    MemoryTag.USER_REPORT:   25,   # 일반 제보
    MemoryTag.VERIFIED:      150,  # 검증 완료 보너스
}

VERIFICATION_POINTS = 10    # 검증 참여 포인트
HELPFUL_VOTE_POINTS = 5     # '도움됨' 투표 포인트


async def award_points(
    db: AsyncSession,
    user_id: str,
    amount: int,
    tx_type: PointTxType,
    description: str,
    ref_id: str,
):
    """포인트 지급 헬퍼"""
    # 지갑 조회 또는 생성
    wallet_result = await db.execute(
        select(PointWallet).where(PointWallet.user_id == user_id)
    )
    wallet = wallet_result.scalar_one_or_none()

    if not wallet:
        wallet = PointWallet(user_id=user_id, balance=0, locked=0)
        db.add(wallet)
        await db.flush()

    wallet.balance += amount
    wallet.lifetime_earned += amount
    wallet.monthly_earned += amount
    wallet.updated_at = datetime.utcnow()

    # 거래 기록
    tx = PointTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tx_type=tx_type,
        amount=amount,
        balance_after=wallet.balance,
        ref_id=ref_id,
        ref_type="brain_memory",
        description=description,
        note="앱 내 가상 재화 거래",
    )
    db.add(tx)


async def send_notification(
    db: AsyncSession,
    user_id: str,
    notif_type: str,
    title: str,
    body: str,
    ref_id: str = None,
    ref_type: str = None,
):
    """알림 전송 헬퍼"""
    notif = Notification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        notif_type=notif_type,
        title=title,
        body=body,
        ref_id=ref_id,
        ref_type=ref_type,
    )
    db.add(notif)


# ─── Endpoints ───────────────────────────────────────────────

@router.post("/submit", summary="새 기억/지식 제출")
async def submit_memory(
    req: MemorySubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    🧠 닥터브릉이에게 새 기억을 제출합니다.

    - 정상 소리, 고장 소리, 수리 팁, 차량 정보 등 다양한 유형
    - 제출 즉시 포인트 지급 (검토 전 기본 포인트)
    - 전문가 검증 완료 시 추가 보너스
    """
    memory_id = str(uuid.uuid4())

    # 기본 포인트 계산
    base_points = MEMORY_POINTS.get(req.memory_type, 25)

    memory = BrainMemory(
        id=memory_id,
        memory_type=req.memory_type,
        title=req.title,
        content=req.content,
        structured_data=req.structured_data or {},
        vehicle_type=req.vehicle_type,
        vehicle_brand=req.vehicle_brand,
        vehicle_model=req.vehicle_model,
        component=req.component,
        dominant_freq=req.dominant_freq,
        freq_signature=req.freq_signature,
        audio_url=req.audio_url,
        source_user_id=current_user.id,
        source_type=current_user.role,
        tags=req.tags,
        session_id=req.session_id,
        status=MemoryStatus.PENDING,
        points_awarded=base_points,
        points_paid_at=datetime.utcnow(),
    )
    db.add(memory)

    # 포인트 지급
    await award_points(
        db, current_user.id, base_points,
        PointTxType.CONTRIBUTION,
        f"기억 제출 보상: {req.title[:30]}",
        memory_id
    )

    await db.commit()

    return {
        "success": True,
        "memory_id": memory_id,
        "message": "🧠 닥터브릉이가 새 기억을 받았어요! 감사합니다.",
        "points_awarded": base_points,
        "status": "pending",
        "next_step": "전문가 검증 완료 시 추가 포인트가 지급됩니다."
    }


@router.get("/list", summary="기억 목록 조회")
async def list_memories(
    memory_type: Optional[MemoryTag] = None,
    vehicle_brand: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    component: Optional[str] = None,
    status: Optional[MemoryStatus] = MemoryStatus.ACTIVE,
    tag: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at", enum=["created_at", "confidence", "helpful_count", "verification_count"]),
    db: AsyncSession = Depends(get_db),
):
    """
    🧠 기억 목록 조회 (필터링/정렬 지원)
    """
    query = select(BrainMemory)

    filters = []
    if memory_type:
        filters.append(BrainMemory.memory_type == memory_type)
    if vehicle_brand:
        filters.append(BrainMemory.vehicle_brand == vehicle_brand)
    if vehicle_type:
        filters.append(BrainMemory.vehicle_type == vehicle_type)
    if component:
        filters.append(BrainMemory.component == component)
    if status:
        filters.append(BrainMemory.status == status)
    if tag:
        filters.append(BrainMemory.tags.contains([tag]))

    if filters:
        query = query.where(and_(*filters))

    # 정렬
    sort_col = {
        "created_at": BrainMemory.created_at.desc(),
        "confidence": BrainMemory.confidence.desc(),
        "helpful_count": BrainMemory.helpful_count.desc(),
        "verification_count": BrainMemory.verification_count.desc(),
    }.get(sort_by, BrainMemory.created_at.desc())

    query = query.order_by(sort_col)

    # 페이지네이션
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    memories = result.scalars().all()

    # 총 개수
    count_query = select(func.count()).select_from(BrainMemory)
    if filters:
        count_query = count_query.where(and_(*filters))
    count_result = await db.execute(count_query)
    total = count_result.scalar()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "memories": [
            {
                "id": m.id,
                "memory_type": m.memory_type,
                "title": m.title,
                "content": m.content[:200] + "..." if len(m.content) > 200 else m.content,
                "vehicle_type": m.vehicle_type,
                "vehicle_brand": m.vehicle_brand,
                "component": m.component,
                "confidence": m.confidence,
                "verification_count": m.verification_count,
                "helpful_count": m.helpful_count,
                "status": m.status,
                "tags": m.tags or [],
                "dominant_freq": m.dominant_freq,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in memories
        ]
    }


@router.get("/search", summary="기억 검색")
async def search_memories(
    q: str = Query(..., description="검색어 (부품명, 증상, 차량 등)"),
    vehicle_brand: Optional[str] = None,
    memory_type: Optional[MemoryTag] = None,
    page: int = 1,
    per_page: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """
    🔍 전문 검색 — 제목, 내용, 태그 통합 검색
    """
    query = select(BrainMemory).where(
        BrainMemory.status == MemoryStatus.ACTIVE,
        or_(
            BrainMemory.title.ilike(f"%{q}%"),
            BrainMemory.content.ilike(f"%{q}%"),
            BrainMemory.component.ilike(f"%{q}%"),
            BrainMemory.vehicle_brand.ilike(f"%{q}%"),
            BrainMemory.vehicle_model.ilike(f"%{q}%"),
        )
    )

    if vehicle_brand:
        query = query.where(BrainMemory.vehicle_brand == vehicle_brand)
    if memory_type:
        query = query.where(BrainMemory.memory_type == memory_type)

    query = query.order_by(
        BrainMemory.helpful_count.desc(),
        BrainMemory.confidence.desc(),
    ).offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    memories = result.scalars().all()

    return {
        "query": q,
        "count": len(memories),
        "memories": [
            {
                "id": m.id,
                "memory_type": m.memory_type,
                "title": m.title,
                "content": m.content[:300],
                "vehicle_brand": m.vehicle_brand,
                "component": m.component,
                "confidence": m.confidence,
                "helpful_count": m.helpful_count,
                "tags": m.tags or [],
            }
            for m in memories
        ]
    }


@router.get("/stats", summary="메모리 통계")
async def get_memory_stats(db: AsyncSession = Depends(get_db)):
    """
    📊 브레인 메모리 전체 통계
    """
    total_result = await db.execute(select(func.count()).select_from(BrainMemory))
    total = total_result.scalar()

    active_result = await db.execute(
        select(func.count()).select_from(BrainMemory)
        .where(BrainMemory.status == MemoryStatus.ACTIVE)
    )
    active = active_result.scalar()

    # 유형별 통계
    type_stats = {}
    for tag in MemoryTag:
        cnt_result = await db.execute(
            select(func.count()).select_from(BrainMemory)
            .where(BrainMemory.memory_type == tag)
        )
        type_stats[tag.value] = cnt_result.scalar()

    # 차량 브랜드별 상위 5
    brand_result = await db.execute(
        select(BrainMemory.vehicle_brand, func.count().label("cnt"))
        .where(BrainMemory.vehicle_brand.isnot(None))
        .group_by(BrainMemory.vehicle_brand)
        .order_by(func.count().desc())
        .limit(5)
    )
    top_brands = [{"brand": r[0], "count": r[1]} for r in brand_result.all()]

    return {
        "total_memories": total,
        "active_memories": active,
        "by_type": type_stats,
        "top_vehicle_brands": top_brands,
        "summary": f"닥터브릉이가 {total:,}개의 기억을 보관 중입니다 🧠"
    }


@router.get("/{memory_id}", summary="기억 상세 조회")
async def get_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
):
    """특정 기억 상세 정보"""
    result = await db.execute(
        select(BrainMemory).where(BrainMemory.id == memory_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="기억을 찾을 수 없습니다.")

    # 조회수 증가 (구현 단순화를 위해 생략 가능)

    return {
        "id": memory.id,
        "memory_type": memory.memory_type,
        "title": memory.title,
        "content": memory.content,
        "structured_data": memory.structured_data,
        "vehicle_type": memory.vehicle_type,
        "vehicle_brand": memory.vehicle_brand,
        "vehicle_model": memory.vehicle_model,
        "component": memory.component,
        "dominant_freq": memory.dominant_freq,
        "freq_signature": memory.freq_signature,
        "audio_url": memory.audio_url,
        "source_type": memory.source_type,
        "confidence": memory.confidence,
        "verification_count": memory.verification_count,
        "helpful_count": memory.helpful_count,
        "status": memory.status,
        "tags": memory.tags or [],
        "points_awarded": memory.points_awarded,
        "is_public": memory.is_public,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
    }


@router.post("/{memory_id}/verify", summary="기억 검증")
async def verify_memory(
    memory_id: str,
    req: MemoryVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    ✅ 기억 검증 투표
    - 맞음/틀림 투표
    - 전문가는 검증 시 신뢰도에 더 큰 가중치
    - 검증 참여 포인트 지급
    """
    result = await db.execute(
        select(BrainMemory).where(BrainMemory.id == memory_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="기억을 찾을 수 없습니다.")

    # 이미 검증했는지 확인
    existing = await db.execute(
        select(MemoryVerification).where(
            MemoryVerification.memory_id == memory_id,
            MemoryVerification.verifier_id == current_user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 검증에 참여하셨습니다.")

    # 역할에 따른 가중치
    weight = 3.0 if current_user.role in ["trainer", "expert"] else 1.0
    confidence_delta = weight * 0.05 if req.is_correct else -(weight * 0.03)

    verification = MemoryVerification(
        id=str(uuid.uuid4()),
        memory_id=memory_id,
        verifier_id=current_user.id,
        verifier_role=current_user.role,
        is_correct=req.is_correct,
        correction_note=req.correction_note,
        points_earned=VERIFICATION_POINTS,
    )
    db.add(verification)

    # 기억 신뢰도 업데이트
    memory.verification_count += 1
    new_confidence = max(0.0, min(1.0, memory.confidence + confidence_delta))
    memory.confidence = new_confidence

    # 신뢰도 높으면 ACTIVE로 상태 변경
    if new_confidence >= 0.7 and memory.status == MemoryStatus.PENDING:
        memory.status = MemoryStatus.ACTIVE
        # 검증 완료 보너스 (제출자에게)
        if memory.source_user_id:
            await award_points(
                db, memory.source_user_id, 50,
                PointTxType.CONTRIBUTION_BONUS,
                f"기억 검증 완료 보너스: {memory.title[:30]}",
                memory_id
            )
            # 알림
            await send_notification(
                db, memory.source_user_id,
                "sound_verified",
                "🎉 기억이 검증되었어요!",
                f"'{memory.title}' 기억이 전문가에게 검증되었습니다. 보너스 포인트 50P 지급!",
                ref_id=memory_id, ref_type="brain_memory"
            )

    # 검증자에게 포인트
    await award_points(
        db, current_user.id, VERIFICATION_POINTS,
        PointTxType.CONTRIBUTION,
        "기억 검증 참여 보상",
        memory_id
    )

    await db.commit()

    return {
        "success": True,
        "memory_id": memory_id,
        "is_correct": req.is_correct,
        "new_confidence": round(new_confidence, 3),
        "points_earned": VERIFICATION_POINTS,
        "message": "✅ 검증 참여 감사합니다!" if req.is_correct else "⚠️ 수정 의견이 반영되었습니다."
    }


@router.post("/{memory_id}/helpful", summary="'도움됨' 투표")
async def vote_helpful(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """👍 이 기억이 도움됐어요 투표"""
    result = await db.execute(
        select(BrainMemory).where(BrainMemory.id == memory_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="기억을 찾을 수 없습니다.")

    memory.helpful_count += 1

    # 제출자에게 소액 포인트
    if memory.source_user_id and memory.source_user_id != current_user.id:
        await award_points(
            db, memory.source_user_id, HELPFUL_VOTE_POINTS,
            PointTxType.CONTRIBUTION,
            f"'도움됨' 투표 보상: {memory.title[:20]}",
            memory_id
        )

    await db.commit()

    return {
        "success": True,
        "helpful_count": memory.helpful_count,
        "message": "👍 감사합니다! 이 기억이 더 많은 사람에게 도움될 것입니다."
    }
