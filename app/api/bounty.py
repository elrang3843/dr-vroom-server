"""
Dr. Vroom — 현상금 & 에스크로 API
Bounty & Escrow System

수수료 구조:
  플랫폼 수수료: 15%
  보험 기여금:   5%
  원천징수세:    3.3% (전문가 수입에 대해)
  전문가 실수령: 76.7% (100 - 15 - 5 - 3.3)

법적 근거:
  - 소득세법 제21조 (기타소득 원천징수 3.3%)
  - 전자금융거래법 (에스크로 의무)
  - 전자상거래법 (이용약관, 환불 정책)
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.db.database import get_db, User
from app.db.ecosystem_schema import (
    Bounty, BountyAnswer, Escrow, Transaction, Dispute, MediationVote,
    UserGrade, ExpertProfile, SoundContribution,
    BountyStatus, EscrowStatus, ExpertTier, TransactionType, DisputeStatus
)
from app.core.auth import get_current_user
from app.services.ws_manager import manager

router = APIRouter(prefix="/api/v1/bounty", tags=["bounty"])


# ── Fee constants (법적 수수료 구조) ───────────────────────────
PLATFORM_FEE_RATE  = 0.15   # 플랫폼 수수료 15%
INSURANCE_FEE_RATE = 0.05   # 보험 기여금 5%
TAX_RATE           = 0.033  # 원천징수 3.3%
ESCROW_HOLD_DAYS   = 7      # 에스크로 보관 기간 (이의신청 기한)
BOUNTY_EXPIRE_DAYS = 7      # 현상금 만료 기간


# ── Pydantic Schemas ───────────────────────────────────────────

class BountyCreateRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10)
    vehicle_type: str
    vehicle_brand: str
    vehicle_model: str
    vehicle_year: int = Field(..., ge=1990, le=2030)
    mileage: Optional[int] = None
    sound_session_id: Optional[str] = None
    symptom_when: str   # startup/acceleration/braking/always/other
    symptom_location: str   # front/rear/engine/wheel/unknown
    sound_tags: List[str] = []
    reward_points: int = Field(0, ge=0, le=100000)
    reward_krw: int = Field(0, ge=0, le=1000000)
    min_expert_tier: str = "apprentice"


class BountyResponse(BaseModel):
    id: str
    title: str
    description: str
    vehicle_type: str
    vehicle_brand: str
    vehicle_model: str
    vehicle_year: int
    symptom_when: str
    symptom_location: str
    sound_tags: List[str]
    reward_points: int
    reward_krw: int
    status: str
    expires_at: datetime
    created_at: datetime
    view_count: int
    answer_count: int
    min_expert_tier: str


class AnswerCreateRequest(BaseModel):
    bounty_id: str
    diagnosis: str = Field(..., min_length=20)
    likely_cause: str
    repair_method: str
    estimated_cost: str
    urgency: str   # "즉시수리" / "주의관찰" / "정기점검"
    confidence: float = Field(..., ge=0.1, le=1.0)
    fault_code: Optional[str] = None
    freq_analysis: Optional[dict] = None


class AnswerResponse(BaseModel):
    id: str
    bounty_id: str
    expert_id: str
    diagnosis: str
    likely_cause: str
    repair_method: str
    estimated_cost: str
    urgency: str
    confidence: float
    fault_code: Optional[str]
    is_adopted: bool
    rating: Optional[int]
    helpful_count: int
    created_at: datetime


class AdoptAnswerRequest(BaseModel):
    answer_id: str
    rating: int = Field(..., ge=1, le=5)
    rating_comment: Optional[str] = None


class DisputeCreateRequest(BaseModel):
    bounty_id: str
    answer_id: str
    reason: str = Field(..., min_length=20)
    evidence_urls: List[str] = []
    claimed_refund: int = Field(0, ge=0)


class SoundContributionRequest(BaseModel):
    vehicle_brand: str
    vehicle_model: str
    vehicle_year: int
    mileage: Optional[int] = None
    engine_cc: Optional[int] = None
    sound_type: str   # "normal" / "fault"
    component: str
    fault_code: Optional[str] = None
    description: str
    sound_session_id: str


# ── Fee Calculator ─────────────────────────────────────────────

def calculate_escrow_distribution(total_krw: int, total_points: int) -> dict:
    """
    수수료 및 세금 계산
    Returns dict with breakdown of amounts
    """
    platform_fee_krw = int(total_krw * PLATFORM_FEE_RATE)
    insurance_fee_krw = int(total_krw * INSURANCE_FEE_RATE)
    remaining_krw = total_krw - platform_fee_krw - insurance_fee_krw
    tax_krw = int(remaining_krw * TAX_RATE)
    expert_net_krw = remaining_krw - tax_krw

    platform_fee_pts = int(total_points * PLATFORM_FEE_RATE)
    insurance_fee_pts = int(total_points * INSURANCE_FEE_RATE)
    remaining_pts = total_points - platform_fee_pts - insurance_fee_pts
    expert_net_pts = int(remaining_pts * (1 - TAX_RATE))

    return {
        "total_krw": total_krw,
        "total_points": total_points,
        "platform_fee_krw": platform_fee_krw,
        "insurance_fee_krw": insurance_fee_krw,
        "tax_withheld_krw": tax_krw,
        "expert_net_krw": expert_net_krw,
        "expert_net_points": expert_net_pts,
        "breakdown_pct": {
            "expert_net": f"{(1 - PLATFORM_FEE_RATE - INSURANCE_FEE_RATE) * (1 - TAX_RATE) * 100:.1f}%",
            "platform_fee": f"{PLATFORM_FEE_RATE * 100:.0f}%",
            "insurance_contribution": f"{INSURANCE_FEE_RATE * 100:.0f}%",
            "tax_withholding": f"{TAX_RATE * 100:.1f}%",
        }
    }


# ── Bounty Endpoints ───────────────────────────────────────────

@router.post("/create", response_model=BountyResponse, summary="현상금 게시")
async def create_bounty(
    req: BountyCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현상금 게시 — 포인트 또는 현금 보상 예치"""
    # Validate reward
    if req.reward_krw == 0 and req.reward_points == 0:
        raise HTTPException(status_code=400, detail="현상금을 설정해 주세요 (포인트 또는 원화).")

    # Check user grade for bounty limits
    grade_res = await db.execute(
        select(UserGrade).where(UserGrade.user_id == current_user.id)
    )
    grade = grade_res.scalar_one_or_none()

    if grade and req.reward_krw > 0:
        from app.api.grades import TIER_BENEFITS, UserTier
        user_tier = grade.tier or UserTier.SPROUT
        max_bounty = TIER_BENEFITS[user_tier]["max_bounty_krw"]
        if req.reward_krw > max_bounty:
            raise HTTPException(
                status_code=400,
                detail=f"현재 등급({user_tier.value})의 최대 현상금 한도: {max_bounty:,}원"
            )

    try:
        expert_tier = ExpertTier(req.min_expert_tier)
    except ValueError:
        expert_tier = ExpertTier.APPRENTICE

    bounty_id = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(days=BOUNTY_EXPIRE_DAYS)

    bounty = Bounty(
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
        reward_krw=req.reward_krw,
        min_expert_tier=expert_tier,
        status=BountyStatus.OPEN,
        expires_at=expires_at,
    )
    db.add(bounty)

    # Create escrow
    dist = calculate_escrow_distribution(req.reward_krw, req.reward_points)
    escrow = Escrow(
        id=str(uuid.uuid4()),
        bounty_id=bounty_id,
        user_id=current_user.id,
        total_amount_krw=req.reward_krw,
        total_amount_points=req.reward_points,
        platform_fee_rate=PLATFORM_FEE_RATE,
        insurance_fee_rate=INSURANCE_FEE_RATE,
        tax_rate=TAX_RATE,
        expert_net_krw=dist["expert_net_krw"],
        expert_net_points=dist["expert_net_points"],
        platform_fee_krw=dist["platform_fee_krw"],
        insurance_pool_krw=dist["insurance_fee_krw"],
        tax_withheld_krw=dist["tax_withheld_krw"],
        status=EscrowStatus.HELD,
        held_at=datetime.utcnow(),
        release_after=expires_at + timedelta(days=ESCROW_HOLD_DAYS),
        dispute_deadline=expires_at + timedelta(days=ESCROW_HOLD_DAYS),
    )
    db.add(escrow)

    # Transaction record
    if req.reward_krw > 0:
        txn = Transaction(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            related_id=bounty_id,
            type=TransactionType.BOUNTY_DEPOSIT,
            amount_krw=-req.reward_krw,
            amount_points=-req.reward_points,
            description=f"현상금 예치: {req.title[:50]}",
            receipt_number=f"DR{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{bounty_id[:8].upper()}",
        )
        db.add(txn)

    # Update grade stats
    if grade:
        grade.total_bounties_posted += 1

    await db.commit()

    # Notify experts via WebSocket
    await manager.broadcast_to_role("trainer", {
        "event": "new_bounty",
        "bounty_id": bounty_id,
        "title": req.title,
        "vehicle": f"{req.vehicle_brand} {req.vehicle_model} ({req.vehicle_year})",
        "reward_krw": req.reward_krw,
        "reward_points": req.reward_points,
        "min_expert_tier": req.min_expert_tier,
    })

    return BountyResponse(
        id=bounty_id,
        title=req.title,
        description=req.description,
        vehicle_type=req.vehicle_type,
        vehicle_brand=req.vehicle_brand,
        vehicle_model=req.vehicle_model,
        vehicle_year=req.vehicle_year,
        symptom_when=req.symptom_when,
        symptom_location=req.symptom_location,
        sound_tags=req.sound_tags or [],
        reward_points=req.reward_points,
        reward_krw=req.reward_krw,
        status=BountyStatus.OPEN.value,
        expires_at=expires_at,
        created_at=datetime.utcnow(),
        view_count=0,
        answer_count=0,
        min_expert_tier=expert_tier.value,
    )


