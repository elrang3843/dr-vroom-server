"""
Dr. Vroom — 포인트 기반 현상금 API (Points-Only Bounty System)
══════════════════════════════════════════════════════════════

수수료 구조 (포인트 내부):
  전문가 실수령: 80%
  플랫폼 수수료: 15%
  보험 적립금:   5%
  세금:         없음 (포인트는 가상재화)

글로벌 설계:
  - 포인트 전용 → 환율/세금/금융규제 없음
  - 7일 이의신청 기간 → 에스크로 잠금
  - 마스터급 3인 중재
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.db.database import get_db, User
from app.db.points_schema import (
    BountyV2, BountyAnswerV2, PointEscrow, PointTransaction,
    DisputeV2, MediationVoteV2, SoundContribution,
    UserGradeV2, ExpertProfileV2,
    BountyStatus, EscrowStatus, ExpertTier, PointTxType, DisputeStatus
)
from app.services.points_service import (
    PointsService, PLATFORM_FEE_RATE, INSURANCE_FEE_RATE,
    EXPERT_NET_RATE, CONTRIBUTION_POINTS, VERIFICATION_BONUS,
    BOUNTY_MIN_POINTS
)
from app.api.auth import get_current_user
from app.services.ws_manager import ws_manager as manager

router = APIRouter(prefix="/api/v1/bounty", tags=["bounty"])

BOUNTY_EXPIRE_DAYS  = 7
DISPUTE_WINDOW_DAYS = 7


# ── Pydantic ─────────────────────────────────────────────────────

class BountyCreateReq(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10)
    vehicle_type: str
    vehicle_brand: str
    vehicle_model: str
    vehicle_year: int = Field(..., ge=1990, le=2030)
    mileage: Optional[int] = None
    sound_session_id: Optional[str] = None
    symptom_when: str
    symptom_location: str
    sound_tags: List[str] = []
    reward_points: int = Field(..., ge=BOUNTY_MIN_POINTS, le=50000)
    min_expert_tier: str = "apprentice"


class AnswerCreateReq(BaseModel):
    bounty_id: str
    diagnosis: str = Field(..., min_length=20)
    likely_cause: str
    repair_method: str
    estimated_cost: str
    urgency: str
    confidence: float = Field(..., ge=0.1, le=1.0)
    fault_code: Optional[str] = None
    freq_analysis: Optional[dict] = None


class AdoptReq(BaseModel):
    answer_id: str
    rating: int = Field(..., ge=1, le=5)
    rating_comment: Optional[str] = None


class DisputeReq(BaseModel):
    bounty_id: str
    answer_id: str
    reason: str = Field(..., min_length=20)
    evidence_urls: List[str] = []
    claimed_refund_pts: int = Field(0, ge=0)


class SoundContribReq(BaseModel):
    vehicle_brand: str
    vehicle_model: str
    vehicle_year: int
    mileage: Optional[int] = None
    engine_cc: Optional[int] = None
    country: str = "KR"
    sound_type: str
    component: str
    fault_code: Optional[str] = None
    description: str
    sound_session_id: str


# ── Fee Calculator ────────────────────────────────────────────────

def calc_fees(total_pts: int) -> dict:
    platform = int(total_pts * PLATFORM_FEE_RATE)
    insurance = int(total_pts * INSURANCE_FEE_RATE)
    expert_net = total_pts - platform - insurance
    return {
        "total": total_pts,
        "expert_net": expert_net,
        "platform_fee": platform,
        "insurance_fund": insurance,
        "breakdown": {
            "expert": f"{EXPERT_NET_RATE*100:.0f}%",
            "platform": f"{PLATFORM_FEE_RATE*100:.0f}%",
            "insurance": f"{INSURANCE_FEE_RATE*100:.0f}%",
            "tax": "없음 (가상재화)",
        },
        "global_note": "Points only — no currency, no tax withholding.",
    }


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/fee-calculator", summary="수수료 계산기")
async def fee_calculator(reward_points: int = 1000):
    """현상금 포인트 수수료 계산 — 전 세계 동일"""
    return {
        "input_points": reward_points,
        "distribution": calc_fees(reward_points),
        "legal_note": (
            "포인트는 앱 내 가상 재화입니다. "
            "현금 환전 없음, 세금 없음. "
            "Points are virtual goods — no cash conversion, no taxation."
        ),
    }


@router.post("/create", summary="현상금 게시 (포인트 예치)")
async def create_bounty(
    req: BountyCreateReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현상금 게시 — 포인트 에스크로 잠금"""
    # 등급 한도 체크
    grade_res = await db.execute(
        select(UserGradeV2).where(UserGradeV2.user_id == current_user.id)
    )
    grade = grade_res.scalar_one_or_none()
    if grade:
        from app.services.points_service import BOUNTY_MAX_POINTS, UserTier
        tier = grade.tier or UserTier.FREE
        max_pts = BOUNTY_MAX_POINTS.get(tier, 500)
        if req.reward_points > max_pts:
            raise HTTPException(400, f"현재 등급의 현상금 한도: {max_pts}P")

    try:
        expert_tier = ExpertTier(req.min_expert_tier)
    except ValueError:
        expert_tier = ExpertTier.APPRENTICE

    # 포인트 에스크로 잠금
    try:
        await PointsService.lock(
            db=db,
            user_id=current_user.id,
            amount=req.reward_points,
            ref_id="pending",  # will update after bounty created
            description=f"현상금 예치: {req.title[:40]} ({req.reward_points}P)",
        )
    except ValueError as e:
        raise HTTPException(402, str(e))

    bounty_id = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(days=BOUNTY_EXPIRE_DAYS)
    fees = calc_fees(req.reward_points)

    bounty = BountyV2(
        id=bounty_id,
        user_id=current_user.id,
        title=req.title,
        description=req.description,
        vehicle_type=req.vehicle_type,
        vehicle_brand=req.vehicle_brand,
        vehicle_model=req.vehicle_model,
        vehicle_year=req.vehicle_year,
        mileage=req.mileage,
        sound_session_id=req.sound_session_id,
        sound_tags=req.sound_tags,
        symptom_when=req.symptom_when,
        symptom_location=req.symptom_location,
        reward_points=req.reward_points,
        min_expert_tier=expert_tier,
        status=BountyStatus.OPEN,
        expires_at=expires_at,
    )
    db.add(bounty)

    escrow = PointEscrow(
        id=str(uuid.uuid4()),
        bounty_id=bounty_id,
        poster_id=current_user.id,
        total_points=req.reward_points,
        platform_fee_pts=fees["platform_fee"],
        insurance_fund_pts=fees["insurance_fund"],
        expert_net_pts=fees["expert_net"],
        status=EscrowStatus.HELD,
        release_after=expires_at + timedelta(days=DISPUTE_WINDOW_DAYS),
        dispute_deadline=expires_at + timedelta(days=DISPUTE_WINDOW_DAYS),
    )
    db.add(escrow)

    if grade:
        grade.total_bounties_posted += 1

    await db.commit()

    # 전문가에게 알림
    try:
        await manager.broadcast_to_role("trainer", {
            "event": "new_bounty",
            "bounty_id": bounty_id,
            "title": req.title,
            "vehicle": f"{req.vehicle_brand} {req.vehicle_model} ({req.vehicle_year})",
            "reward_points": req.reward_points,
            "min_expert_tier": req.min_expert_tier,
        })
    except Exception:
        pass

    return {
        "success": True,
        "bounty_id": bounty_id,
        "reward_points": req.reward_points,
        "escrow": fees,
        "expires_at": expires_at.isoformat(),
        "message": f"✅ 현상금 {req.reward_points}P 게시 완료! 에스크로에 안전히 보관됩니다.",
        "dispute_info": f"채택 후 {DISPUTE_WINDOW_DAYS}일 이내 이의신청 가능",
    }


