"""
🔊 Sound Report API — 소리 제보 시스템
친구들이 정상/고장 소리와 정보를 알려주는 채널

Endpoints:
  POST /api/v1/sounds/report          — 소리 제보 제출
  GET  /api/v1/sounds/reports         — 제보 목록
  GET  /api/v1/sounds/reports/{id}    — 제보 상세
  POST /api/v1/sounds/reports/{id}/helpful  — 도움됨 투표
  GET  /api/v1/sounds/my-reports      — 내 제보 목록
  POST /api/v1/sounds/reports/{id}/review  — (전문가) 제보 검토
  GET  /api/v1/sounds/leaderboard     — 기여 랭킹
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
import uuid

from app.db.database import get_db, User
from app.db.community_schema import (
    SoundReport, SoundReportType, BrainMemory, MemoryTag, MemoryStatus, Notification
)
from app.db.points_schema import PointWallet, PointTransaction, PointTxType
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/v1/sounds", tags=["🔊 Sound Reports"])


# ─── Pydantic Models ──────────────────────────────────────────

class SoundReportRequest(BaseModel):
    """소리 제보 요청"""
    report_type: SoundReportType
    title: str = Field(..., max_length=200, description="제보 제목 (예: '브레이크 밟을 때 끼익 소리')")
    description: str = Field(..., description="자세한 상황 설명")

    # 차량 정보
    vehicle_type: Optional[str] = None      # "sedan", "suv", "truck"
    vehicle_brand: Optional[str] = None     # "hyundai", "toyota"
    vehicle_model: Optional[str] = None     # "sonata", "camry"
    vehicle_year: Optional[int] = None
    mileage_km: Optional[int] = None

    # 소리 특성
    component: Optional[str] = None         # 어떤 부품인지 알면
    audio_url: Optional[str] = None         # 녹음 파일 URL
    audio_data: Optional[Dict] = None       # FFT 분석 결과
    dominant_freq: Optional[float] = None
    rms_amplitude: Optional[float] = None

    # 상황 정보
    when_does_it_happen: Optional[str] = None  # "엑셀 밟을 때", "저속에서"
    repair_history: Optional[str] = None
    is_repaired: bool = False
    repair_result: Optional[str] = None

    tags: List[str] = []


class ExpertReviewRequest(BaseModel):
    """전문가 제보 검토"""
    is_accepted: bool
    review_note: str
    component: Optional[str] = None
    correct_freq: Optional[float] = None
    correct_status: Optional[str] = None    # "normal"/"warning"/"critical"
    bonus_points: int = 0                   # 추가 보상 포인트


# ─── Points Config ────────────────────────────────────────────

REPORT_BASE_POINTS = {
    SoundReportType.NORMAL:        40,   # 정상 소리 제보
    SoundReportType.ABNORMAL:      80,   # 비정상 소리 제보 (더 희귀)
    SoundReportType.BEFORE_REPAIR: 60,   # 수리 전 소리
    SoundReportType.AFTER_REPAIR:  70,   # 수리 후 검증 소리 (매우 가치 있음)
    SoundReportType.UNKNOWN:       20,   # 모름
}
EXPERT_ACCEPT_BONUS = 100   # 전문가 채택 추가 보너스
HELPFUL_VOTE_REWARD = 5     # 도움됨 투표 받을 때마다


# ─── Helpers ─────────────────────────────────────────────────

async def award_points(db, user_id, amount, tx_type, description, ref_id):
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

    tx = PointTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tx_type=tx_type,
        amount=amount,
        balance_after=wallet.balance,
        ref_id=ref_id,
        ref_type="sound_report",
        description=description,
        note="앱 내 가상 재화 거래",
    )
    db.add(tx)


# ─── Endpoints ───────────────────────────────────────────────

@router.post("/report", summary="소리 제보 제출")
async def submit_sound_report(
    req: SoundReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    🔊 소리 제보를 제출합니다.

    닥터브릉이가 아직 모르는 소리를 알려주세요!
    - 정상 소리 / 고장 소리 모두 환영
    - 수리 전/후 비교 소리 특히 가치 있음
    - 제보 즉시 포인트 지급
    - 전문가 검증 완료 시 추가 보너스
    """
    report_id = str(uuid.uuid4())
    base_points = REPORT_BASE_POINTS.get(req.report_type, 25)

    # 오디오 URL 있으면 보너스
    if req.audio_url:
        base_points = int(base_points * 1.5)

    report = SoundReport(
        id=report_id,
        reporter_id=current_user.id,
        report_type=req.report_type,
        title=req.title,
        description=req.description,
        vehicle_type=req.vehicle_type,
        vehicle_brand=req.vehicle_brand,
        vehicle_model=req.vehicle_model,
        vehicle_year=req.vehicle_year,
        mileage_km=req.mileage_km,
        component=req.component,
        audio_url=req.audio_url,
        audio_data=req.audio_data,
        dominant_freq=req.dominant_freq,
        rms_amplitude=req.rms_amplitude,
        when_does_it_happen=req.when_does_it_happen,
        repair_history=req.repair_history,
        is_repaired=req.is_repaired,
        repair_result=req.repair_result,
        tags=req.tags,
        status="submitted",
        points_awarded=base_points,
    )
    db.add(report)

    # 포인트 지급
    await award_points(
        db, current_user.id, base_points,
        PointTxType.CONTRIBUTION,
        f"소리 제보 보상: {req.title[:30]}",
        report_id,
    )

    await db.commit()

    return {
        "success": True,
        "report_id": report_id,
        "message": "🔊 소리 정보를 알려주셔서 감사해요! 닥터브릉이가 열심히 배울게요.",
        "points_awarded": base_points,
        "bonus_info": "전문가 검증 완료 시 최대 100P 추가 지급됩니다.",
        "status": "submitted",
    }