@router.get("/list", response_model=List[BountyResponse], summary="현상금 목록")
async def list_bounties(
    status_filter: Optional[str] = "open",
    vehicle_brand: Optional[str] = None,
    component: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """공개 현상금 목록 조회"""
    query = select(Bounty).order_by(Bounty.created_at.desc())

    if status_filter:
        try:
            sf = BountyStatus(status_filter)
            query = query.where(Bounty.status == sf)
        except ValueError:
            pass

    if vehicle_brand:
        query = query.where(Bounty.vehicle_brand == vehicle_brand)

    query = query.limit(min(limit, 100))
    result = await db.execute(query)
    bounties = result.scalars().all()

    return [
        BountyResponse(
            id=b.id,
            title=b.title,
            description=b.description,
            vehicle_type=b.vehicle_type,
            vehicle_brand=b.vehicle_brand,
            vehicle_model=b.vehicle_model,
            vehicle_year=b.vehicle_year,
            symptom_when=b.symptom_when,
            symptom_location=b.symptom_location,
            sound_tags=b.sound_tags or [],
            reward_points=b.reward_points,
            reward_krw=b.reward_krw,
            status=b.status.value if hasattr(b.status, 'value') else b.status,
            expires_at=b.expires_at,
            created_at=b.created_at,
            view_count=b.view_count,
            answer_count=b.answer_count,
            min_expert_tier=b.min_expert_tier.value if hasattr(b.min_expert_tier, 'value') else (b.min_expert_tier or "apprentice"),
        )
        for b in bounties
    ]


@router.get("/{bounty_id}", summary="현상금 상세")
async def get_bounty(
    bounty_id: str,
    db: AsyncSession = Depends(get_db),
):
    """현상금 상세 + 에스크로 분배 정보 조회"""
    bounty_res = await db.execute(select(Bounty).where(Bounty.id == bounty_id))
    bounty = bounty_res.scalar_one_or_none()
    if not bounty:
        raise HTTPException(status_code=404, detail="현상금을 찾을 수 없습니다.")

    # Increment view
    bounty.view_count += 1
    await db.commit()

    # Escrow info
    escrow_res = await db.execute(select(Escrow).where(Escrow.bounty_id == bounty_id))
    escrow = escrow_res.scalar_one_or_none()

    escrow_info = None
    if escrow:
        escrow_info = {
            "status": escrow.status.value if hasattr(escrow.status, 'value') else escrow.status,
            "total_krw": escrow.total_amount_krw,
            "expert_net_krw": escrow.expert_net_krw,
            "platform_fee_krw": escrow.platform_fee_krw,
            "insurance_pool_krw": escrow.insurance_pool_krw,
            "tax_withheld_krw": escrow.tax_withheld_krw,
            "release_after": escrow.release_after.isoformat() if escrow.release_after else None,
        }

    return {
        "bounty": {
            "id": bounty.id,
            "title": bounty.title,
            "description": bounty.description,
            "vehicle": f"{bounty.vehicle_brand} {bounty.vehicle_model} ({bounty.vehicle_year})",
            "mileage": bounty.mileage,
            "symptom_when": bounty.symptom_when,
            "symptom_location": bounty.symptom_location,
            "sound_tags": bounty.sound_tags or [],
            "reward_points": bounty.reward_points,
            "reward_krw": bounty.reward_krw,
            "status": bounty.status.value if hasattr(bounty.status, 'value') else bounty.status,
            "expires_at": bounty.expires_at.isoformat(),
            "created_at": bounty.created_at.isoformat(),
            "view_count": bounty.view_count,
            "answer_count": bounty.answer_count,
        },
        "escrow": escrow_info,
        "fee_breakdown": calculate_escrow_distribution(bounty.reward_krw, bounty.reward_points),
    }


# ── Answer Endpoints ───────────────────────────────────────────

@router.post("/answer", response_model=AnswerResponse, summary="전문가 답변 제출")
async def submit_answer(
    req: AnswerCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """전문가가 현상금 게시글에 답변 (특허 기반 FFT 분석 결과 포함 권장)"""
    # Check bounty exists and is open
    bounty_res = await db.execute(select(Bounty).where(Bounty.id == req.bounty_id))
    bounty = bounty_res.scalar_one_or_none()
    if not bounty:
        raise HTTPException(status_code=404, detail="현상금을 찾을 수 없습니다.")
    if bounty.status != BountyStatus.OPEN:
        raise HTTPException(status_code=400, detail="이미 마감된 현상금입니다.")
    if bounty.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="자신의 현상금에는 답변할 수 없습니다.")

    # Check expert tier requirement
    expert_res = await db.execute(
        select(ExpertProfile).where(ExpertProfile.user_id == current_user.id)
    )
    expert = expert_res.scalar_one_or_none()

    if bounty.min_expert_tier and bounty.min_expert_tier != ExpertTier.APPRENTICE:
        tier_order = [ExpertTier.APPRENTICE, ExpertTier.CERTIFIED, ExpertTier.MASTER, ExpertTier.PARTNER]
        required_idx = tier_order.index(bounty.min_expert_tier)
        current_expert_tier = expert.tier if expert else ExpertTier.APPRENTICE
        current_idx = tier_order.index(current_expert_tier)
        if current_idx < required_idx:
            raise HTTPException(
                status_code=403,
                detail=f"이 현상금은 {bounty.min_expert_tier.value} 이상 전문가만 답변 가능합니다."
            )

    answer = BountyAnswer(
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
    bounty.answer_count += 1
    bounty.status = BountyStatus.ANSWERED

    if expert:
        expert.total_answers += 1
        expert.monthly_answers += 1

    await db.commit()
    await db.refresh(answer)

    # Notify bounty owner
    await manager.broadcast_to_role("client", {
        "event": "new_answer",
        "bounty_id": req.bounty_id,
        "answer_id": answer.id,
        "expert_id": current_user.id,
        "urgency": req.urgency,
    })

    return AnswerResponse(
        id=answer.id,
        bounty_id=answer.bounty_id,
        expert_id=answer.expert_id,
        diagnosis=answer.diagnosis,
        likely_cause=answer.likely_cause,
        repair_method=answer.repair_method,
        estimated_cost=answer.estimated_cost,
        urgency=answer.urgency,
        confidence=answer.confidence,
        fault_code=answer.fault_code,
        is_adopted=False,
        rating=None,
        helpful_count=0,
        created_at=answer.created_at,
    )


@router.get("/{bounty_id}/answers", response_model=List[AnswerResponse], summary="답변 목록")
async def get_answers(
    bounty_id: str,
    db: AsyncSession = Depends(get_db),
):
    """현상금 게시글의 전문가 답변 목록"""
    result = await db.execute(
        select(BountyAnswer)
        .where(BountyAnswer.bounty_id == bounty_id)
        .order_by(BountyAnswer.confidence.desc())
    )
    answers = result.scalars().all()
    return [
        AnswerResponse(
            id=a.id,
            bounty_id=a.bounty_id,
            expert_id=a.expert_id,
            diagnosis=a.diagnosis,
            likely_cause=a.likely_cause,
            repair_method=a.repair_method,
            estimated_cost=a.estimated_cost,
            urgency=a.urgency,
            confidence=a.confidence,
            fault_code=a.fault_code,
            is_adopted=a.is_adopted,
            rating=a.rating,
            helpful_count=a.helpful_count,
            created_at=a.created_at,
        )
        for a in answers
    ]


@router.post("/adopt", summary="답변 채택 + 자동 보상 지급")
async def adopt_answer(
    req: AdoptAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    답변 채택 및 에스크로 보상 자동 지급
    
    처리 순서:
    1. 답변 채택 표시
    2. 에스크로 → 전문가 지급 처리
    3. 수수료/세금 분리
    4. 7일 후 자동 확정 (이의신청 기간)
    5. 전문가 통계 업데이트
    """
    # Verify answer
    answer_res = await db.execute(select(BountyAnswer).where(BountyAnswer.id == req.answer_id))
    answer = answer_res.scalar_one_or_none()
    if not answer:
        raise HTTPException(status_code=404, detail="답변을 찾을 수 없습니다.")

    # Verify bounty ownership
    bounty_res = await db.execute(select(Bounty).where(Bounty.id == answer.bounty_id))
    bounty = bounty_res.scalar_one_or_none()
    if not bounty or bounty.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="자신의 현상금 답변만 채택할 수 있습니다.")

    if bounty.status == BountyStatus.ADOPTED:
        raise HTTPException(status_code=400, detail="이미 채택된 현상금입니다.")

    # Update answer
    answer.is_adopted = True
    answer.rating = req.rating
    answer.rating_comment = req.rating_comment
    answer.adopted_at = datetime.utcnow()

    # Update bounty
    bounty.status = BountyStatus.ADOPTED
    bounty.adopted_answer_id = answer.id
    bounty.adopted_at = datetime.utcnow()

    # Update escrow
    escrow_res = await db.execute(select(Escrow).where(Escrow.bounty_id == bounty.id))
    escrow = escrow_res.scalar_one_or_none()
    payout_info = {"expert_net_krw": 0, "expert_net_points": 0}

    if escrow:
        escrow.status = EscrowStatus.RELEASED
        escrow.expert_id = answer.expert_id
        escrow.released_at = datetime.utcnow()
        payout_info = {
            "expert_net_krw": escrow.expert_net_krw,
            "expert_net_points": escrow.expert_net_points,
        }

        # Create reward transaction
        txn = Transaction(
            id=str(uuid.uuid4()),
            user_id=answer.expert_id,
            related_id=escrow.id,
            type=TransactionType.REWARD_PAYMENT,
            amount_krw=escrow.expert_net_krw,
            amount_points=escrow.expert_net_points,
            description=f"현상금 채택 보상: {bounty.title[:50]}",
            receipt_number=f"RW{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{answer.id[:8].upper()}",
        )
        db.add(txn)

        # Platform fee transaction
        fee_txn = Transaction(
            id=str(uuid.uuid4()),
            user_id=answer.expert_id,
            related_id=escrow.id,
            type=TransactionType.PLATFORM_FEE,
            amount_krw=-escrow.platform_fee_krw,
            description=f"플랫폼 수수료 15%: {escrow.platform_fee_krw:,}원",
        )
        db.add(fee_txn)

        # Tax transaction
        if escrow.tax_withheld_krw > 0:
            tax_txn = Transaction(
                id=str(uuid.uuid4()),
                user_id=answer.expert_id,
                related_id=escrow.id,
                type=TransactionType.TAX_WITHHOLD,
                amount_krw=-escrow.tax_withheld_krw,
                description=f"원천징수 3.3%: {escrow.tax_withheld_krw:,}원",
            )
            db.add(tax_txn)

    # Update expert stats
    expert_res = await db.execute(
        select(ExpertProfile).where(ExpertProfile.user_id == answer.expert_id)
    )
    expert = expert_res.scalar_one_or_none()
    if expert:
        expert.adopted_answers += 1
        expert.monthly_adopted += 1
        expert.total_earned_krw += payout_info["expert_net_krw"]
        expert.monthly_earned += payout_info["expert_net_krw"]

        # Update adoption rate
        if expert.total_answers > 0:
            expert.adoption_rate = expert.adopted_answers / expert.total_answers

        # Update rating
        if req.rating:
            total_rating = expert.avg_rating * expert.total_ratings + req.rating
            expert.total_ratings += 1
            expert.avg_rating = total_rating / expert.total_ratings

    await db.commit()

    # Notify expert
    await manager.broadcast_to_role("trainer", {
        "event": "answer_adopted",
        "answer_id": answer.id,
        "expert_id": answer.expert_id,
        "reward_krw": payout_info["expert_net_krw"],
        "reward_points": payout_info["expert_net_points"],
        "rating": req.rating,
    })

    return {
        "success": True,
        "message": "✅ 답변이 채택되었습니다! 전문가에게 보상이 자동 지급됩니다.",
        "bounty_id": bounty.id,
        "answer_id": answer.id,
        "payout": {
            **payout_info,
            "dispute_deadline": (datetime.utcnow() + timedelta(days=ESCROW_HOLD_DAYS)).isoformat(),
            "note": f"채택 후 {ESCROW_HOLD_DAYS}일 이내 이의신청 가능 (전자상거래법 준수)",
        },
        "tax_info": {
            "withheld": escrow.tax_withheld_krw if escrow else 0,
            "rate": "3.3%",
            "legal_basis": "소득세법 제21조 (기타소득 원천징수)",
        }
    }


# ── Sound Contribution ─────────────────────────────────────────

@router.post("/contribute/sound", summary="소리 데이터 기여 + 포인트 적립")
async def contribute_sound(
    req: SoundContributionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    정상/고장 소리 데이터를 기여하고 포인트를 적립합니다.
    
    포인트 지급:
      - 소리 제출: +10 포인트
      - 전문가 검증 통과: +40 포인트 (추후 지급)
    """
    contribution = SoundContribution(
        id=str(uuid.uuid4()),
        contributor_id=current_user.id,
        vehicle_brand=req.vehicle_brand,
        vehicle_model=req.vehicle_model,
        vehicle_year=req.vehicle_year,
        mileage=req.mileage,
        engine_cc=req.engine_cc,
        sound_type=req.sound_type,
        component=req.component,
        fault_code=req.fault_code,
        description=req.description,
        sound_session_id=req.sound_session_id,
        verification_status="pending",
        points_awarded=10,
    )
    db.add(contribution)

    # Award points
    grade_res = await db.execute(
        select(UserGrade).where(UserGrade.user_id == current_user.id)
    )
    grade = grade_res.scalar_one_or_none()
    if not grade:
        grade = UserGrade(user_id=current_user.id)
        db.add(grade)

    grade.points += 10
    grade.lifetime_points += 10
    grade.sound_contributions += 1

    await db.commit()

    return {
        "success": True,
        "contribution_id": contribution.id,
        "points_awarded": 10,
        "message": "🎵 소리 데이터 기여 감사합니다! +10 포인트가 적립되었습니다.",
        "pending_bonus": "전문가 검증 통과 시 +40 포인트 추가 예정",
        "sound_type": req.sound_type,
        "component": req.component,
    }


# ── Dispute ────────────────────────────────────────────────────

@router.post("/dispute", summary="이의신청")
async def create_dispute(
    req: DisputeCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    채택 후 7일 이내 이의신청 가능
    마스터급 전문가 3인이 중재
    """
    bounty_res = await db.execute(select(Bounty).where(Bounty.id == req.bounty_id))
    bounty = bounty_res.scalar_one_or_none()
    if not bounty:
        raise HTTPException(status_code=404, detail="현상금을 찾을 수 없습니다.")

    # Check dispute window
    if bounty.adopted_at:
        dispute_deadline = bounty.adopted_at + timedelta(days=ESCROW_HOLD_DAYS)
        if datetime.utcnow() > dispute_deadline:
            raise HTTPException(
                status_code=400,
                detail=f"이의신청 기한이 지났습니다. (채택 후 {ESCROW_HOLD_DAYS}일 이내만 가능)"
            )

    # Find respondent
    answer_res = await db.execute(select(BountyAnswer).where(BountyAnswer.id == req.answer_id))
    answer = answer_res.scalar_one_or_none()
    if not answer:
        raise HTTPException(status_code=404, detail="답변을 찾을 수 없습니다.")

    dispute = Dispute(
        id=str(uuid.uuid4()),
        bounty_id=req.bounty_id,
        answer_id=req.answer_id,
        complainant_id=current_user.id,
        respondent_id=answer.expert_id,
        reason=req.reason,
        evidence_urls=req.evidence_urls,
        claimed_refund=req.claimed_refund,
        status=DisputeStatus.PENDING,
        deadline=datetime.utcnow() + timedelta(days=ESCROW_HOLD_DAYS),
    )
    db.add(dispute)

    # Freeze escrow
    escrow_res = await db.execute(select(Escrow).where(Escrow.bounty_id == req.bounty_id))
    escrow = escrow_res.scalar_one_or_none()
    if escrow:
        escrow.status = EscrowStatus.DISPUTED

    bounty.status = BountyStatus.DISPUTED
    await db.commit()

    return {
        "success": True,
        "dispute_id": dispute.id,
        "status": "pending",
        "message": "이의신청이 접수되었습니다. 마스터급 전문가 3인이 중재합니다.",
        "process": [
            "1. 이의신청 접수 및 에스크로 동결",
            "2. 마스터급 전문가 3인 무작위 배정",
            "3. 각 중재자 독립적 검토 (3일 이내)",
            "4. 다수결 판정 → 에스크로 처리",
            "5. 패소측 페널티 포인트 부과",
        ],
        "deadline": dispute.deadline.isoformat(),
    }


@router.get("/fee-calculator", summary="수수료 계산기")
async def fee_calculator(
    reward_krw: int = 10000,
    reward_points: int = 0,
):
    """현상금 수수료 및 전문가 실수령액 계산"""
    dist = calculate_escrow_distribution(reward_krw, reward_points)
    return {
        "input": {"reward_krw": reward_krw, "reward_points": reward_points},
        "distribution": dist,
        "legal_notes": {
            "platform_fee": "전자상거래법에 따른 플랫폼 서비스 이용료 15%",
            "insurance_contribution": "상호부조 보험 적립금 5%",
            "tax_withholding": "소득세법 제21조 기타소득 원천징수 3.3%",
            "payment_timing": "채택 후 7일 이의신청 기간 경과 시 자동 정산",
        }
    }
