"""
Dr. Vroom — 등급 시스템 API
Grade System API: User Tiers & Expert Profiles

등급 구조:
  사용자: 새싹(무료) → 드라이버(990원) → 골드(2,990원) → 다이아(9,900원)
  전문가: 견습 → 인증 정비사 → 마스터 → 닥터브릉이 파트너
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.db.database import get_db, User
from app.db.ecosystem_schema import (
    UserGrade, ExpertProfile, GradeHistory,
    UserTier, ExpertTier, InsurancePolicy, InsuranceType
)
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/v1/grades", tags=["grades"])


# ── Pricing constants ──────────────────────────────────────────
SUBSCRIPTION_PRICES = {
    UserTier.SPROUT:  0,
    UserTier.DRIVER:  990,
    UserTier.GOLD:    2990,
    UserTier.DIAMOND: 9900,
}

MONTHLY_DIAGNOSIS_LIMITS = {
    UserTier.SPROUT:  3,
    UserTier.DRIVER:  30,
    UserTier.GOLD:    -1,   # unlimited
    UserTier.DIAMOND: -1,   # unlimited + priority
}

TIER_BENEFITS = {
    UserTier.SPROUT: {
        "ko": "새싹",
        "emoji": "🌱",
        "monthly_diagnoses": 3,
        "max_bounty_krw": 10000,
        "priority": False,
        "description": "무료 3회/월 진단, 기본 보험 자동 포함"
    },
    UserTier.DRIVER: {
        "ko": "드라이버",
        "emoji": "🚗",
        "monthly_diagnoses": 30,
        "max_bounty_krw": 50000,
        "priority": False,
        "description": "월 990원, 30회/월 진단, 현상금 게시 가능"
    },
    UserTier.GOLD: {
        "ko": "골드",
        "emoji": "⭐",
        "monthly_diagnoses": -1,
        "max_bounty_krw": 200000,
        "priority": False,
        "description": "월 2,990원, 무제한 진단, 고급 분석 보고서"
    },
    UserTier.DIAMOND: {
        "ko": "다이아",
        "emoji": "💎",
        "monthly_diagnoses": -1,
        "max_bounty_krw": 1000000,
        "priority": True,
        "description": "월 9,900원, 무제한+우선처리, 전문가 직접 연결"
    },
}

EXPERT_TIER_REQUIREMENTS = {
    ExpertTier.APPRENTICE: {
        "ko": "견습 정비사",
        "emoji": "🔩",
        "fee_rate": 0.20,           # 플랫폼 수수료 20%
        "max_answer_fee": 50000,
        "requires_license": False,
        "min_adopted": 0,
        "min_rating": 0.0,
    },
    ExpertTier.CERTIFIED: {
        "ko": "인증 정비사",
        "emoji": "🔧",
        "fee_rate": 0.15,           # 15%
        "max_answer_fee": 200000,
        "requires_license": True,
        "min_adopted": 10,
        "min_rating": 4.0,
    },
    ExpertTier.MASTER: {
        "ko": "마스터 정비사",
        "emoji": "🏆",
        "fee_rate": 0.12,           # 12%
        "max_answer_fee": 500000,
        "requires_license": True,
        "min_adopted": 100,
        "min_rating": 4.5,
    },
    ExpertTier.PARTNER: {
        "ko": "닥터브릉이 파트너",
        "emoji": "👑",
        "fee_rate": 0.10,           # 10%
        "max_answer_fee": 2000000,
        "requires_license": True,
        "min_adopted": 500,
        "min_rating": 4.8,
    },
}


# ── Pydantic Schemas ───────────────────────────────────────────

class UserGradeResponse(BaseModel):
    user_id: str
    tier: str
    tier_ko: str
    tier_emoji: str
    subscription_active: bool
    subscription_expires: Optional[datetime]
    monthly_diagnoses_used: int
    monthly_diagnoses_limit: int
    points: int
    lifetime_points: int
    reputation_score: float
    sound_contributions: int
    benefits: dict


class ExpertProfileCreate(BaseModel):
    license_type: str
    license_number: str
    business_name: Optional[str] = None
    business_reg_number: Optional[str] = None
    specialties: List[str] = []
    vehicle_brands: List[str] = []
    experience_years: int = 0
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    account_holder: Optional[str] = None


class ExpertProfileResponse(BaseModel):
    user_id: str
    tier: str
    tier_ko: str
    tier_emoji: str
    license_verified: bool
    business_verified: bool
    specialties: List[str]
    vehicle_brands: List[str]
    experience_years: int
    total_answers: int
    adopted_answers: int
    adoption_rate: float
    avg_rating: float
    total_earned_krw: int
    is_active: bool
    fee_rate: float
    requirements: dict


class SubscribeRequest(BaseModel):
    tier: str
    auto_renew: bool = True


class GradeHistoryItem(BaseModel):
    grade_type: str
    from_tier: str
    to_tier: str
    reason: str
    changed_at: datetime


# ── Helpers ────────────────────────────────────────────────────

async def get_or_create_user_grade(user_id: str, db: AsyncSession) -> UserGrade:
    result = await db.execute(select(UserGrade).where(UserGrade.user_id == user_id))
    grade = result.scalar_one_or_none()
    if not grade:
        grade = UserGrade(user_id=user_id)
        db.add(grade)
        await db.flush()
    return grade


async def record_grade_change(
    user_id: str,
    grade_type: str,
    from_tier: str,
    to_tier: str,
    reason: str,
    db: AsyncSession,
    changed_by: str = "system"
):
    history = GradeHistory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        grade_type=grade_type,
        from_tier=from_tier,
        to_tier=to_tier,
        reason=reason,
        changed_by=changed_by,
    )
    db.add(history)


# ── Endpoints ──────────────────────────────────────────────────

@router.get("/my", response_model=UserGradeResponse, summary="내 등급 조회")
async def get_my_grade(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 로그인 사용자의 등급, 혜택, 포인트 정보 반환"""
    grade = await get_or_create_user_grade(current_user.id, db)
    tier = grade.tier or UserTier.SPROUT
    limits = MONTHLY_DIAGNOSIS_LIMITS[tier]
    benefits = TIER_BENEFITS[tier]

    return UserGradeResponse(
        user_id=current_user.id,
        tier=tier.value,
        tier_ko=benefits["ko"],
        tier_emoji=benefits["emoji"],
        subscription_active=grade.subscription_active,
        subscription_expires=grade.subscription_expires,
        monthly_diagnoses_used=grade.monthly_diagnoses,
        monthly_diagnoses_limit=limits,
        points=grade.points,
        lifetime_points=grade.lifetime_points,
        reputation_score=grade.reputation_score,
        sound_contributions=grade.sound_contributions,
        benefits=benefits,
    )