@router.get("/reports", summary="제보 목록 조회")
async def list_reports(
    report_type: Optional[SoundReportType] = None,
    vehicle_brand: Optional[str] = None,
    component: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """📋 소리 제보 목록"""
    query = select(SoundReport)
    filters = []

    if report_type:
        filters.append(SoundReport.report_type == report_type)
    if vehicle_brand:
        filters.append(SoundReport.vehicle_brand == vehicle_brand)
    if component:
        filters.append(SoundReport.component == component)
    if status:
        filters.append(SoundReport.status == status)

    if filters:
        from sqlalchemy import and_
        query = query.where(and_(*filters))

    query = query.order_by(SoundReport.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    reports = result.scalars().all()

    count_q = select(func.count()).select_from(SoundReport)
    if filters:
        from sqlalchemy import and_
        count_q = count_q.where(and_(*filters))
    cnt = (await db.execute(count_q)).scalar()

    return {
        "total": cnt,
        "page": page,
        "reports": [
            {
                "id": r.id,
                "report_type": r.report_type,
                "title": r.title,
                "description": r.description[:150] + "..." if len(r.description) > 150 else r.description,
                "vehicle_brand": r.vehicle_brand,
                "vehicle_model": r.vehicle_model,
                "component": r.component,
                "has_audio": bool(r.audio_url),
                "status": r.status,
                "helpful_votes": r.helpful_votes,
                "tags": r.tags or [],
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ]
    }


@router.get("/my-reports", summary="내 제보 목록")
async def my_reports(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """📋 내가 제출한 소리 제보 목록"""
    result = await db.execute(
        select(SoundReport)
        .where(SoundReport.reporter_id == current_user.id)
        .order_by(SoundReport.created_at.desc())
        .limit(50)
    )
    reports = result.scalars().all()

    total_points = sum(r.points_awarded for r in reports)

    return {
        "total_reports": len(reports),
        "total_points_earned": total_points,
        "reports": [
            {
                "id": r.id,
                "report_type": r.report_type,
                "title": r.title,
                "status": r.status,
                "points_awarded": r.points_awarded,
                "helpful_votes": r.helpful_votes,
                "is_featured": r.is_featured,
                "memory_id": r.memory_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ]
    }


@router.get("/reports/{report_id}", summary="제보 상세 조회")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
):
    """제보 상세 정보"""
    result = await db.execute(
        select(SoundReport).where(SoundReport.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="제보를 찾을 수 없습니다.")

    report.view_count += 1
    await db.commit()

    return {
        "id": report.id,
        "report_type": report.report_type,
        "title": report.title,
        "description": report.description,
        "vehicle_type": report.vehicle_type,
        "vehicle_brand": report.vehicle_brand,
        "vehicle_model": report.vehicle_model,
        "vehicle_year": report.vehicle_year,
        "mileage_km": report.mileage_km,
        "component": report.component,
        "audio_url": report.audio_url,
        "dominant_freq": report.dominant_freq,
        "when_does_it_happen": report.when_does_it_happen,
        "repair_history": report.repair_history,
        "is_repaired": report.is_repaired,
        "repair_result": report.repair_result,
        "status": report.status,
        "review_note": report.review_note,
        "memory_id": report.memory_id,
        "helpful_votes": report.helpful_votes,
        "view_count": report.view_count,
        "points_awarded": report.points_awarded,
        "tags": report.tags or [],
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


@router.post("/reports/{report_id}/helpful", summary="'도움됨' 투표")
async def vote_helpful(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """👍 이 제보가 도움됐어요"""
    result = await db.execute(
        select(SoundReport).where(SoundReport.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="제보를 찾을 수 없습니다.")

    report.helpful_votes += 1

    # 제보자에게 소액 포인트
    if report.reporter_id != current_user.id:
        await award_points(
            db, report.reporter_id, HELPFUL_VOTE_REWARD,
            PointTxType.CONTRIBUTION,
            f"'도움됨' 투표 보상: {report.title[:20]}",
            report_id,
        )

    await db.commit()
    return {"success": True, "helpful_votes": report.helpful_votes}


@router.post("/reports/{report_id}/review", summary="[전문가] 제보 검토")
async def review_report(
    report_id: str,
    req: ExpertReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    👨‍🔧 전문가/트레이너가 제보를 검토합니다.
    채택 시 BrainMemory로 변환 + 추가 보너스 지급
    """
    if current_user.role not in ["trainer", "expert"]:
        raise HTTPException(status_code=403, detail="전문가만 제보를 검토할 수 있습니다.")

    result = await db.execute(
        select(SoundReport).where(SoundReport.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="제보를 찾을 수 없습니다.")

    if req.is_accepted:
        # BrainMemory로 변환
        memory_id = str(uuid.uuid4())
        memory_type = (
            MemoryTag.NORMAL_SOUND if report.report_type == SoundReportType.NORMAL
            else MemoryTag.FAULT_SOUND if report.report_type == SoundReportType.ABNORMAL
            else MemoryTag.USER_REPORT
        )

        memory = BrainMemory(
            id=memory_id,
            memory_type=memory_type,
            title=report.title,
            content=report.description,
            vehicle_type=report.vehicle_type,
            vehicle_brand=report.vehicle_brand,
            vehicle_model=report.vehicle_model,
            component=req.component or report.component,
            dominant_freq=req.correct_freq or report.dominant_freq,
            audio_url=report.audio_url,
            source_user_id=report.reporter_id,
            source_type="user_report",
            confidence=0.7,
            tags=report.tags or [],
            status=MemoryStatus.ACTIVE,
        )
        db.add(memory)

        report.status = "accepted"
        report.review_note = req.review_note
        report.memory_id = memory_id

        # 추가 보너스 지급
        bonus = EXPERT_ACCEPT_BONUS + req.bonus_points
        await award_points(
            db, report.reporter_id, bonus,
            PointTxType.CONTRIBUTION_BONUS,
            f"소리 제보 채택 보너스: {report.title[:30]}",
            report_id,
        )

        # 알림
        notif = Notification(
            id=str(uuid.uuid4()),
            user_id=report.reporter_id,
            notif_type="sound_verified",
            title="🎉 소리 제보가 채택되었어요!",
            body=f"'{report.title}' 제보가 전문가에게 채택되었습니다. 보너스 {bonus}P 지급!",
            ref_id=report_id,
            ref_type="sound_report",
        )
        db.add(notif)

    else:
        report.status = "rejected"
        report.review_note = req.review_note

    await db.commit()

    return {
        "success": True,
        "report_id": report_id,
        "is_accepted": req.is_accepted,
        "memory_id": report.memory_id if req.is_accepted else None,
        "bonus_awarded": (EXPERT_ACCEPT_BONUS + req.bonus_points) if req.is_accepted else 0,
        "message": "✅ 제보가 채택되어 브레인 메모리에 추가됐습니다!" if req.is_accepted else "제보가 반려되었습니다.",
    }


@router.get("/leaderboard", summary="기여 랭킹")
async def get_leaderboard(
    period: str = Query("all", enum=["all", "monthly"]),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    🏆 소리 기여 랭킹
    많이 기여한 친구들을 확인하세요!
    """
    query = (
        select(
            SoundReport.reporter_id,
            func.count().label("total_reports"),
            func.sum(SoundReport.points_awarded).label("total_points"),
            func.sum(SoundReport.helpful_votes).label("total_helpful"),
        )
        .where(SoundReport.status.in_(["submitted", "accepted"]))
        .group_by(SoundReport.reporter_id)
        .order_by(func.sum(SoundReport.points_awarded).desc())
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    leaderboard = []
    for rank, row in enumerate(rows, 1):
        # 사용자 정보 조회
        user_result = await db.execute(
            select(User).where(User.id == row.reporter_id)
        )
        user = user_result.scalar_one_or_none()
        leaderboard.append({
            "rank": rank,
            "user_id": row.reporter_id,
            "username": user.username if user else "Unknown",
            "total_reports": row.total_reports,
            "total_points": int(row.total_points or 0),
            "total_helpful_votes": int(row.total_helpful or 0),
        })

    return {
        "period": period,
        "leaderboard": leaderboard,
        "message": f"🏆 소리 기여 랭킹 Top {len(leaderboard)}"
    }