@router.get("/list", summary="현상금 목록")
async def list_bounties(
    status_filter: str = "open",
    vehicle_brand: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    query = select(BountyV2).order_by(BountyV2.reward_points.desc())
    try:
        query = query.where(BountyV2.status == BountyStatus(status_filter))
    except ValueError:
        pass
    if vehicle_brand:
        query = query.where(BountyV2.vehicle_brand == vehicle_brand)
    query = query.limit(min(limit, 100))
    result = await db.execute(query)
    bounties = result.scalars().all()

    return [
        {
            "id": b.id,
            "title": b.title,
            "vehicle": f"{b.vehicle_brand} {b.vehicle_model} ({b.vehicle_year})",
            "symptom_when": b.symptom_when,
            "symptom_location": b.symptom_location,
            "sound_tags": b.sound_tags or [],
            "reward_points": b.reward_points,
            "expert_net_pts": int(b.reward_points * EXPERT_NET_RATE),
            "status": b.status.value if hasattr(b.status, "value") else b.status,
            "expires_at": b.expires_at.isoformat(),
            "view_count": b.view_count,
            "answer_count": b.answer_count,
            "min_expert_tier": b.min_expert_tier.value if hasattr(b.min_expert_tier, "value") else (b.min_expert_tier or "apprentice"),
        }
        for b in bounties
    ]


@router.get("/{bounty_id}", summary="현상금 상세")
async def get_bounty(bounty_id: str, db: AsyncSession = Depends(get_db)):
    b_res = await db.execute(select(BountyV2).where(BountyV2.id == bounty_id))
    b = b_res.scalar_one_or_none()
    if not b:
        raise HTTPException(404, "현상금을 찾을 수 없습니다.")

    b.view_count += 1
    await db.commit()

    e_res = await db.execute(select(PointEscrow).where(PointEscrow.bounty_id == bounty_id))
    e = e_res.scalar_one_or_none()

    return {
        "id": b.id,
        "title": b.title,
        "description": b.description,
        "vehicle_type": b.vehicle_type,
        "vehicle_brand": b.vehicle_brand,
        "vehicle_model": b.vehicle_model,
        "vehicle_year": b.vehicle_year,
        "mileage": b.mileage,
        "symptom_when": b.symptom_when,
        "symptom_location": b.symptom_location,
        "sound_tags": b.sound_tags or [],
        "reward_points": b.reward_points,
        "status": b.status.value if hasattr(b.status, "value") else b.status,
        "expires_at": b.expires_at.isoformat(),
        "view_count": b.view_count,
        "answer_count": b.answer_count,
        "fee_breakdown": calc_fees(b.reward_points),
        "escrow": {
            "status": e.status.value if e and hasattr(e.status, "value") else (e.status if e else None),
            "expert_net_pts": e.expert_net_pts if e else 0,
            "platform_fee_pts": e.platform_fee_pts if e else 0,
            "insurance_fund_pts": e.insurance_fund_pts if e else 0,
            "release_after": e.release_after.isoformat() if e and e.release_after else None,
        } if e else None,
    }


@router.post("/answer", summary="전문가 답변 제출")
async def submit_answer(
    req: AnswerCreateReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    b_res = await db.execute(select(BountyV2).where(BountyV2.id == req.bounty_id))
    b = b_res.scalar_one_or_none()
    if not b:
        raise HTTPException(404, "현상금을 찾을 수 없습니다.")
    if b.status not in [BountyStatus.OPEN, BountyStatus.ANSWERED]:
        raise HTTPException(400, "마감된 현상금입니다.")
    if b.user_id == current_user.id:
        raise HTTPException(400, "자신의 현상금에는 답변할 수 없습니다.")

    answer = BountyAnswerV2(
        id=str(uuid.uuid4()),
        bounty_id=req.bounty_id,
        expert_id=current_user.id,
        diagnosis=req.diagnosis,
        likely_cause=req.likely_cause,
        repair_method=req.repair_method,
        estimated_cost=req.estimated_cost,
        urgency=req.urgency,
        confidence=req.confidence,
        fault_code=req.fault_code,
        freq_analysis=req.freq_analysis,
    )
    db.add(answer)
    b.answer_count += 1
    b.status = BountyStatus.ANSWERED

    ep_res = await db.execute(
        select(ExpertProfileV2).where(ExpertProfileV2.user_id == current_user.id)
    )
    ep = ep_res.scalar_one_or_none()
    if ep:
        ep.total_answers += 1

    await db.commit()
    await db.refresh(answer)

    return {
        "success": True,
        "answer_id": answer.id,
        "bounty_id": req.bounty_id,
        "potential_reward_pts": int(b.reward_points * EXPERT_NET_RATE),
        "message": f"답변 제출 완료! 채택 시 {int(b.reward_points * EXPERT_NET_RATE)}P 수령",
        "fee_note": f"전문가 실수령: {EXPERT_NET_RATE*100:.0f}% (수수료·세금 없음)",
    }


@router.get("/{bounty_id}/answers", summary="답변 목록")
async def get_answers(bounty_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BountyAnswerV2)
        .where(BountyAnswerV2.bounty_id == bounty_id)
        .order_by(BountyAnswerV2.confidence.desc())
    )
    answers = result.scalars().all()
    return [
        {
            "id": a.id,
            "expert_id": a.expert_id,
            "diagnosis": a.diagnosis,
            "likely_cause": a.likely_cause,
            "repair_method": a.repair_method,
            "estimated_cost": a.estimated_cost,
            "urgency": a.urgency,
            "confidence": a.confidence,
            "fault_code": a.fault_code,
            "is_adopted": a.is_adopted,
            "rating": a.rating,
            "helpful_count": a.helpful_count,
            "created_at": a.created_at.isoformat(),
        }
        for a in answers
    ]


@router.post("/adopt", summary="답변 채택 + 포인트 자동 지급")
async def adopt_answer(
    req: AdoptReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    답변 채택 + 에스크로 자동 해제 + 전문가 포인트 지급
    수수료 (포인트 내부 공제): 플랫폼 15% + 보험 5% = 전문가 80%
    세금 없음 (포인트는 가상재화)
    """
    a_res = await db.execute(select(BountyAnswerV2).where(BountyAnswerV2.id == req.answer_id))
    answer = a_res.scalar_one_or_none()
    if not answer:
        raise HTTPException(404, "답변을 찾을 수 없습니다.")

    b_res = await db.execute(select(BountyV2).where(BountyV2.id == answer.bounty_id))
    bounty = b_res.scalar_one_or_none()
    if not bounty or bounty.user_id != current_user.id:
        raise HTTPException(403, "자신의 현상금만 채택 가능합니다.")
    if bounty.status == BountyStatus.ADOPTED:
        raise HTTPException(400, "이미 채택된 현상금입니다.")

    # 채택 처리
    answer.is_adopted = True
    answer.rating = req.rating
    answer.rating_comment = req.rating_comment
    answer.adopted_at = datetime.utcnow()

    bounty.status = BountyStatus.ADOPTED
    bounty.adopted_answer_id = answer.id
    bounty.adopted_at = datetime.utcnow()

    # 에스크로 해제 + 전문가 포인트 지급
    e_res = await db.execute(
        select(PointEscrow).where(PointEscrow.bounty_id == bounty.id)
    )
    escrow = e_res.scalar_one_or_none()

    payout = {"expert_net": 0, "platform_fee": 0, "insurance_fund": 0}
    if escrow:
        payout = await PointsService.release_escrow(
            db=db,
            escrow_id=escrow.id,
            poster_id=current_user.id,
            expert_id=answer.expert_id,
            total_points=escrow.total_points,
        )
        escrow.status = EscrowStatus.RELEASED
        escrow.expert_id = answer.expert_id
        escrow.released_at = datetime.utcnow()

    # 전문가 통계 업데이트
    ep_res = await db.execute(
        select(ExpertProfileV2).where(ExpertProfileV2.user_id == answer.expert_id)
    )
    ep = ep_res.scalar_one_or_none()
    if ep:
        ep.adopted_answers += 1
        if ep.total_answers > 0:
            ep.adoption_rate = ep.adopted_answers / ep.total_answers
        if req.rating:
            total_r = ep.avg_rating * ep.total_ratings + req.rating
            ep.total_ratings += 1
            ep.avg_rating = total_r / ep.total_ratings
        ep.updated_at = datetime.utcnow()

    await db.commit()

    # 전문가 알림
    try:
        await manager.broadcast_to_role("trainer", {
            "event": "answer_adopted",
            "answer_id": answer.id,
            "reward_pts": payout.get("expert_net", 0),
            "rating": req.rating,
        })
    except Exception:
        pass

    return {
        "success": True,
        "message": "✅ 채택 완료! 전문가에게 포인트가 자동 지급됩니다.",
        "payout": {
            "expert_net_pts": payout.get("expert_net", 0),
            "platform_fee_pts": payout.get("platform_fee", 0),
            "insurance_fund_pts": payout.get("insurance_fee", 0),
            "tax": "없음 (가상재화)",
            "dispute_deadline": (datetime.utcnow() + timedelta(days=DISPUTE_WINDOW_DAYS)).isoformat(),
            "global_note": "No currency, no tax — points only.",
        },
    }


@router.post("/contribute/sound", summary="소리 기여 + 포인트 적립")
async def contribute_sound(
    req: SoundContribReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """소리 데이터 기여 → 즉시 포인트 적립 (글로벌 동일)"""
    contrib = SoundContribution(
        id=str(uuid.uuid4()),
        contributor_id=current_user.id,
        vehicle_brand=req.vehicle_brand,
        vehicle_model=req.vehicle_model,
        vehicle_year=req.vehicle_year,
        mileage=req.mileage,
        engine_cc=req.engine_cc,
        country=req.country,
        sound_type=req.sound_type,
        component=req.component,
        fault_code=req.fault_code,
        description=req.description,
        sound_session_id=req.sound_session_id,
        points_awarded=CONTRIBUTION_POINTS,
    )
    db.add(contrib)

    await PointsService.earn(
        db=db,
        user_id=current_user.id,
        amount=CONTRIBUTION_POINTS,
        tx_type=PointTxType.CONTRIBUTION,
        ref_id=contrib.id,
        ref_type="sound_contribution",
        description=f"소리 기여 보상: {req.sound_type}/{req.component} +{CONTRIBUTION_POINTS}P",
    )

    # 등급 업데이트
    grade_res = await db.execute(
        select(UserGradeV2).where(UserGradeV2.user_id == current_user.id)
    )
    grade = grade_res.scalar_one_or_none()
    if grade:
        grade.sound_contributions += 1

    await db.commit()

    return {
        "success": True,
        "contribution_id": contrib.id,
        "points_awarded": CONTRIBUTION_POINTS,
        "verification_bonus": VERIFICATION_BONUS,
        "message": f"🎵 +{CONTRIBUTION_POINTS}P 적립! 전문가 검증 통과 시 +{VERIFICATION_BONUS}P 추가 예정",
        "country": req.country,
        "global_note": "Points awarded globally — same for all countries.",
    }


@router.post("/dispute", summary="이의신청")
async def create_dispute(
    req: DisputeReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """채택 후 7일 이내 이의신청 — 마스터급 3인 중재"""
    b_res = await db.execute(select(BountyV2).where(BountyV2.id == req.bounty_id))
    b = b_res.scalar_one_or_none()
    if not b:
        raise HTTPException(404, "현상금을 찾을 수 없습니다.")

    if b.adopted_at:
        deadline = b.adopted_at + timedelta(days=DISPUTE_WINDOW_DAYS)
        if datetime.utcnow() > deadline:
            raise HTTPException(400, f"이의신청 기한 초과 (채택 후 {DISPUTE_WINDOW_DAYS}일)")

    a_res = await db.execute(
        select(BountyAnswerV2).where(BountyAnswerV2.id == req.answer_id)
    )
    answer = a_res.scalar_one_or_none()
    if not answer:
        raise HTTPException(404, "답변을 찾을 수 없습니다.")

    dispute = DisputeV2(
        id=str(uuid.uuid4()),
        bounty_id=req.bounty_id,
        answer_id=req.answer_id,
        complainant_id=current_user.id,
        respondent_id=answer.expert_id,
        reason=req.reason,
        evidence_urls=req.evidence_urls,
        claimed_refund_pts=req.claimed_refund_pts,
        status=DisputeStatus.PENDING,
        deadline=datetime.utcnow() + timedelta(days=DISPUTE_WINDOW_DAYS),
    )
    db.add(dispute)

    # 에스크로 동결
    e_res = await db.execute(
        select(PointEscrow).where(PointEscrow.bounty_id == req.bounty_id)
    )
    e = e_res.scalar_one_or_none()
    if e:
        e.status = EscrowStatus.DISPUTED

    b.status = BountyStatus.DISPUTED
    await db.commit()

    return {
        "success": True,
        "dispute_id": dispute.id,
        "status": "pending",
        "message": "이의신청 접수. 마스터급 전문가 3인이 중재합니다.",
        "process": [
            "1. 에스크로 포인트 동결",
            "2. 마스터급 전문가 3인 무작위 배정",
            "3. 독립적 검토 후 다수결 판정",
            "4. 승소측 포인트 지급, 패소측 페널티",
        ],
        "deadline": dispute.deadline.isoformat(),
    }


@router.get("/wallet/me", summary="내 포인트 지갑")
async def my_wallet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """포인트 잔액 및 거래 내역"""
    balance = await PointsService.get_balance(current_user.id, db)
    await db.commit()

    # 최근 거래 10건
    tx_res = await db.execute(
        select(PointTransaction)
        .where(PointTransaction.user_id == current_user.id)
        .order_by(PointTransaction.created_at.desc())
        .limit(10)
    )
    txns = tx_res.scalars().all()

    return {
        "wallet": balance,
        "currency": "points",
        "legal_note": "포인트는 앱 내 가상 재화입니다. 현금이 아닙니다.",
        "recent_transactions": [
            {
                "id": t.id,
                "type": t.tx_type.value if hasattr(t.tx_type, "value") else t.tx_type,
                "amount": t.amount,
                "balance_after": t.balance_after,
                "description": t.description,
                "created_at": t.created_at.isoformat(),
            }
            for t in txns
        ],
    }
