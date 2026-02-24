"""
Dr. Vroom — 포인트 기반 등급 API (Global-Friendly Grade System)
══════════════════════════════════════════════════════════════

설계 원칙:
  - 구독료 = 포인트 (현금 없음)
  - 전 세계 동일 등급 체계 (환율 불필요)
  - ISO 국가코드만 기록 (현지화는 앱에서)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.db.database import get_db, User
from app.db.points_schema import (
    UserGradeV2, ExpertProfileV2, GradeHistoryV2,
    PointWallet, UserTier, ExpertTier, PointTxType
)
from app.services.points_service import (
    PointsService, SUBSCRIPTION_COST, FREE_DIAGNOSES_PER_MONTH,
    BOUNTY_MAX_POINTS
)
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/v1/grades", tags=["grades"])


# ── 등급 정의 (언어 독립적) ────────────────────────────────────
USER_TIERS = {
    UserTier.FREE: {
        "label": "Free",
        "label_ko": "무료",
        "emoji": "🌱",
        "monthly_pts": 0,
        "monthly_diagnoses": 3,
        "bounty_max_pts": 500,
        "features": ["월 3회 진단", "기본 보험 자동 포함", "소리 기여 포인트 적립"],
    },
    UserTier.STARTER: {
        "label": "Starter",
        "label_ko": "스타터",
        "emoji": "🚗",
        "monthly_pts": 300,
        "monthly_diagnoses": 30,
        "bounty_max_pts": 2000,
        "features": ["월 30회 진단", "현상금 게시 가능 (최대 2,000P)", "표준 보험 가입 가능"],
    },
    UserTier.PRO: {
        "label": "Pro",
        "label_ko": "프로",
        "emoji": "⭐",
        "monthly_pts": 900,
        "monthly_diagnoses": -1,
        "bounty_max_pts": 10000,
        "features": ["무제한 진단", "고급 FFT 분석 리포트", "현상금 최대 10,000P", "프리미엄 보험 가입 가능"],
    },
    UserTier.EXPERT: {
        "label": "Expert",
        "label_ko": "전문가",
        "emoji": "💎",
        "monthly_pts": 2700,
        "monthly_diagnoses": -1,
        "bounty_max_pts": 50000,
        "features": ["무제한+우선처리 진단", "전문가 직접 연결", "현상금 최대 50,000P", "전용 지원"],
    },
}

EXPERT_TIERS = {
    ExpertTier.APPRENTICE: {
        "label": "Apprentice",
        "label_ko": "견습 정비사",
        "emoji": "🔩",
        "platform_fee": 0.20,
        "min_adopted": 0,
        "min_rating": 0.0,
        "requires_license": False,
        "features": ["기본 현상금 답변", "플랫폼 수수료 20%", "자격증 불필요"],
    },
    ExpertTier.CERTIFIED: {
        "label": "Certified",
        "label_ko": "인증 정비사",
        "emoji": "🔧",
        "platform_fee": 0.15,
        "min_adopted": 10,
        "min_rating": 4.0,
        "requires_license": True,
        "features": ["수수료 15%", "인증 배지", "자격증 검증 필요", "우선 노출"],
    },
    ExpertTier.MASTER: {
        "label": "Master",
        "label_ko": "마스터 정비사",
        "emoji": "🏆",
        "platform_fee": 0.12,
        "min_adopted": 100,
        "min_rating": 4.5,
        "requires_license": True,
        "features": ["수수료 12%", "분쟁 중재자 자격", "마스터 배지", "채택 100건+"],
    },
    ExpertTier.PARTNER: {
        "label": "Partner",
        "label_ko": "닥터브릉이 파트너",
        "emoji": "👑",
        "platform_fee": 0.10,
        "min_adopted": 500,
        "min_rating": 4.8,
        "requires_license": True,
        "features": ["수수료 10%", "파트너 배지", "채택 500건+", "플랫폼 공동 성장"],
    },
}


# ── Pydantic ────────────────────────────────────────────────────

class UserGradeOut(BaseModel):
    user_id: str
    tier: str
    tier_label: str
    tier_ko: str
    emoji: str
    monthly_pts_cost: int
    monthly_diagnoses_limit: int
    monthly_diagnoses_used: int
    bounty_max_pts: int
    features: List[str]
    tier_expires: Optional[datetime]
    auto_renew: bool
    reputation_score: float
    sound_contributions: int
    total_diagnoses: int


class ExpertOut(BaseModel):
    user_id: str
    tier: str
    tier_label: str
    tier_ko: str
    emoji: str
    platform_fee_pct: str
    license_verified: bool
    specialties: List[str]
    vehicle_brands: List[str]
    country: str
    total_answers: int
    adopted_answers: int
    adoption_rate: float
    avg_rating: float
    total_earned_points: int
    is_active: bool
    next_tier_requirements: Optional[dict]


class SubscribeReq(BaseModel):
    tier: str
    auto_renew: bool = True


class ExpertRegisterReq(BaseModel):
    license_type: Optional[str] = None
    license_number: Optional[str] = None
    specialties: List[str] = []
    vehicle_brands: List[str] = []
    experience_years: int = 0
    country: str = "KR"


# ── Helpers ─────────────────────────────────────────────────────

async def get_or_create_grade(user_id: str, db: AsyncSession) -> UserGradeV2:
    res = await db.execute(select(UserGradeV2).where(UserGradeV2.user_id == user_id))
    g = res.scalar_one_or_none()
    if not g:
        g = UserGradeV2(user_id=user_id)
        db.add(g)
        await db.flush()
    return g


async def record_change(user_id, grade_type, from_t, to_t, reason, pts, db):
    db.add(GradeHistoryV2(
        id=str(uuid.uuid4()),
        user_id=user_id,
        grade_type=grade_type,
        from_tier=from_t,
        to_tier=to_t,
        reason=reason,
        points_cost=pts,
    ))


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/tiers", summary="전체 등급 안내 (글로벌)")
async def get_tiers():
    """사용자·전문가 등급 체계 전체 정보 — 환율 없음, 포인트 전용"""
    return {
        "currency": "points",
        "legal_note": "포인트는 앱 내 가상 재화입니다. 현금이 아닙니다.",
        "user_tiers": [
            {"tier": t.value, **info}
            for t, info in USER_TIERS.items()
        ],
        "expert_tiers": [
            {"tier": t.value, **info}
            for t, info in EXPERT_TIERS.items()
        ],
        "earn_points": {
            "join_bonus": "50P (가입 시)",
            "daily_login": "5P (출석)",
            "sound_contribution": "10P (소리 제출)",
            "verification_bonus": "40P (검증 통과)",
            "referral": "50P (친구 초대)",
            "bounty_reward": "80% of bounty (채택 보상)",
        }
    }


@router.get("/my", response_model=UserGradeOut, summary="내 등급 조회")
async def get_my_grade(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    grade = await get_or_create_grade(current_user.id, db)
    tier = grade.tier or UserTier.FREE
    info = USER_TIERS[tier]
    limit = FREE_DIAGNOSES_PER_MONTH[tier]

    return UserGradeOut(
        user_id=current_user.id,
        tier=tier.value,
        tier_label=info["label"],
        tier_ko=info["label_ko"],
        emoji=info["emoji"],
        monthly_pts_cost=info["monthly_pts"],
        monthly_diagnoses_limit=limit,
        monthly_diagnoses_used=grade.monthly_diagnoses,
        bounty_max_pts=info["bounty_max_pts"],
        features=info["features"],
        tier_expires=grade.tier_expires,
        auto_renew=grade.auto_renew,
        reputation_score=grade.reputation_score,
        sound_contributions=grade.sound_contributions,
        total_diagnoses=grade.total_diagnoses,
    )


@router.post("/subscribe", summary="등급 구독 (포인트 차감)")
async def subscribe(
    req: SubscribeReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """포인트로 등급 구독 — 현금 없음, 환율 없음"""
    try:
        new_tier = UserTier(req.tier)
    except ValueError:
        raise HTTPException(400, f"유효하지 않은 등급: {req.tier}")

    grade = await get_or_create_grade(current_user.id, db)
    old_tier = grade.tier or UserTier.FREE
    cost = SUBSCRIPTION_COST[new_tier]
    info = USER_TIERS[new_tier]

    # 무료 다운그레이드
    if new_tier == UserTier.FREE:
        old_t = old_tier.value
        grade.tier = UserTier.FREE
        grade.tier_expires = None
        grade.auto_renew = False
        await record_change(current_user.id, "user", old_t, "free", "구독 취소", 0, db)
        await db.commit()
        return {"success": True, "tier": "free", "message": "🌱 무료 등급으로 변경되었습니다."}

    # 포인트 차감
    try:
        await PointsService.spend(
            db=db,
            user_id=current_user.id,
            amount=cost,
            tx_type=PointTxType.SUBSCRIPTION,
            ref_type="grade_subscribe",
            description=f"{info['emoji']} {info['label']} 등급 구독 {cost}P",
        )
    except ValueError as e:
        raise HTTPException(402, str(e))

    grade.tier = new_tier
    grade.tier_expires = datetime.utcnow() + timedelta(days=30)
    grade.auto_renew = req.auto_renew
    grade.tier_since = datetime.utcnow()

    await record_change(current_user.id, "user", old_tier.value, new_tier.value,
                        f"포인트 구독: {cost}P", cost, db)
    await db.commit()

    return {
        "success": True,
        "tier": new_tier.value,
        "tier_ko": info["label_ko"],
        "emoji": info["emoji"],
        "points_spent": cost,
        "expires_at": grade.tier_expires.isoformat(),
        "features": info["features"],
        "message": f"{info['emoji']} {info['label_ko']} 구독 완료! {cost}P 사용됨.",
    }


# ── Expert ────────────────────────────────────────────────────────

@router.post("/expert/register", summary="전문가 등록 (글로벌)")
async def register_expert(
    req: ExpertRegisterReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ExpertProfileV2).where(ExpertProfileV2.user_id == current_user.id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "이미 전문가로 등록되어 있습니다.")

    profile = ExpertProfileV2(
        user_id=current_user.id,
        tier=ExpertTier.APPRENTICE,
        license_type=req.license_type,
        license_number=req.license_number,
        specialties=req.specialties,
        vehicle_brands=req.vehicle_brands,
        experience_years=req.experience_years,
        country=req.country,
    )
    db.add(profile)
    await db.execute(update(User).where(User.id == current_user.id).values(role="trainer"))
    await db.commit()

    info = EXPERT_TIERS[ExpertTier.APPRENTICE]
    return {
        "success": True,
        "tier": "apprentice",
        "emoji": info["emoji"],
        "message": f"🔩 견습 정비사로 등록되었습니다. ({req.country})",
        "next_tier": "certified",
        "next_requirements": EXPERT_TIERS[ExpertTier.CERTIFIED],
    }


@router.get("/expert/me", response_model=ExpertOut, summary="내 전문가 프로필")
async def my_expert(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(ExpertProfileV2).where(ExpertProfileV2.user_id == current_user.id)
    )
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "전문가 프로필 없음. 먼저 등록하세요.")

    tier = p.tier or ExpertTier.APPRENTICE
    info = EXPERT_TIERS[tier]
    tier_order = list(ExpertTier)
    idx = tier_order.index(tier)
    next_req = EXPERT_TIERS[tier_order[idx + 1]] if idx < len(tier_order) - 1 else None

    return ExpertOut(
        user_id=current_user.id,
        tier=tier.value,
        tier_label=info["label"],
        tier_ko=info["label_ko"],
        emoji=info["emoji"],
        platform_fee_pct=f"{info['platform_fee']*100:.0f}%",
        license_verified=p.license_verified,
        specialties=p.specialties or [],
        vehicle_brands=p.vehicle_brands or [],
        country=p.country or "KR",
        total_answers=p.total_answers,
        adopted_answers=p.adopted_answers,
        adoption_rate=p.adoption_rate,
        avg_rating=p.avg_rating,
        total_earned_points=p.total_earned_points,
        is_active=p.is_active,
        next_tier_requirements=next_req,
    )


@router.post("/expert/promote", summary="등급 승급 평가")
async def promote_expert(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(ExpertProfileV2).where(ExpertProfileV2.user_id == current_user.id)
    )
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "전문가 프로필 없음.")

    tier_order = list(ExpertTier)
    curr_idx = tier_order.index(p.tier or ExpertTier.APPRENTICE)
    if curr_idx >= len(tier_order) - 1:
        return {"eligible": False, "message": "최고 등급(파트너)입니다."}

    next_tier = tier_order[curr_idx + 1]
    req = EXPERT_TIERS[next_tier]

    ok_license = p.license_verified if req["requires_license"] else True
    ok_adopted = p.adopted_answers >= req["min_adopted"]
    ok_rating  = p.avg_rating >= req["min_rating"]

    if ok_license and ok_adopted and ok_rating:
        old_t = p.tier.value
        p.tier = next_tier
        p.tier_since = datetime.utcnow()
        await record_change(current_user.id, "expert", old_t, next_tier.value,
                            f"자동 승급: 채택 {p.adopted_answers}, 평점 {p.avg_rating:.1f}", 0, db)
        await db.commit()
        return {
            "promoted": True,
            "new_tier": next_tier.value,
            "emoji": req["emoji"],
            "message": f"{req['emoji']} {req['label_ko']}으로 승급!",
        }

    gaps = []
    if not ok_license: gaps.append("자격증 검증 필요")
    if not ok_adopted: gaps.append(f"채택 {req['min_adopted']}건 필요 (현재 {p.adopted_answers}건)")
    if not ok_rating:  gaps.append(f"평점 {req['min_rating']} 필요 (현재 {p.avg_rating:.1f})")
    return {"eligible": False, "missing": gaps}


@router.get("/history", summary="등급 변경 이력")
async def grade_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(GradeHistoryV2)
        .where(GradeHistoryV2.user_id == current_user.id)
        .order_by(GradeHistoryV2.changed_at.desc())
        .limit(30)
    )
    items = res.scalars().all()
    return [
        {
            "grade_type": h.grade_type,
            "from": h.from_tier,
            "to": h.to_tier,
            "reason": h.reason,
            "points_cost": h.points_cost,
            "changed_at": h.changed_at.isoformat(),
        }
        for h in items
    ]