@router.post("/subscribe", summary="구독 업그레이드")
async def subscribe(
    req: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """사용자 등급 구독 (실제 결제는 PG 연동 필요 — 현재 시뮬레이션)"""
    try:
        new_tier = UserTier(req.tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 등급: {req.tier}")

    grade = await get_or_create_user_grade(current_user.id, db)
    old_tier = grade.tier or UserTier.SPROUT
    price = SUBSCRIPTION_PRICES[new_tier]

    # Downgrade to free
    if new_tier == UserTier.SPROUT:
        await record_grade_change(
            current_user.id, "user",
            old_tier.value, new_tier.value,
            "구독 취소 (무료 전환)", db
        )
        grade.tier = new_tier
        grade.subscription_active = False
        grade.subscription_expires = None
        grade.auto_renew = False
        await db.commit()
        return {"success": True, "message": "무료 새싹으로 변경되었습니다", "tier": new_tier.value}

    # Upgrade
    grade.tier = new_tier
    grade.subscription_active = True
    grade.subscription_expires = datetime.utcnow() + timedelta(days=30)
    grade.auto_renew = req.auto_renew
    grade.tier_since = datetime.utcnow()

    await record_grade_change(
        current_user.id, "user",
        old_tier.value, new_tier.value,
        f"구독 업그레이드 — 월 {price:,}원", db
    )
    await db.commit()

    return {
        "success": True,
        "message": f"{TIER_BENEFITS[new_tier]['emoji']} {TIER_BENEFITS[new_tier]['ko']}으로 업그레이드!",
        "tier": new_tier.value,
        "monthly_price_krw": price,
        "expires_at": grade.subscription_expires.isoformat(),
        "benefits": TIER_BENEFITS[new_tier],
        "note": "실제 결제 연동 전 테스트 모드입니다."
    }


@router.get("/tiers", summary="전체 등급 안내")
async def get_all_tiers():
    """사용자·전문가 등급 체계 전체 정보 반환"""
    return {
        "user_tiers": [
            {
                "tier": tier.value,
                "price_krw": SUBSCRIPTION_PRICES[tier],
                **TIER_BENEFITS[tier],
            }
            for tier in UserTier
        ],
        "expert_tiers": [
            {
                "tier": tier.value,
                **EXPERT_TIER_REQUIREMENTS[tier],
            }
            for tier in ExpertTier
        ]
    }


# ── Expert Profile ─────────────────────────────────────────────

@router.get("/expert/me", response_model=ExpertProfileResponse, summary="내 전문가 프로필")
async def get_my_expert_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """전문가 프로필 및 현재 등급 조회"""
    result = await db.execute(
        select(ExpertProfile).where(ExpertProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="전문가 프로필이 없습니다. 먼저 등록해 주세요.")

    tier = profile.tier or ExpertTier.APPRENTICE
    req = EXPERT_TIER_REQUIREMENTS[tier]

    return ExpertProfileResponse(
        user_id=current_user.id,
        tier=tier.value,
        tier_ko=req["ko"],
        tier_emoji=req["emoji"],
        license_verified=profile.license_verified,
        business_verified=profile.business_verified,
        specialties=profile.specialties or [],
        vehicle_brands=profile.vehicle_brands or [],
        experience_years=profile.experience_years,
        total_answers=profile.total_answers,
        adopted_answers=profile.adopted_answers,
        adoption_rate=profile.adoption_rate,
        avg_rating=profile.avg_rating,
        total_earned_krw=profile.total_earned_krw,
        is_active=profile.is_active,
        fee_rate=req["fee_rate"],
        requirements=req,
    )


@router.post("/expert/register", summary="전문가 등록 신청")
async def register_expert(
    req: ExpertProfileCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """전문가 프로필 등록 (자격증 검증은 관리자 수동 처리)"""
    existing = await db.execute(
        select(ExpertProfile).where(ExpertProfile.user_id == current_user.id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 등록된 전문가 프로필이 있습니다.")

    profile = ExpertProfile(
        user_id=current_user.id,
        tier=ExpertTier.APPRENTICE,
        license_type=req.license_type,
        license_number=req.license_number,
        business_name=req.business_name,
        business_reg_number=req.business_reg_number,
        specialties=req.specialties,
        vehicle_brands=req.vehicle_brands,
        experience_years=req.experience_years,
        bank_name=req.bank_name,
        bank_account=req.bank_account,
        account_holder=req.account_holder,
    )
    db.add(profile)

    # Update user role
    await db.execute(
        update(User).where(User.id == current_user.id).values(role="trainer")
    )

    await db.commit()
    return {
        "success": True,
        "message": "🔩 견습 정비사로 등록되었습니다. 자격증 검증 후 인증 정비사로 승급됩니다.",
        "tier": ExpertTier.APPRENTICE.value,
        "next_tier": ExpertTier.CERTIFIED.value,
        "requirements": EXPERT_TIER_REQUIREMENTS[ExpertTier.CERTIFIED],
        "review_note": "자격증 검증은 영업일 3일 이내에 처리됩니다."
    }


@router.post("/expert/promote", summary="전문가 등급 승급 평가 (시스템)")
async def auto_promote_expert(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 성과 기반 등급 승급 가능 여부 평가 및 자동 승급"""
    result = await db.execute(
        select(ExpertProfile).where(ExpertProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="전문가 프로필이 없습니다.")

    current_tier = profile.tier or ExpertTier.APPRENTICE
    tier_order = [ExpertTier.APPRENTICE, ExpertTier.CERTIFIED, ExpertTier.MASTER, ExpertTier.PARTNER]
    current_idx = tier_order.index(current_tier)

    if current_idx >= len(tier_order) - 1:
        return {"eligible": False, "message": "이미 최고 등급(파트너)입니다.", "tier": current_tier.value}

    next_tier = tier_order[current_idx + 1]
    next_req = EXPERT_TIER_REQUIREMENTS[next_tier]

    # Check requirements
    meets_license = profile.license_verified if next_req["requires_license"] else True
    meets_adopted = profile.adopted_answers >= next_req["min_adopted"]
    meets_rating = profile.avg_rating >= next_req["min_rating"]

    if meets_license and meets_adopted and meets_rating:
        old_tier = current_tier.value
        profile.tier = next_tier
        profile.tier_since = datetime.utcnow()
        await record_grade_change(
            current_user.id, "expert",
            old_tier, next_tier.value,
            f"자동 승급: 채택 {profile.adopted_answers}건, 평점 {profile.avg_rating:.1f}", db
        )
        await db.commit()
        req_info = EXPERT_TIER_REQUIREMENTS[next_tier]
        return {
            "eligible": True,
            "promoted": True,
            "old_tier": old_tier,
            "new_tier": next_tier.value,
            "new_tier_ko": req_info["ko"],
            "emoji": req_info["emoji"],
            "message": f"{req_info['emoji']} 축하합니다! {req_info['ko']}로 승급되었습니다!"
        }
    else:
        gaps = []
        if not meets_license:
            gaps.append("자격증 검증 필요")
        if not meets_adopted:
            gaps.append(f"채택 답변 {next_req['min_adopted']}건 필요 (현재 {profile.adopted_answers}건)")
        if not meets_rating:
            gaps.append(f"평점 {next_req['min_rating']} 필요 (현재 {profile.avg_rating:.1f})")

        return {
            "eligible": False,
            "promoted": False,
            "current_tier": current_tier.value,
            "next_tier": next_tier.value,
            "missing_requirements": gaps,
            "message": "승급 조건을 아직 충족하지 못했습니다.",
        }


@router.get("/history", response_model=List[GradeHistoryItem], summary="등급 변경 이력")
async def get_grade_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 사용자의 등급 변경 이력 조회"""
    result = await db.execute(
        select(GradeHistory)
        .where(GradeHistory.user_id == current_user.id)
        .order_by(GradeHistory.changed_at.desc())
        .limit(50)
    )
    items = result.scalars().all()
    return [
        GradeHistoryItem(
            grade_type=h.grade_type,
            from_tier=h.from_tier,
            to_tier=h.to_tier,
            reason=h.reason,
            changed_at=h.changed_at,
        )
        for h in items
    ]
